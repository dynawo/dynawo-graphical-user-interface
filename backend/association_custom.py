#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import glob
import json
import os
import re
import threading
import xml.etree.ElementTree as ET

# Models added by the user: libraries of the selected Dynawo's ddb unknown to
# PowSyBl, declared to it through powsybl-dynawo's additional models file.
# A library is offered for a category when its .desc.xml declares what
# PowSyBl uses for that category (connected variables, values it reads),
# learnt by generating a built-in model of the category with the fake Dynawo.
# An automation system is generated on an element given the equipment model
# it connects to, whose own variables and values are left out.

_NS = "{http://www.rte-france.com/dynawo}"

# Categories whose interface cannot be probed on a single element: their
# models make PowSyBl require a model on every branch, or every bus.
UNSUPPORTED_CATEGORIES = {"Line", "Transformer", "Bus", "InfiniteBus"}

SHARED_PATH = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "association_custom_models.json")

_PROBE_PREFIX = "__PROBE__"
_MISSING_PARAMETER = re.compile(r"Parameter (\S+) not found in set (\S+)")
_INTERNAL_CATEGORY = re.compile(rf"Model {_PROBE_PREFIX}(\w+) not found for (\w+)")

_shared_lock = threading.Lock()


def numeric_version(version: str) -> str:
    """"1.7.1 (rev:v1.7-1250b93)" -> "1.7.1", the form of PowSyBl's version ranges."""
    match = re.match(r"\s*(\d+(?:\.\d+)*)", version)
    return match.group(1) if match else version.strip()


def next_patch(version: str) -> str:
    """"1.7.1" -> "1.7.2"."""
    parts = version.split(".")
    return ".".join(parts[:-1] + [str(int(parts[-1]) + 1)])


# ── Worker side ───────────────────────────────────────────────────────────────

def _probe_element(network, element_type: dict, category: str) -> str | None:
    """An element of the probe network to put a model of the category on."""
    from backend.association_rules import element_table

    if element_type["id"] == "HVDC_LINE":
        lines = network.get_hvdc_lines(all_attributes=True)
        vsc = set(network.get_vsc_converter_stations().index)
        wanted = [i for i, r in lines.iterrows() if (r["converter_station1_id"] in vsc) == (category == "HvdcVsc")]
        return wanted[0] if wanted else None
    ids = list(element_table(network, element_type)["id"])
    return ids[0] if ids else None


def probe_interfaces_in_worker(dynawo_version: str, categories: list[dict]) -> dict:
    """Worker entry point. For each {category, element_type, model}: what
    PowSyBl uses of a model of that category, by generating `model` on an
    element of pypowsybl's four-substation network with an empty parameter set.
    Automation systems also hold `automation`: {element_fields, fields (other
    field values), equipment: {category, model} or None}.

    Returns {category: {internal, variables, parameters} or {error}}; `internal`
    is PowSyBl's own category name, used in the additional models file."""
    import pypowsybl as pp
    import pypowsybl.dynamic as dyn
    import pypowsybl.report as rep

    from backend.association_generate import _cleanup_dynawo_run_dirs, _dynawo_run_dirs
    from backend.powsybl_config import worker_fake_dynawo_dir, worker_tmp_dir

    tmp_dir, fake_dir = worker_tmp_dir(), worker_fake_dynawo_dir()
    if not tmp_dir or not fake_dir:
        return {c["category"]: {"error": "not running in a pypowsybl worker"} for c in categories}
    with open(os.path.join(fake_dir, "version"), "w", encoding="utf-8") as fh:
        fh.write(dynawo_version + "\n")
    network = pp.network.create_four_substations_node_breaker_network()
    par_path = os.path.join(tmp_dir, "probe.par")

    def run(mapping, values: dict[str, list[str]]) -> tuple[str, str | None, bytes | None]:
        sets = "".join(f'<dyn:set id="{s}">'
                       + "".join(f'<dyn:par type="DOUBLE" name="{n}" value="1"/>' for n in values.get(s, []))
                       + "</dyn:set>" for s in ("P", "E"))
        with open(par_path, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?><dyn:parametersSet xmlns:dyn="http://www.rte-france.com/dynawo">'
                     f'{sets}<dyn:set id="N"/><dyn:set id="S"/></dyn:parametersSet>')
        report = rep.ReportNode()
        before = _dynawo_run_dirs(tmp_dir)
        error = None
        try:
            dyn.Simulation().run(network, mapping, parameters=dyn.Parameters(0, 1, {
                "parametersFile": par_path, "network.parametersFile": par_path, "network.parametersId": "N",
                "solver.type": "IDA", "solver.parametersFile": par_path, "solver.parametersId": "S",
            }), report_node=report)
        except Exception as exc:
            error = str(exc)
        new_dirs = _dynawo_run_dirs(tmp_dir) - before
        dyd = None
        for run_dir in new_dirs:
            path = os.path.join(run_dir, "powsybl_dynawo.dyd")
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    dyd = fh.read()
        _cleanup_dynawo_run_dirs(tmp_dir, new_dirs)
        return str(report), error, dyd

    def add(mapping, c: dict, element: str, model: str) -> str:
        """Add a model of the category on the element; returns its dynamic model id."""
        automation = c.get("automation")
        if automation is None:
            mapping.add_dynamic_model(category_name=c["category"], static_id=element,
                                      parameter_set_id="P", model_name=model)
            return element
        if automation["equipment"]:
            mapping.add_dynamic_model(category_name=automation["equipment"]["category"], static_id=element,
                                      parameter_set_id="E", model_name=automation["equipment"]["model"])
        dynamic_id = _PROBE_PREFIX + "AUTOMATION"
        mapping.add_dynamic_model(category_name=c["category"], dynamic_model_id=dynamic_id, parameter_set_id="P",
                                  model_name=model, **{f: element for f in automation["element_fields"]},
                                  **automation["fields"])
        return dynamic_id

    results: dict[str, dict] = {}
    # PowSyBl's internal category names: its error for an unknown model names them.
    internal = {}
    for c in categories:
        element = _probe_element(network, c["element_type"], c["category"])
        if element:
            mapping = dyn.ModelMapping()
            add(mapping, c, element, _PROBE_PREFIX + c["category"])
            report, _, _ = run(mapping, {})
            internal.update(_INTERNAL_CATEGORY.findall(report))

    for c in categories:
        element = _probe_element(network, c["element_type"], c["category"])
        if not element:
            results[c["category"]] = {"error": "no element to probe"}
            continue
        values: dict[str, list[str]] = {}
        for _ in range(10):
            mapping = dyn.ModelMapping()
            model_id = add(mapping, c, element, c["model"])
            _, error, dyd = run(mapping, values)
            missing = _MISSING_PARAMETER.search(error or "")
            if not missing or missing.group(2) not in ("P", "E"):
                break
            values.setdefault(missing.group(2), []).append(missing.group(1))
        if error or dyd is None:
            results[c["category"]] = {"error": error or "nothing written"}
            continue
        results[c["category"]] = {
            "internal":   internal.get(c["category"]),
            "variables":  sorted(_model_variables(dyd, model_id)),
            "parameters": values.get("P", []),
        }
    return results


def _model_variables(dyd: bytes, model_id: str) -> set[str]:
    """Variables of a model PowSyBl uses in a .dyd: its side of each macro
    connection, and its static references."""
    root = ET.fromstring(dyd)
    model = next((m for m in root.iter(_NS + "blackBoxModel") if m.get("id") == model_id), None)
    references = {r.get("id") for r in model.iter(_NS + "macroStaticRef")} if model is not None else set()
    connectors = {mc.get("id"): mc for mc in root.iter(_NS + "macroConnector")}
    variables = set()
    for connect in root.iter(_NS + "macroConnect"):
        connector = connectors.get(connect.get("connector"))
        if connector is None:
            continue
        for side, var in (("id1", "var1"), ("id2", "var2")):
            if connect.get(side) == model_id:
                variables |= {c.get(var) for c in connector.iter(_NS + "connect") if "@" not in c.get(var, "@")}
    for reference in root.iter(_NS + "macroStaticReference"):
        if reference.get("id") in references:
            variables |= {r.get("var") for r in reference.iter(_NS + "staticRef") if r.get("var")}
    if model is not None:
        variables |= {r.get("var") for r in model.findall(_NS + "staticRef") if r.get("var")}
    return variables


# ── API side ──────────────────────────────────────────────────────────────────

def ddb_libraries(exe: str) -> list[str]:
    """Libraries of a Dynawo's ddb (those with a .desc.xml)."""
    ddb = os.path.join(os.path.dirname(exe), "ddb")
    return sorted(os.path.basename(p)[: -len(".desc.xml")] for p in glob.glob(os.path.join(ddb, "*.desc.xml")))


def declares(names: list[str], required: list[str]) -> bool:
    """Whether a .desc.xml declares each required variable: as is, or expanded
    by Dynawo (`load_terminal` -> `load_terminal_V_re`…, a signal -> `…_value`)."""
    return all(r in names or any(n.startswith(r + "_") for n in names) for r in required)


def fits(symbols: tuple[list[str], list[str]], interface: dict) -> bool:
    variables, parameters = symbols
    return declares(variables, interface["variables"]) and set(interface["parameters"]) <= set(parameters)


def candidates(category: str, interfaces: dict, libraries: dict[str, tuple[list[str], list[str]]]) -> list[str]:
    """Libraries fitting `category` and no category with a strictly larger
    interface (a SignalN generator fits the simplified generators too, but
    needs its generator_N connected)."""
    interface = interfaces.get(category)
    if not interface or "error" in interface:
        return []
    required = set(interface["variables"])
    larger = [i for c, i in interfaces.items()
              if c != category and "error" not in i and required < set(i["variables"])]
    return [lib for lib, symbols in libraries.items()
            if fits(symbols, interface) and not any(fits(symbols, i) for i in larger)]


def load_shared() -> list[dict]:
    try:
        with open(SHARED_PATH, encoding="utf-8") as fh:
            return json.load(fh).get("models", [])
    except (OSError, ValueError):
        return []


def save_shared(models: list[dict]) -> None:
    with _shared_lock:
        os.makedirs(os.path.dirname(SHARED_PATH), exist_ok=True)
        tmp = SHARED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"models": models}, fh, indent=2)
        os.replace(tmp, SHARED_PATH)


def with_custom_models(catalogue: list[dict], models: list[dict]) -> list[dict]:
    """The catalogue with the added models in their categories (flag `custom`)."""
    added: dict[str, list[dict]] = {}
    for m in models:
        added.setdefault(m["category"], []).append(
            {"name": m["lib"], "description": m.get("doc", ""), "custom": "shared" if m.get("shared") else "live"})
    return [{**c, "models": c["models"] + sorted(added[c["name"]], key=lambda m: m["name"])}
            if c["name"] in added else c for c in catalogue]


def additional_models_file(models: list[dict]) -> dict:
    """powsybl-dynawo's additional models content for these models, keyed by
    PowSyBl's internal category names."""
    content: dict[str, dict] = {}
    for m in models:
        content.setdefault(m["internal"], {"libs": []})["libs"].append({
            "lib": m["lib"], "doc": m.get("doc", ""),
            # Only this Dynawo version: maxVersion is exclusive; PowSyBl's
            # report fails on a maxVersion without endCause.
            "minVersion": m["dynawo_version"], "maxVersion": next_patch(m["dynawo_version"]),
            "endCause": f"Added for Dynawo {m['dynawo_version']} only",
        })
    return content
