#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import os
import re
import xml.etree.ElementTree as ET

from backend.association_par import FILE, MANUAL
from backend.association_rules import (
    automation_fields,
    element_table,
    get_automation_system,
    load_automation_systems,
    load_element_types,
    model_library,
    resolve,
)

# Rules rebuilt from a .dyd: the models it puts on the elements of the loaded
# network become rules giving the same models and parameter sets. Models
# that rules cannot express are reported with the reason.

_NS = "{http://www.rte-france.com/dynawo}"
_ROOT = _NS + "dynamicModelsArchitecture"
NETWORK = "NETWORK"

# Libraries left out, with the reason.
_FREQUENCY_LIBS = {"DYNModelOmegaRef", "SignalN"}
_FREQUENCY = "frequency model: PowSyBl adds its own (its parameters and the generators it connects are not kept)"
_EVENT = "event: to define in the Events page"
_COMPOSITE = "composite model ({tag}): not supported"
_NO_STATIC_ID = "model on no network element (signal, automation system not supported yet…)"
_NOT_IN_NETWORK = "static id not in the loaded network"
_TYPE_NOT_COVERED = "element of a type rules do not cover"
_NO_ELEMENT = "automation system connected to no element it applies to"
_SEVERAL_ELEMENTS = "automation system on several elements: not supported yet"

_MAX_EXAMPLES = 5
_MAX_DIFFERENCES = 50


# ── Reading ───────────────────────────────────────────────────────────────────

def read_dyd(path: str) -> dict:
    """Top-level content of a .dyd, read as a stream:
    {models: {id: {lib, static_id, par_file, par_id}}, composites: [(tag, id)],
     connects: [(id1, var1, id2, var2)], macro_connectors: {id: [(var1, var2)]},
     macro_connects: [{connector, id1, id2, name1, name2, index1, index2}]}."""
    content = {"models": {}, "composites": [], "connects": [], "macro_connectors": {}, "macro_connects": []}
    depth = 0
    for event, el in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            depth += 1
            continue
        depth -= 1
        if depth != 1:
            continue
        tag = el.tag[len(_NS):] if el.tag.startswith(_NS) else el.tag
        get = el.get
        if tag == "blackBoxModel":
            content["models"][get("id")] = {"lib": get("lib"), "static_id": get("staticId"),
                                            "par_file": get("parFile"), "par_id": get("parId")}
        elif tag in ("modelicaModel", "modelTemplateExpansion", "modelTemplate"):
            content["composites"].append((tag, get("id")))
        elif tag == "connect":
            content["connects"].append((get("id1"), get("var1"), get("id2"), get("var2")))
        elif tag == "macroConnector":
            content["macro_connectors"][get("id")] = [(c.get("var1"), c.get("var2"))
                                                      for c in el.iter(_NS + "connect")]
        elif tag == "macroConnect":
            content["macro_connects"].append({k: get(k) for k in
                                              ("connector", "id1", "id2", "name1", "name2", "index1", "index2")})
        el.clear()
    return content


def _connections_of(content: dict, model_ids: set[str]) -> dict[str, list[tuple[str, str, str]]]:
    """{model id: [(its variable, other model id, other variable)]} for these
    models; macro connections expanded (@NAME@, @INDEX@ of each side)."""
    found: dict[str, list[tuple[str, str, str]]] = {m: [] for m in model_ids}

    def add(id1, var1, id2, var2):
        if id1 in found:
            found[id1].append((var1, id2, var2))
        if id2 in found:
            found[id2].append((var2, id1, var1))

    for connect in content["connects"]:
        add(*connect)
    for mc in content["macro_connects"]:
        if mc["id1"] not in found and mc["id2"] not in found:
            continue
        for var1, var2 in content["macro_connectors"].get(mc["connector"], []):
            sub = lambda var, side: var.replace("@NAME@", mc[f"name{side}"] or "").replace("@INDEX@", mc[f"index{side}"] or "")
            add(mc["id1"], sub(var1, 1), mc["id2"], sub(var2, 2))
    return found


# ── Reconstruction ────────────────────────────────────────────────────────────

class _Report:
    def __init__(self):
        self.left_out: dict[str, list[str]] = {}

    def leave_out(self, reason: str, what: str) -> None:
        self.left_out.setdefault(reason, []).append(what)

    def summary(self) -> list[dict]:
        return [{"reason": r, "count": len(ids), "examples": ids[:_MAX_EXAMPLES]}
                for r, ids in sorted(self.left_out.items(), key=lambda x: -len(x[1]))]


def _par_file(par_file: str | None, dyd_name: str, session_pars: list[str]) -> str | None:
    """Session .par file a model's parFile names: relative to the .dyd, else
    the only one with that name; the parFile itself when none."""
    if not par_file:
        return None
    relative = os.path.normpath(os.path.join(os.path.dirname(dyd_name), par_file))
    if relative in session_pars:
        return relative
    same_name = [p for p in session_pars if os.path.basename(p) == os.path.basename(par_file)]
    return same_name[0] if len(same_name) == 1 else par_file


def _par(par_file: str | None, par_id: str | None) -> dict:
    """Parameter source of a rule for a model's parFile and parId."""
    if par_file and par_id:
        return {"par_source": FILE, "par_file": par_file, "parameter_set_id": par_id}
    return {"par_source": MANUAL, "par_file": "", "parameter_set_id": par_id or ""}


def _rule(element_type: str, ids: list[str] | None, model: str, par_file: str | None, par_id: str | None,
          fields: dict | None = None) -> dict:
    conditions = [] if ids is None else [{"attribute": "id", "operator": "in", "value": sorted(ids)}]
    return {"element_type": element_type, "conditions": conditions, "model": model,
            **_par(par_file, par_id), "fields": fields or {}}


def _describe(model_and_set: tuple | None) -> str:
    return f"{model_and_set[0]} (set {model_and_set[2] or 'none'})" if model_and_set else "no model"


def _element_of(variable: str, ids: set[str]) -> tuple[str, str] | None:
    """(element id, rest) for a network variable `<element id>_<rest>`, the
    longest element id matching."""
    for i in [m.start() for m in re.finditer("_", variable)][::-1]:
        if variable[:i] in ids:
            return variable[:i], variable[i + 1:]
    return None


def reconstruct(network, dyd_path: str, dyd_name: str, catalogue: list[dict], session_pars: list[str],
                tables: dict | None = None) -> dict:
    """Rules giving the models of a .dyd on the loaded network, and a report:
    {rules, report: {equipment, automation_systems, left_out: [{reason, count,
    examples}], unknown_models, missing_par_files, differences}}."""
    if tables is None:
        tables = {}
    content = read_dyd(dyd_path)
    report = _Report()

    def table(type_entry: dict):
        if type_entry["id"] not in tables:
            tables[type_entry["id"]] = element_table(network, type_entry)
        return tables[type_entry["id"]]

    types = load_element_types()
    type_of: dict[str, str] = {}
    for type_entry in types:
        for i in table(type_entry)["id"]:
            type_of.setdefault(i, type_entry["id"])
    known_models = {m["name"] for c in catalogue for m in c["models"]}
    # Library -> (automation system, model name), built-in and added models.
    automation_libs: dict[str, tuple[dict, str]] = {}
    by_name = {c["name"]: c for c in catalogue}
    for automation in load_automation_systems():
        for model in by_name.get(automation["category"], {}).get("models", []):
            automation_libs[model_library(model["name"], automation["category"])] = (automation, model["name"])

    # Equipment: (type, model, par file, par id) -> element ids.
    equipment: dict[tuple, list[str]] = {}
    automation_candidates: dict[str, dict] = {}
    for model_id, m in content["models"].items():
        lib = m["lib"] or ""
        if lib in _FREQUENCY_LIBS:
            report.leave_out(_FREQUENCY, model_id)
        elif lib.startswith("Event") or lib == "NodeFault":
            report.leave_out(_EVENT, model_id)
        elif m["static_id"]:
            type_id = type_of.get(m["static_id"])
            if type_id is None:
                report.leave_out(_NOT_IN_NETWORK, m["static_id"])
                continue
            key = (type_id, lib, _par_file(m["par_file"], dyd_name, session_pars), m["par_id"])
            equipment.setdefault(key, []).append(m["static_id"])
        elif lib in automation_libs:
            automation_candidates[model_id] = m
        else:
            report.leave_out(_NO_STATIC_ID, f"{model_id} ({lib})")
    for tag, model_id in content["composites"]:
        report.leave_out(_COMPOSITE.format(tag=tag), model_id)

    # Automation systems: element and fields from their connections.
    static_of = {i: m["static_id"] for i, m in content["models"].items() if m["static_id"]}
    connections = _connections_of(content, set(automation_candidates))
    automation: dict[tuple, list[str]] = {}
    expected_automation: set[tuple] = set()
    for model_id, m in automation_candidates.items():
        system, model = automation_libs[m["lib"]]
        allowed = {i for i, t in type_of.items() if t in system["element_types"]}
        # (element id, variable without the element id prefix for the network)
        links = []
        for _, other, other_var in connections[model_id]:
            if other == NETWORK:
                found = _element_of(other_var or "", allowed)
                if found:
                    links.append(found)
            elif static_of.get(other) in allowed:
                links.append((static_of[other], other_var or ""))
        hints = system.get("reconstruct", {})
        roles = hints.get("roles") or {f: "" for f in system["element_fields"]}
        elements = {e for role, pattern in roles.items() for e, rest in links if re.search(pattern, rest)}
        if not elements:
            report.leave_out(_NO_ELEMENT, f"{model_id} ({m['lib']})")
            continue
        if len(elements) > 1:
            report.leave_out(_SEVERAL_ELEMENTS, f"{model_id} ({m['lib']}: {', '.join(sorted(elements))})")
            continue
        element = elements.pop()
        given = {}
        for field, patterns in hints.get("fields", {}).items():
            given[field] = next((value for pattern, value in patterns for _, rest in links if re.search(pattern, rest)), "")
        fields, _ = automation_fields(system, given)
        par_file = _par_file(m["par_file"], dyd_name, session_pars)
        key = (type_of[element], model, par_file, m["par_id"], tuple(sorted(fields.items())))
        automation.setdefault(key, []).append(element)
        expected_automation.add((model, element, tuple(sorted(fields.items()))))

    # Rules: per element type, the largest group without condition when every
    # element of the type has a model and it holds several, then the other
    # groups by id.
    rules = []
    for type_entry in types:
        groups = sorted(((k, ids) for k, ids in equipment.items() if k[0] == type_entry["id"]), key=lambda g: -len(g[1]))
        covered = sum(len(ids) for _, ids in groups)
        for n, ((type_id, lib, par_file, par_id), ids) in enumerate(groups):
            every = n == 0 and len(ids) > 1 and covered == len(table(type_entry))
            rules.append(_rule(type_id, None if every else ids, lib, par_file, par_id))
        for (type_id, model, par_file, par_id, fields), ids in automation.items():
            if type_id == type_entry["id"]:
                every = len(ids) == len(table(type_entry))
                rules.append(_rule(type_id, None if every else ids, model, par_file, par_id, dict(fields)))

    # Fidelity: the rules applied to the network against the .dyd; rules of
    # unknown models are left out by the resolution, and reported apart.
    unknown = sorted({r["model"] for r in rules if r["model"] not in known_models})
    resolution = resolve(network, rules, catalogue, tables=tables)
    got, got_automation = {}, set()
    for a in resolution["assignments"]:
        rule = rules[a["rule"]]
        if a.get("automation"):
            element_fields = get_automation_system(a["category"])["element_fields"]
            fields = tuple(sorted((k, v) for k, v in a["fields"].items() if k not in element_fields))
            got_automation.add((rule["model"], a["static_id"], fields))
        else:
            got[(a["element_type"], a["static_id"])] = (rule["model"], rule["par_file"], rule["parameter_set_id"])
    differences = []
    for (type_id, lib, par_file, par_id), ids in equipment.items():
        par = _par(par_file, par_id)
        wanted = (lib, par["par_file"], par["parameter_set_id"])
        for i in ids:
            if lib not in unknown and got.get((type_id, i)) != wanted:
                differences.append(f"{i}: {_describe(got.get((type_id, i)))} instead of {_describe(wanted)}")
    for model, element, _ in expected_automation - got_automation:
        if model not in unknown:
            differences.append(f"{element}: automation system {model} not given by the rules")
    missing_pars = sorted({r["par_file"] for r in rules if r["par_source"] == FILE and r["par_file"] not in session_pars})

    return {
        "rules": rules,
        "report": {
            "equipment":          sum(len(ids) for ids in equipment.values()),
            "automation_systems": sum(len(ids) for ids in automation.values()),
            "left_out":           report.summary(),
            "unknown_models":     unknown,
            "missing_par_files":  missing_pars,
            "differences":        differences[:_MAX_DIFFERENCES],
            "difference_count":   len(differences),
        },
    }
