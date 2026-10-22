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

from backend import association_par, association_rules
from backend.association_rules import check_condition, condition_code, element_table, get_element_type

# Export of the last generation as a standalone pypowsybl script. Element
# tables and .par conversion reuse the app's functions; conditions are
# written by condition_code.

_HEADER = '''#!/usr/bin/env python3
#
# Dynamic association exported from the Dynawo GUI on {date}, for Dynawo
# {dynawo_version}. Run it in the folder of the session files ("Download Files").
#
# Requires pypowsybl and a Dynawo configured in PowSyBl (~/.itools/config.yml):
#     dynawo:
#       homeDir: /path/to/dynawo      # folder holding dynawo.sh
#
# Parameter sets left empty in the GUI must be filled first.

import copy
import os
import re
import tempfile
import xml.etree.ElementTree as ET

import pandas as pd
import pypowsybl as pp
import pypowsybl.dynamic as dyn
import pypowsybl.report as rep

HERE = os.path.dirname(os.path.abspath(__file__))

NETWORK = {iidm!r}
MODELS_PAR = {models_par!r}
NETWORK_PAR, NETWORK_PAR_ID = {network_par!r}, {network_id!r}
SOLVER_TYPE, SOLVER_PAR, SOLVER_PAR_ID = {solver_type!r}, {solver_par!r}, {solver_id!r}
START_TIME, STOP_TIME = {start!r}, {stop!r}

_NS = {ns!r}
_VOLTAGE_LEVEL_ID = re.compile({vl_pattern!r})
_VOLTAGE_LEVEL_ATTRIBUTES = {vl_attributes!r}
'''

_FOOTER = '''

def powsybl_copy(name: str, tmp_dir: str) -> str:
    """A copy of a .par PowSyBl can read (see for_powsybl)."""
    with open(os.path.join(HERE, name), "rb") as fh:
        content = fh.read()
    path = os.path.join(tmp_dir, name)
    with open(path, "wb") as fh:
        fh.write(for_powsybl(content))
    return path


def main() -> None:
    network = pp.network.load(os.path.join(HERE, NETWORK))
    mapping = build_mapping(network)
    report = rep.ReportNode()
    with tempfile.TemporaryDirectory() as tmp_dir:
        parameters = dyn.Parameters(START_TIME, STOP_TIME, {
            "parametersFile":         powsybl_copy(MODELS_PAR, tmp_dir),
            "network.parametersFile": powsybl_copy(NETWORK_PAR, tmp_dir),
            "network.parametersId":   NETWORK_PAR_ID,
            "solver.type":            SOLVER_TYPE,
            "solver.parametersFile":  powsybl_copy(SOLVER_PAR, tmp_dir),
            "solver.parametersId":    SOLVER_PAR_ID,
        })
        result = dyn.Simulation().run(network, mapping, parameters=parameters, report_node=report)
    print(report)
    print("status:", result.status(), result.status_text() or "")


if __name__ == "__main__":
    main()
'''


def build_script(generation: dict, network) -> str:
    """Script reproducing a recorded generation; `network` gives the attribute kinds."""
    header = _HEADER.format(
        date=datetime.date.today().isoformat(), dynawo_version=generation["dynawo_version"],
        iidm=generation["iidm"], models_par=generation["models_par"],
        network_par=generation["network_par"], network_id=generation["network_id"],
        solver_type=generation["solver_type"], solver_par=generation["solver_par"],
        solver_id=generation["solver_id"], start=generation["start"], stop=generation["stop"],
        ns=association_par._NS, vl_pattern=association_rules._VOLTAGE_LEVEL_ID.pattern,
        vl_attributes=association_rules._VOLTAGE_LEVEL_ATTRIBUTES,
    )
    functions = "\n\n" + inspect.getsource(element_table) + "\n\n" + inspect.getsource(association_par.for_powsybl)
    return header + functions + "\n\n" + _mapping_function(generation, network) + _FOOTER


def _mapping_function(generation: dict, network) -> str:
    lines = [
        "def build_mapping(network) -> dyn.ModelMapping:",
        '    """Apply the rules in order; a later rule overrides an earlier one."""',
        "    owner = {}  # (element type, id) -> (category, model, parameter set id)",
        "    tables = {}",
    ]
    catalogue_category = generation.get("model_category", {})
    tables = {}
    for index, (rule, set_id) in enumerate(zip(generation["rules"], generation["set_ids"])):
        lines.append("")
        type_entry = get_element_type(rule.get("element_type", ""))
        category = catalogue_category.get(rule.get("model", ""))
        if type_entry is None or category is None or set_id is None:
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
            f"    # Rule {index + 1}: {type_entry['label'].lower()}, {conditions} -> {rule['model']}",
            f"    if {type_entry['id']!r} not in tables:",
            f"        tables[{type_entry['id']!r}] = element_table(network, {{'getter': {type_entry['getter']!r}}})",
            f"    df = tables[{type_entry['id']!r}]",
            f"    selected = df[{' & '.join(filters)}]" if filters else "    selected = df",
            "    for static_id in selected['id']:",
            f"        owner[({type_entry['id']!r}, static_id)] = ({category!r}, {rule['model']!r}, {set_id!r})",
        ]
    lines += [
        "",
        "    mapping = dyn.ModelMapping()",
        "    by_category = {}",
        "    for (_, static_id), (category, model, set_id) in owner.items():",
        "        by_category.setdefault(category, []).append((static_id, set_id, model))",
        "    for category, rows in by_category.items():",
        "        mapping.add_dynamic_model(category, pd.DataFrame(",
        "            rows, columns=['static_id', 'parameter_set_id', 'model_name']).set_index('static_id'))",
        "    return mapping",
    ]
    return "\n".join(lines)
