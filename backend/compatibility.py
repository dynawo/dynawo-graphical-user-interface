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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from backend.desc_parser import get_lib_symbols
from backend.jobs_parser import read_all_file_refs, read_network_par, read_solver_info

# Compatibility of a job's input files with a Dynawo version, without running
# it: a static check against the Dynawo's ddb, and Dynawo's own migration
# tool (update-xml) to tell, and apply, the changes between two versions.

_NS = "http://www.rte-france.com/dynawo"
_FILE_ATTRIBUTES = ("iidmFile", "parFile", "dydFile", "inputFile")
_EXAMPLES = 5

# (exe, lib) -> (variables, parameters) of its ddb description, None if absent.
_desc_cache: dict[tuple[str, str], tuple[set[str], set[str]] | None] = {}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


# ── Static check ──────────────────────────────────────────────────────────────

def scan_dyd(path: str) -> dict:
    """What a .dyd uses, read in one streaming pass: models (id -> lib,
    parFile, parId), connectors, connections and static references."""
    models: dict[str, tuple[str, str, str]] = {}
    connectors: dict[str, list[tuple[str, str]]] = {}
    macro_connects: set[tuple] = set()
    connects: set[tuple[str, str, str, str]] = set()
    static_refs: set[tuple[str, str]] = set()          # (model id, variable)
    model_macro_refs: list[tuple[str, str]] = []       # (model id, macroStaticReference id)
    macro_static_vars: dict[str, list[str]] = {}
    modelica_models = 0
    stack: list[str] = []
    current_model = current_connector = current_reference = None
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = _local(el.tag)
        if event == "start":
            stack.append(tag)
            if tag == "blackBoxModel":
                current_model = el.get("id")
            elif tag == "macroConnector":
                current_connector = connectors.setdefault(el.get("id"), [])
            elif tag == "macroStaticReference":
                current_reference = macro_static_vars.setdefault(el.get("id"), [])
            elif tag == "modelicaModel":
                modelica_models += 1
            continue
        stack.pop()
        parent = stack[-1] if stack else None
        if tag == "blackBoxModel":
            models[el.get("id")] = (el.get("lib", ""), el.get("parFile", ""), el.get("parId", ""))
            current_model = None
        elif tag == "macroStaticRef" and parent == "blackBoxModel":
            model_macro_refs.append((current_model, el.get("id")))
        elif tag == "staticRef":
            if parent == "blackBoxModel":
                static_refs.add((current_model, el.get("var")))
            elif parent == "macroStaticReference" and current_reference is not None:
                current_reference.append(el.get("var"))
        elif tag == "connect":
            if parent == "macroConnector" and current_connector is not None:
                current_connector.append((el.get("var1"), el.get("var2")))
            elif parent == "dynamicModelsArchitecture":
                connects.add((el.get("id1"), el.get("var1"), el.get("id2"), el.get("var2")))
        elif tag == "macroConnector":
            current_connector = None
        elif tag == "macroStaticReference":
            current_reference = None
        elif tag == "macroConnect":
            macro_connects.add(tuple(el.get(a) for a in ("connector", "id1", "index1", "name1", "id2", "index2", "name2")))
        if parent in ("dynamicModelsArchitecture", None):
            el.clear()
    for model_id, ref in model_macro_refs:
        static_refs |= {(model_id, var) for var in macro_static_vars.get(ref, [])}
    return {"models": models, "connectors": connectors, "macro_connects": macro_connects,
            "connects": connects, "static_refs": static_refs, "modelica_models": modelica_models}


def scan_par_sets(path: str, wanted: set[str]) -> dict[str, set[str]]:
    """Parameter names of the wanted sets of a .par (macros expanded), in one
    streaming pass."""
    sets: dict[str, set[str]] = {}
    macro_of: dict[str, list[str]] = {}
    macros: dict[str, set[str]] = {}
    current: set[str] | None = None
    current_id = None
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = _local(el.tag)
        if event == "start":
            if tag == "set" and el.get("id") in wanted:
                current_id, current = el.get("id"), set()
            elif tag == "macroParameterSet":
                current_id, current = "macro:" + el.get("id", ""), set()
            continue
        if current is not None and tag in ("par", "parTable", "reference"):
            current.add(el.get("name"))
        elif current is not None and tag == "refPar":
            current.add(el.get("id") or el.get("name"))
        elif current is not None and tag == "macroParSet":
            macro_of.setdefault(current_id, []).append(el.get("id"))
        elif tag == "set" and current is not None and not current_id.startswith("macro:"):
            sets[current_id], current = current, None
        elif tag == "macroParameterSet" and current is not None:
            macros[current_id[len("macro:"):]], current = current, None
        if tag in ("set", "macroParameterSet"):
            el.clear()
    for set_id, refs in macro_of.items():
        if set_id in sets:
            for ref in refs:
                sets[set_id] |= macros.get(ref, set())
    return sets


def _desc(exe: str, lib: str) -> tuple[set[str], set[str]] | None:
    """(variables, parameters) of a library of the Dynawo's ddb, None if absent."""
    key = (exe, lib)
    if key not in _desc_cache:
        path = os.path.join(os.path.dirname(exe), "ddb", f"{lib}.desc.xml")
        variables, parameters = get_lib_symbols(exe, lib)
        _desc_cache[key] = (set(variables), set(parameters)) if os.path.isfile(path) else None
    return _desc_cache[key]


def _declared(variables: set[str], var: str) -> bool:
    """A connected variable is declared as is, or expanded by Dynawo
    (`x_terminal` -> `x_terminal_V_re`…, a signal -> `x_value` before 1.8)."""
    return var in variables or any(v.startswith(var + "_") for v in variables)


def _resolve(var: str, index: str | None, name: str | None) -> str | None:
    var = var.replace("@INDEX@", index or "").replace("@NAME@", name or "")
    return None if "@" in var else var


class _Issues:
    """Issues grouped by (level, message), each with a count and examples."""

    def __init__(self) -> None:
        self._groups: dict[tuple[str, str], dict] = {}

    def add(self, level: str, message: str, example: str | None = None) -> None:
        group = self._groups.setdefault((level, message), {"level": level, "message": message, "count": 0, "examples": []})
        group["count"] += 1
        if example and len(group["examples"]) < _EXAMPLES and example not in group["examples"]:
            group["examples"].append(example)

    def as_list(self) -> list[dict]:
        order = {"error": 0, "warning": 1, "info": 2}
        return sorted(self._groups.values(), key=lambda g: (order[g["level"]], -g["count"]))


def check_job(session_dir: str, jobs_name: str, exe: str) -> dict:
    """Static check of a job's files against a Dynawo's ddb and libraries.

    Errors: missing files, parameter sets or solver; libraries absent from the
    ddb; connected or referenced variables a library does not declare.
    Warnings: parameters a library does not declare. Variables and parameters
    of C++ models (DYNModel…) are not checked: their ddb description does not
    list what they connect."""
    issues = _Issues()
    jobs_path = os.path.join(session_dir, jobs_name)
    base = os.path.dirname(jobs_path)
    refs = read_all_file_refs(jobs_path)
    lib_dir = os.path.join(os.path.dirname(exe), "lib")

    solver = read_solver_info(jobs_path)
    if solver and solver.lib and not os.path.isfile(os.path.join(lib_dir, solver.lib + ".so")):
        issues.add("error", f"solver library '{solver.lib}' not found in this Dynawo")
    wanted_sets: dict[str, set[str]] = {}
    network_par, network_id = read_network_par(jobs_path)
    if network_par and network_id:
        wanted_sets.setdefault(network_par, set()).add(network_id)
    if solver and solver.par_file and solver.par_id:
        wanted_sets.setdefault(solver.par_file, set()).add(solver.par_id)

    models: dict[str, tuple[str, str, str]] = {}
    variables: dict[tuple[str, str], str] = {}    # (lib, variable) -> example model
    modelica = 0
    for dyd in refs["dyd"]:
        path = os.path.join(base, dyd)
        if not os.path.isfile(path):
            issues.add("error", f"dynamic models file '{dyd}' not found")
            continue
        scan = scan_dyd(path)
        modelica += scan["modelica_models"]
        models.update(scan["models"])
        lib_of = {i: m[0] for i, m in scan["models"].items()}
        for connector, id1, index1, name1, id2, index2, name2 in scan["macro_connects"]:
            for var1, var2 in scan["connectors"].get(connector, []):
                for model_id, var, index, name in ((id1, var1, index1, name1), (id2, var2, index2, name2)):
                    resolved = _resolve(var, index, name) if model_id in lib_of else None
                    if resolved:
                        variables.setdefault((lib_of[model_id], resolved), model_id)
        for id1, var1, id2, var2 in scan["connects"]:
            for model_id, var in ((id1, var1), (id2, var2)):
                if model_id in lib_of and "@" not in var:
                    variables.setdefault((lib_of[model_id], var), model_id)
        for model_id, var in scan["static_refs"]:
            if model_id in lib_of:
                variables.setdefault((lib_of[model_id], var), model_id)
        for model_id, (_, par_file, par_id) in scan["models"].items():
            if par_file and par_id:
                wanted_sets.setdefault(par_file, set()).add(par_id)

    libraries: dict[str, list[str]] = {}
    for model_id, (lib, _, _) in models.items():
        libraries.setdefault(lib, []).append(model_id)
    for lib, ids in libraries.items():
        if _desc(exe, lib) is None:
            for i in ids:
                issues.add("error", f"library '{lib}' not in this Dynawo's ddb", i)
    for (lib, var), model_id in sorted(variables.items()):
        desc = _desc(exe, lib)
        if desc is not None and not lib.startswith("DYNModel") and not _declared(desc[0], var):
            issues.add("error", f"variable '{var}' not declared by '{lib}'", model_id)

    par_sets: dict[tuple[str, str], set[str]] = {}
    for par_file, ids in wanted_sets.items():
        path = os.path.join(base, par_file)
        if not os.path.isfile(path):
            issues.add("error", f"parameters file '{par_file}' not found")
            continue
        found = scan_par_sets(path, ids)
        for set_id in ids:
            if set_id not in found:
                issues.add("error", f"parameter set '{set_id}' not in '{par_file}'")
            else:
                par_sets[(par_file, set_id)] = found[set_id]
    checked: set[tuple[str, str, str]] = set()
    for model_id, (lib, par_file, par_id) in models.items():
        desc = _desc(exe, lib)
        if desc is None or lib.startswith("DYNModel") or (lib, par_file, par_id) in checked:
            continue
        checked.add((lib, par_file, par_id))
        for name in sorted(par_sets.get((par_file, par_id), set()) - desc[1]):
            issues.add("warning", f"parameter '{name}' not declared by '{lib}'", f"set {par_id}")
    if modelica:
        issues.add("info", f"{modelica} Modelica model(s) compiled at run time: not checked")

    return {
        "issues": issues.as_list(),
        "models": len(models), "libraries": len(libraries), "parameter_sets": len(par_sets),
    }


# ── Migration with Dynawo's update-xml ────────────────────────────────────────

# Origin given to the update tool to apply every update script up to the target,
# whatever the files were written for (PowSyBl keeps an older naming than the
# Dynawo version it is given). Relies on the scripts leaving files already in
# the target's format unchanged: true of the 2.0.0.2 scripts (they rename,
# remove, or add only what is missing; none converts a value).
FULL_UPGRADE_ORIGIN = "0.0.0"

APP_UPDATE_TOOL = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "updateXML", "update.py")


def update_tool_places(exe: str) -> list[str]:
    """Where Dynawo's update.py is looked for, in order: the distribution
    (sbin/updateXML), $DYNAWO_HOME (what dynawo.sh update-xml uses; util/ in a
    source tree), the app's config directory."""
    places = [os.path.join(os.path.dirname(exe), "sbin", "updateXML", "update.py")]
    home = os.environ.get("DYNAWO_HOME")
    if home:
        places += [os.path.join(home, d, "updateXML", "update.py") for d in ("sbin", "util")]
    return places + [APP_UPDATE_TOOL]


def latest_script(tool: str) -> str | None:
    """Version of the tool's most recent update script, e.g. "1.8.0.8"."""
    versions = [m.group(1) for p in glob.glob(os.path.join(os.path.dirname(tool), "update*.py"))
                if (m := re.match(r"update(\d+(?:\.\d+){3})\.py$", os.path.basename(p)))]
    return max(versions, key=version_tuple) if versions else None


def find_update_tool(exe: str) -> str | None:
    """The most recent update tool among those found (an older one cannot
    produce what a newer Dynawo expects); the first found on a tie."""
    found = [p for p in update_tool_places(exe) if os.path.isfile(p)]
    return max(found, key=lambda p: version_tuple(latest_script(p) or "0"), default=None)


def update_python() -> str | None:
    """A Python able to run update.py (it needs lxml): the app's, else python3."""
    for python in (sys.executable, shutil.which("python3")):
        if python and subprocess.run([python, "-c", "import lxml"], capture_output=True).returncode == 0:
            return python
    return None


def tool_versions(tool: str) -> list[str]:
    """Dynawo versions the tool has update scripts for (x.y.z), ascending."""
    versions = {".".join(m.group(1).split(".")[:3])
                for p in glob.glob(os.path.join(os.path.dirname(tool), "update*.py"))
                if (m := re.match(r"update(\d+\.\d+\.\d+)\.\d+\.py$", os.path.basename(p)))}
    return sorted(versions, key=version_tuple)


def run_update(tool: str, python: str, jobs_path: str, origin: str, target: str, out_dir: str) -> dict:
    """Run update.py; returns {files: {name: path}, tickets: [...], output: str}.
    Raises RuntimeError on failure."""
    proc = subprocess.run(
        [python, tool, "--job", jobs_path, "--origin", origin, "--version", target, "-o", out_dir, "--log"],
        cwd=os.path.dirname(tool), capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},  # nothing written into the Dynawo tree
    )
    produced = glob.glob(os.path.join(out_dir, "*", "*"))
    if proc.returncode != 0 or not produced:
        raise RuntimeError((proc.stdout + proc.stderr).strip()[-2000:] or f"update.py exited with {proc.returncode}")
    files = {os.path.basename(p): p for p in produced if not os.path.basename(p).startswith("applied_tickets.log")}
    tickets = []
    for log in (p for p in produced if os.path.basename(p).startswith("applied_tickets.log")):
        with open(log, encoding="utf-8") as fh:
            tickets += [l.strip() for l in fh if l.strip()]
    return {"files": files, "tickets": tickets, "output": proc.stdout.strip()}


def _canonical(path: str) -> str:
    with open(path, "rb") as fh:
        root = ET.fromstring(fh.read())
    for el in root.iter():
        el.text = (el.text or "").strip() or None
        el.tail = None
    return ET.canonicalize(ET.tostring(root, encoding="unicode"))


def migration_changes(tool: str, python: str, session_dir: str, jobs_name: str, origin: str, target: str) -> dict:
    """What update.py would change between `origin` and `target`, on a
    temporary copy: {changed: [file names], tickets: [...]}."""
    with tempfile.TemporaryDirectory() as tmp:
        result = run_update(tool, python, os.path.join(session_dir, jobs_name), origin, target, tmp)
        base = os.path.dirname(os.path.join(session_dir, jobs_name))
        changed = [name for name, path in sorted(result["files"].items())
                   if not os.path.isfile(os.path.join(base, name)) or _canonical(path) != _canonical(os.path.join(base, name))]
    return {"changed": changed, "tickets": result["tickets"]}


def relink(path: str, present: set[str]) -> bytes:
    """An upgraded file whose references to files not upgraded with it point
    back to the originals, one directory up."""
    ET.register_namespace("dyn", _NS)
    tree = ET.parse(path)
    for el in tree.getroot().iter():
        for attr in _FILE_ATTRIBUTES:
            value = el.get(attr)
            if value and not os.path.isabs(value) and value not in present:
                el.set(attr, "../" + value)
    return ET.tostring(tree.getroot(), xml_declaration=True, encoding="UTF-8")
