#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import datetime
import inspect
import io
import json
import os
import xml.etree.ElementTree as ET
import zipfile
from typing import Callable

from backend import association_par, association_rules, compatibility
from backend.association_custom import numeric_version
from backend.association_par import FILE, GENERIC, _Assembler, spec_key
from backend.association_rules import (
    automation_fields,
    automation_id,
    check_condition,
    condition_code,
    element_table,
    get_automation_system,
    get_element_type,
)
from backend.powsybl_config import _FAKE_DYNAWO_SCRIPT

# Export of the last generation as a standalone folder: a pypowsybl script,
# the network and the user's .par files the rules use, the models added in
# the GUI and a README. As in the GUI, PowSyBl writes the job with a fake
# Dynawo, Dynawo's update tool upgrades it to the Dynawo running it, then the
# script runs it. The script merges the .par files as the GUI does; element
# tables, the merge, the .par conversion and the upgrade reuse the app's
# functions, conditions are written by condition_code.

SCRIPT_NAME = "dynamic_association.py"
MODELS_JSON_NAME = "models.json"
README_NAME = "README.md"
RUNS_DIR = "runs"
# PowSyBl configuration and fake Dynawo, written by the script.
POWSYBL_DIR = "powsybl"
# Sets filled in the GUI (no file of the user's), and the app's generic sets.
MANUAL_PAR_NAME = "manual_sets.par"
GENERIC_PAR_NAME = "generic.par"

_INTRO = '''#!/usr/bin/env python3
#
# Dynamic association exported from the Dynawo GUI on {date}, for Dynawo
# {dynawo_version}. PowSyBl applies the rules to the network and writes the
# Dynawo job, Dynawo's update tool upgrades it to the Dynawo running it, then
# the script runs it. See {readme}.
#
#     python {script} [--network other.iidm] [--par-dir DIR] [--par NAME=PATH] ...
#     python {script} --help
'''

_IMPORTS = '''
import argparse
import copy
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
'''

_SETUP = r'''
HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
POWSYBL = os.path.join(HERE, "powsybl")

# Read by GraalVM, pypowsybl's Java side, when pypowsybl is imported: the
# PowSyBl configuration of powsybl/ (not ~/.itools), runs kept in runs/.
if " " in HERE:
    sys.exit(f"Move this folder to a path without spaces (GraalVM options cannot hold them): {HERE}")
os.makedirs(RUNS, exist_ok=True)
os.makedirs(POWSYBL, exist_ok=True)
# PowSyBl writes the job with the fake Dynawo of powsybl/, which answers its
# version query with WRITE_VERSION and simulates nothing.
_config = f"dynawo:\n  homeDir: {json.dumps(POWSYBL)}\n  debug: true\n"
for _name, _text in {**FAKE_DYNAWO, "version": WRITE_VERSION + "\n", "config.yml": _config}.items():
    with open(os.path.join(POWSYBL, _name), "w", encoding="utf-8", newline="") as _fh:
        _fh.write(_text)
os.chmod(os.path.join(POWSYBL, "dynawo.sh"), 0o755)
# PowSyBl's per-process working folders, left empty by earlier runs.
for _name in os.listdir(RUNS):
    if _name.startswith("itools_common_") and not os.listdir(os.path.join(RUNS, _name)):
        os.rmdir(os.path.join(RUNS, _name))
_options = re.sub(r"-D(powsybl\.config\.dirs|java\.io\.tmpdir)=\S*", "", os.environ.get("GRAALVM_OPTIONS", ""))
os.environ["GRAALVM_OPTIONS"] = f"{_options.strip()} -Dpowsybl.config.dirs={POWSYBL} -Djava.io.tmpdir={RUNS}".strip()

import pandas as pd  # noqa: E402
import pypowsybl as pp  # noqa: E402
import pypowsybl.dynamic as dyn  # noqa: E402
import pypowsybl.report as rep  # noqa: E402
'''

_FOOTER = '''

def build_mapping(owner: dict, automation: dict, set_of: dict) -> dyn.ModelMapping:
    """ModelMapping of the selected models, with their set ids in the merged .par."""
    mapping = dyn.ModelMapping()
    by_category = {}
    for (_, static_id), (category, model, spec) in owner.items():
        by_category.setdefault(category, []).append((static_id, set_of[spec], model))
    for category, rows in by_category.items():
        mapping.add_dynamic_model(category, pd.DataFrame(
            rows, columns=["static_id", "parameter_set_id", "model_name"]).set_index("static_id"))
    # Automation systems: one dataframe per category, indexed by their own id.
    by_category = {}
    for dynamic_id, (category, model, spec, fields) in automation.items():
        by_category.setdefault(category, []).append(
            {"dynamic_model_id": dynamic_id, "parameter_set_id": set_of[spec], "model_name": model, **fields})
    for category, rows in by_category.items():
        mapping.add_dynamic_model(category, pd.DataFrame(rows).set_index("dynamic_model_id"))
    return mapping


def read(path: str, what: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        sys.exit(f"Cannot read {what} {path}: {exc.strerror}")


def powsybl_copy(content: bytes, name: str, tmp_dir: str) -> str:
    """A copy of a .par PowSyBl can read (see for_powsybl)."""
    path = os.path.join(tmp_dir, name)
    with open(path, "wb") as fh:
        fh.write(for_powsybl(content))
    return path


def arguments() -> argparse.Namespace:
    """Command-line arguments; files default to those of this folder."""
    default = lambda name: f"(default: {name} in this folder)" if name else "(default: none)"
    parser = argparse.ArgumentParser(description="Dynamic association exported from the Dynawo GUI: PowSyBl "
                                     "writes the Dynawo job, Dynawo's update tool upgrades it to the Dynawo "
                                     "running it, then the script runs it.")
    parser.add_argument("--network", help=f"IIDM network {default(NETWORK)}")
    parser.add_argument("--par-dir", help="folder where to find the .par files, by name "
                                          "(default: this folder): " + ", ".join(PAR_FILES))
    parser.add_argument("--par", action="append", default=[], metavar="NAME=PATH",
                        help="use PATH in place of the .par file NAME; repeatable")
    parser.add_argument("--network-par", help=f".par of the network parameters {default(NETWORK_PAR)}")
    parser.add_argument("--network-par-id", default=NETWORK_PAR_ID,
                        help="set to use in the network parameters .par (default: %(default)s)")
    parser.add_argument("--solver-par", help=f".par of the solver parameters {default(SOLVER_PAR)}")
    parser.add_argument("--solver-par-id", default=SOLVER_PAR_ID,
                        help="set to use in the solver parameters .par (default: %(default)s)")
    parser.add_argument("--models-json", help=f"models added in the GUI, powsybl-dynawo additional models file "
                                              f"{default(MODELS_JSON)}")
    parser.add_argument("--start-time", type=float, default=START_TIME, help="(default: %(default)s s)")
    parser.add_argument("--stop-time", type=float, default=STOP_TIME, help="(default: %(default)s s)")
    parser.add_argument("--dynawo", default=DYNAWO,
                        help="dynawo.sh (dynawo.cmd on Windows) of the Dynawo running the job (default: %(default)s)")
    parser.add_argument("--dynawo-version", help="Dynawo version to upgrade the job to (default: that of --dynawo)")
    parser.add_argument("--update-tool", help="Dynawo's updateXML/update.py (default: the most recent found in "
                                              "sbin/updateXML of --dynawo, $DYNAWO_HOME, the GUI's configuration)")
    parser.add_argument("--no-update", action="store_true",
                        help=f"run the job as PowSyBl writes it, for Dynawo {numeric_version(WRITE_VERSION)}")
    parser.add_argument("--write-only", action="store_true", help="write (and upgrade) the job without running it")
    args = parser.parse_args()
    replaced = {}
    for item in args.par:
        name, sep, path = item.partition("=")
        if not sep or not name or not path:
            parser.error(f"--par expects NAME=PATH, not {item!r}")
        replaced[name] = path
    args.par = replaced
    par_path = lambda name: replaced.get(name) or os.path.join(args.par_dir or HERE, name)
    args.par_path = par_path
    args.network = args.network or os.path.join(HERE, NETWORK)
    args.network_par = args.network_par or par_path(NETWORK_PAR)
    args.solver_par = args.solver_par or par_path(SOLVER_PAR)
    if args.models_json is None and MODELS_JSON:
        args.models_json = os.path.join(HERE, MODELS_JSON)
    if args.dynawo and not os.path.isfile(args.dynawo):
        parser.error(f"Dynawo not found: {args.dynawo} (see --dynawo)")
    if args.update_tool and not os.path.isfile(args.update_tool):
        parser.error(f"Update tool not found: {args.update_tool} (see --update-tool)")
    if not args.dynawo and not args.write_only:
        parser.error("no Dynawo to run the job: give --dynawo (or --write-only)")
    return args


def dynawo_version(dynawo: str) -> str:
    """First line of `dynawo.sh version`, e.g. "1.7.1 (rev:v1.7-1250b93)"."""
    try:
        proc = subprocess.run([dynawo, "version"], capture_output=True, text=True, timeout=60)
    except OSError as exc:
        sys.exit(f"Cannot run {dynawo}: {exc.strerror}")
    lines = proc.stdout.strip().splitlines()
    if proc.returncode != 0 or not lines:
        sys.exit(f"Cannot get the version of {dynawo}: {(proc.stdout + proc.stderr).strip()[-500:]}")
    return lines[0].strip()


def write_job(args: argparse.Namespace) -> str:
    """The job PowSyBl writes with the fake Dynawo, in its run folder of runs/."""
    network = pp.network.load(args.network)
    owner, automation = select_models(network)
    # The sets the rules use, merged into one .par as PowSyBl reads a single one.
    specs = sorted({spec for _, _, spec in owner.values()} | {spec for _, _, spec, _ in automation.values()})
    files = {name: read(args.par_path(name), "the .par") for name in {name for name, _ in specs}}
    for name, set_id in specs:
        if set_id not in set_ids(files[name]):
            sys.exit(f"Set {set_id!r} not found in {args.par_path(name)}")
    models_par, set_of, _ = assemble_par([(FILE, name, set_id) for name, set_id in specs], files, None)
    set_of = {(name, set_id): merged for (_, name, set_id), merged in set_of.items()}
    mapping = build_mapping(owner, automation, set_of)
    report = rep.ReportNode()
    before = set(os.listdir(RUNS))
    error = None
    with tempfile.TemporaryDirectory() as tmp_dir:
        provider = {
            "parametersFile":         powsybl_copy(models_par, "models.par", tmp_dir),
            "network.parametersFile": powsybl_copy(read(args.network_par, "the network .par"), "network.par", tmp_dir),
            "network.parametersId":   args.network_par_id,
            "solver.type":            SOLVER_TYPE,
            "solver.parametersFile":  powsybl_copy(read(args.solver_par, "the solver .par"), "solver.par", tmp_dir),
            "solver.parametersId":    args.solver_par_id,
        }
        if args.models_json:
            provider["additionalModelsFile"] = os.path.abspath(args.models_json)
        parameters = dyn.Parameters(args.start_time, args.stop_time, provider)
        try:
            dyn.Simulation().run(network, mapping, parameters=parameters, report_node=report)
        except Exception as exc:
            error = str(exc)
    print(report)
    # PowSyBl's run folder is kept; its other working files (version checks…) are removed.
    jobs = []
    for name in sorted(set(os.listdir(RUNS)) - before):
        path = os.path.join(RUNS, name)
        if name.startswith("dynawo_") and not name.startswith("dynawo_version_"):
            jobs += glob.glob(os.path.join(path, "*.jobs"))
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    if error or not jobs:
        sys.exit(f"PowSyBl could not write the job: {error or 'no .jobs written'}")
    print(f"job written by PowSyBl for Dynawo {numeric_version(WRITE_VERSION)}:", jobs[0])
    return jobs[0]


def upgrade(jobs: str, target: str | None, args: argparse.Namespace) -> str:
    """The job upgraded to Dynawo `target` by applying all of Dynawo's updates
    up to it, in a folder next to it; the job as written when it cannot be."""
    if args.no_update or not target:
        return jobs
    tool = args.update_tool or (find_update_tool(args.dynawo) if args.dynawo else None)
    python = update_python()
    if not tool or not python:
        why = ("the update tool needs a Python with lxml (pip install lxml)" if tool
               else "Dynawo's update tool (updateXML/update.py) was not found (see --update-tool)")
        print(f"job not upgraded to Dynawo {target}: {why}")
        return jobs
    folder = os.path.join(os.path.dirname(jobs), f"dynawo{target}")
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            result = run_update(tool, python, jobs, FULL_UPGRADE_ORIGIN, target, tmp_dir)
        except RuntimeError as exc:
            sys.exit(f"The update tool failed: {exc}")
        os.makedirs(folder, exist_ok=True)
        for name, path in result["files"].items():
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(relink(path, set(result["files"])))
    print(f"job upgraded to Dynawo {target} by {tool} ({len(result['tickets'])} update(s) applied):", folder)
    return os.path.join(folder, os.path.basename(jobs))


def run_dynawo(dynawo: str, jobs: str) -> int:
    """Dynawo's exit code on the job."""
    print("running:", dynawo, "jobs", jobs, flush=True)
    code = subprocess.run([dynawo, "jobs", jobs], cwd=os.path.dirname(jobs)).returncode
    outputs = ET.parse(jobs).getroot().find(f".//{{{_NS}}}outputs")
    folder = os.path.join(os.path.dirname(jobs), outputs.get("directory", "outputs") if outputs is not None else "")
    print("Dynawo", "succeeded" if code == 0 else f"failed (exit code {code})", "- outputs:", folder)
    return code


def main() -> None:
    args = arguments()
    target = args.dynawo_version or (numeric_version(dynawo_version(args.dynawo)) if args.dynawo else None)
    jobs = upgrade(write_job(args), target, args)
    if not args.write_only:
        sys.exit(run_dynawo(args.dynawo, jobs))


if __name__ == "__main__":
    main()
'''

_README = '''# Dynamic association

Exported from the Dynawo GUI on {date}, for Dynawo {dynawo_version}.

`{script}` applies the rules defined in the GUI to a network, in three steps:

1. **Write**: PowSyBl writes the Dynawo job (`.jobs`, `.dyd`, `.par`) for
   Dynawo {write_version}, with a fake Dynawo that simulates nothing (in
   `{powsybl}/`). The job never depends on the Dynawo running it.
2. **Upgrade**: Dynawo's update tool brings the job to the version of the
   Dynawo running it, so that it runs on a Dynawo more recent than the one
   PowSyBl writes for.
3. **Run**: the script runs the job with that Dynawo.

## Content

| File | Role |
|---|---|
{files}

## Requirements

- Python with **pypowsybl** (the GUI used pypowsybl {pypowsybl}).
- A **Dynawo** install: its `dynawo.sh` (`dynawo.cmd` on Windows) is
  `{dynawo}` by default, else give it with `--dynawo`.
- For the upgrade, Dynawo's **update tool** (`updateXML/update.py`) and
  **lxml** in the Python running the script (`pip install lxml`). The most
  recent tool found is used, in `sbin/updateXML` of the Dynawo, in
  `$DYNAWO_HOME` (`sbin/` or `util/`), or in the GUI's configuration
  (`~/.config/dynawo_ihm/updateXML`); `--update-tool` gives another. Without
  it, the job runs as written, which suits Dynawo {write_version}.
- A path **without spaces** to this folder (GraalVM, pypowsybl's Java side,
  cannot take them in its options).

## Run

```
python {script}
```

Each file can be replaced:

```
python {script} --network other.iidm
python {script} --par-dir /path/to/my/par/files
python {script} --par {example_par}=/path/to/other.par
python {script} --stop-time 50
python {script} --dynawo /path/to/dynawo/dynawo.sh
python {script} --help
```

- `--network`: the IIDM network. The rules are applied to it as in the GUI:
  each selects the elements meeting its conditions, whatever they test
  (`nominal_v`, energy source, id…). On another network, a condition on ids
  only selects the ids present in it.
- `--par-dir`: folder where the `.par` files are looked for, by name:
  {par_files}.
- `--par NAME=PATH`: one of them taken from elsewhere (repeatable).
- `--network-par FILE` and `--network-par-id SET`: the `.par` file of the
  network parameters, and the set to use in it (default: `{network_par}`,
  set `{network_id}`).
- `--solver-par FILE` and `--solver-par-id SET`: the same for the solver
  parameters (default: `{solver_par}`, set `{solver_id}`).
- `--models-json`: models added in the GUI (powsybl-dynawo additional models
  file){models_json}.
- `--start-time`, `--stop-time`: the time window, in seconds.

Dynawo version:

- `--dynawo`: the `dynawo.sh` running the job; the job is upgraded to its
  version.
- `--dynawo-version`: the version to upgrade to, in place of that of
  `--dynawo` (e.g. to run the job elsewhere, with `--write-only`).
- `--update-tool`: the update tool's `update.py`.
- `--no-update`: run the job as PowSyBl writes it.
- `--write-only`: write (and upgrade) the job, without running it.

## Results

Each run is kept in `{runs}/` of this folder, as `dynawo_<number>/`:

- the job PowSyBl wrote (`powsybl_dynawo.jobs`, its `.dyd`, `.par` and
  network);
- `dynawo<version>/`: the job upgraded to that Dynawo version, its files
  pointing to the others one folder up;
- `outputs/`, next to the job Dynawo ran.

The script prints PowSyBl's report, the job it runs and the outputs folder,
and exits with Dynawo's exit code. PowSyBl reads the configuration of
`{powsybl}/`, written by the script, never `~/.itools`.

## Parameters

PowSyBl reads a single `.par` for the models: the script merges the sets the
rules use from their files, as the GUI does (a set id found in two files is
renamed in the merged copy). `{manual}` holds the sets filled in the GUI
rather than taken from a file: sets still empty must be filled before
running.
'''

_FAKE_DYNAWO_CMD = '''@echo off
rem Fake Dynawo of the dynamic association: answers PowSyBl's version query
rem with the content of the "version" file next to it, simulates nothing.
if "%1"=="version" type "%~dp0version"
exit /b 0
'''


def _intro(generation: dict) -> str:
    return _INTRO.format(date=datetime.date.today().isoformat(), dynawo_version=generation["dynawo_version"],
                         readme=README_NAME, script=SCRIPT_NAME)


def _fake_dynawo(generation: dict) -> str:
    """Constants _SETUP writes the fake Dynawo with."""
    with open(_FAKE_DYNAWO_SCRIPT, encoding="utf-8") as fh:
        script = fh.read()
    return "\n".join([
        "",
        "# Version PowSyBl writes the job for (that of the GUI's generation), and the",
        "# fake Dynawo it writes it with.",
        f"WRITE_VERSION = {generation['dynawo_version']!r}",
        f"FAKE_DYNAWO = {{'dynawo.sh': {script!r}, 'dynawo.cmd': {_FAKE_DYNAWO_CMD!r}}}",
    ])


def _constants(generation: dict, iidm: str, par_files: list[str], network: tuple, solver: tuple,
               dynawo: str | None) -> str:
    models_json = MODELS_JSON_NAME if generation.get("additional_models") else None
    return "\n".join([
        "",
        "# Dynawo running the job (that selected in the GUI), see --dynawo.",
        f"DYNAWO = {dynawo!r}",
        "# Defaults, files of this folder.",
        f"NETWORK = {iidm!r}",
        "# .par files, looked up by name (see --par-dir, --par).",
        f"PAR_FILES = {par_files!r}",
        f"NETWORK_PAR, NETWORK_PAR_ID = {network[0]!r}, {network[1]!r}",
        f"SOLVER_TYPE, SOLVER_PAR, SOLVER_PAR_ID = {generation['solver_type']!r}, {solver[0]!r}, {solver[1]!r}",
        f"START_TIME, STOP_TIME = {generation['start']!r}, {generation['stop']!r}",
        "# Models added in the GUI (powsybl-dynawo additional models file), if any.",
        f"MODELS_JSON = {models_json!r}",
        "",
        f"FILE, GENERIC, MANUAL = {FILE!r}, {GENERIC!r}, {association_par.MANUAL!r}",
        f"_NS = {association_par._NS!r}",
        f"_VOLTAGE_LEVEL_ID = re.compile({association_rules._VOLTAGE_LEVEL_ID.pattern!r})",
        f"_VOLTAGE_LEVEL_ATTRIBUTES = {association_rules._VOLTAGE_LEVEL_ATTRIBUTES!r}",
        f"_FILE_ATTRIBUTES = {compatibility._FILE_ATTRIBUTES!r}",
        f"FULL_UPGRADE_ORIGIN = {compatibility.FULL_UPGRADE_ORIGIN!r}",
        'APP_UPDATE_TOOL = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "updateXML", "update.py")',
        "",
    ])


def _functions() -> str:
    """App functions the script runs as they are."""
    functions = [element_table, association_par.for_powsybl, association_par.set_ids, association_par._unique,
                 _Assembler, association_par.assemble_par, numeric_version, compatibility.version_tuple,
                 compatibility.update_tool_places, compatibility.latest_script, compatibility.find_update_tool,
                 compatibility.update_python, compatibility.run_update, compatibility.relink]
    return "".join("\n\n" + inspect.getsource(f) for f in functions)


def build_script(generation: dict, network, iidm: str, par_files: list[str], rule_specs: list,
                 network_par: tuple, solver_par: tuple, dynawo: str | None) -> str:
    """Script reproducing a recorded generation, with the files of the exported
    folder: `rule_specs` gives each rule's (.par name, set id), None if
    skipped; `network` gives the attribute kinds; `dynawo` is the Dynawo
    running the job by default."""
    return (_intro(generation) + _IMPORTS + _fake_dynawo(generation) + _SETUP
            + _constants(generation, iidm, par_files, network_par, solver_par, dynawo)
            + _functions() + "\n\n" + _select_function(generation, network, rule_specs) + _FOOTER)


def _select_function(generation: dict, network, rule_specs: list) -> str:
    lines = [
        "def select_models(network) -> tuple[dict, dict]:",
        '    """The rules applied in order, a later rule overriding an earlier one:',
        "    (equipment: {(element type, id): (category, model, (.par name, set id))},",
        '    automation systems: {dynamic model id: (category, model, (.par name, set id), fields)})."""',
        "    owner = {}",
        "    automation = {}",
        "    tables = {}",
    ]
    catalogue_category = generation.get("model_category", {})
    tables = {}
    for index, (rule, spec) in enumerate(zip(generation["rules"], rule_specs)):
        lines.append("")
        type_entry = get_element_type(rule.get("element_type", ""))
        category = catalogue_category.get(rule.get("model", ""))
        if type_entry is None or category is None or spec is None:
            lines.append(f"    # Rule {index + 1}: skipped by the GUI ({rule.get('element_type')} → {rule.get('model')})")
            continue
        if type_entry["id"] not in tables:
            tables[type_entry["id"]] = element_table(network, type_entry)
        df = tables[type_entry["id"]]
        filters = []
        for condition in rule.get("conditions", []):
            value, error = check_condition(df, condition)
            if error:
                filters = None
                break
            filters.append(condition_code(condition, value))
        if filters is None:
            lines.append(f"    # Rule {index + 1}: skipped by the GUI (invalid condition)")
            continue
        conditions = " & ".join(filters) if filters else "all of them"
        lines += [
            f"    # Rule {index + 1}: {type_entry['label'].lower()}, {conditions} -> {rule['model']}, set {spec[1]} of {spec[0]}",
            f"    if {type_entry['id']!r} not in tables:",
            f"        tables[{type_entry['id']!r}] = element_table(network, {{'getter': {type_entry['getter']!r}}})",
            f"    df = tables[{type_entry['id']!r}]",
            f"    selected = df[{' & '.join(filters)}]" if filters else "    selected = df",
            "    for static_id in selected['id']:",
        ]
        system = get_automation_system(category)
        if system is None:
            lines.append(f"        owner[({type_entry['id']!r}, static_id)] = ({category!r}, {rule['model']!r}, {spec!r})")
        else:
            fields, _ = automation_fields(system, rule.get("fields"))
            prefix, suffix = automation_id(system, "\0", fields).split("\0")
            element_fields = ", ".join(f"{f!r}: static_id" for f in system["element_fields"])
            lines.append(f"        automation[{prefix!r} + static_id + {suffix!r}] = "
                         f"({category!r}, {rule['model']!r}, {spec!r}, {{{element_fields}, **{fields!r}}})")
    lines += ["", "    return owner, automation"]
    return "\n".join(lines)


def _readme(generation: dict, files: dict[str, str], par_files: list[str], network_par: tuple, solver_par: tuple,
            dynawo: str | None, pypowsybl_version: str) -> str:
    rows = "\n".join(f"| `{name}` | {role} |" for name, role in files.items())
    return _README.format(
        date=datetime.date.today().isoformat(), dynawo_version=generation["dynawo_version"],
        write_version=numeric_version(generation["dynawo_version"]), dynawo=dynawo or "none",
        script=SCRIPT_NAME, powsybl=POWSYBL_DIR, runs=RUNS_DIR, manual=MANUAL_PAR_NAME, files=rows,
        pypowsybl=pypowsybl_version, par_files=", ".join(f"`{p}`" for p in par_files) or "none",
        example_par=par_files[0] if par_files else "NAME",
        models_json="" if generation.get("additional_models") else ": none were added",
        network_par=network_par[0], network_id=network_par[1], solver_par=solver_par[0], solver_id=solver_par[1],
    )


def _entry(name: str, mode: int) -> zipfile.ZipInfo:
    """Zip entry with Unix permissions."""
    info = zipfile.ZipInfo(name, date_time=datetime.datetime.now().timetuple()[:6])
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_export(generation: dict, network, read: Callable[[str], bytes], dynawo: str | None,
                 pypowsybl_version: str) -> bytes:
    """Zip of the exported folder. `read` gives the content of a session file:
    the network, the user's .par files the rules use, and the generated .par
    for the sets filled in the GUI; `dynawo` is the selected Dynawo."""
    content: dict[str, bytes] = {}      # name in the folder -> content
    roles: dict[str, str] = {}          # name in the folder -> role, for the README
    names: dict[str, str] = {}          # session file -> name in the folder
    reserved = {SCRIPT_NAME, MODELS_JSON_NAME, README_NAME, MANUAL_PAR_NAME, GENERIC_PAR_NAME, RUNS_DIR, POWSYBL_DIR}

    def add(session_name: str, role: str) -> str:
        """Session file copied into the folder under its own name (made unique)."""
        if session_name not in names:
            stem, ext = os.path.splitext(os.path.basename(session_name))
            name, n = stem + ext, 2
            while name in content or name in reserved:
                name, n = f"{stem}_{n}{ext}", n + 1
            names[session_name], content[name], roles[name] = name, read(session_name), role
        return names[session_name]

    manual = _Assembler()
    manual_ids: dict[tuple[str, str], str] = {}
    parsed: dict[str, ET.Element] = {}

    def manual_set(generated_par: str, set_id: str) -> tuple[str, str]:
        """A set filled in the GUI, copied from the generated .par into the manual one."""
        if generated_par not in parsed:
            parsed[generated_par] = ET.fromstring(read(generated_par))
        key = (generated_par, set_id)
        if key not in manual_ids:
            manual_ids[key] = manual.copy_set(generated_par, parsed[generated_par], set_id, set_id)
        return MANUAL_PAR_NAME, manual_ids[key]

    iidm = add(generation["iidm"], "network (IIDM)")
    rule_specs = []
    for rule, set_id in zip(generation["rules"], generation["set_ids"]):
        if set_id is None:
            rule_specs.append(None)
            continue
        source, par_file, original_id = spec_key(rule)
        if source == FILE:
            rule_specs.append((add(par_file, "parameters of the models (your file)"), original_id))
        elif source == GENERIC:
            content[GENERIC_PAR_NAME] = association_par.generic_par() or b""
            roles[GENERIC_PAR_NAME] = "generic parameters of the models (the GUI's)"
            rule_specs.append((GENERIC_PAR_NAME, original_id))
        else:
            rule_specs.append(manual_set(generation["models_par"], set_id))

    def network_or_solver(source_key: str, generated_key: str, id_key: str, what: str) -> tuple[str, str]:
        source = generation.get(source_key)
        if source:
            return add(source, f"{what} parameters (your file)"), generation[id_key]
        return manual_set(generation[generated_key], generation[id_key])

    network_par = network_or_solver("network_source", "network_par", "network_id", "network")
    solver_par = network_or_solver("solver_source", "solver_par", "solver_id", "solver")
    if manual_ids:
        content[MANUAL_PAR_NAME] = manual.to_bytes()
        roles[MANUAL_PAR_NAME] = "parameter sets filled in the GUI"
    par_files = sorted({spec[0] for spec in rule_specs if spec} | {network_par[0], solver_par[0]})

    folder = os.path.splitext(SCRIPT_NAME)[0]
    files = {SCRIPT_NAME: "the script", README_NAME: "this file"}
    if generation.get("additional_models"):
        files[MODELS_JSON_NAME] = "models added in the GUI"
    files.update(roles)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        script = build_script(generation, network, iidm, par_files, rule_specs, network_par, solver_par, dynawo)
        archive.writestr(_entry(f"{folder}/{SCRIPT_NAME}", 0o755), script)
        archive.writestr(_entry(f"{folder}/{README_NAME}", 0o644),
                         _readme(generation, files, par_files, network_par, solver_par, dynawo, pypowsybl_version))
        if generation.get("additional_models"):
            archive.writestr(_entry(f"{folder}/{MODELS_JSON_NAME}", 0o644),
                             json.dumps(generation["additional_models"], indent=2))
        for name, data in content.items():
            archive.writestr(_entry(f"{folder}/{name}", 0o644), data)
    return buffer.getvalue()
