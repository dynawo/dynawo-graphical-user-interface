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
import xml.etree.ElementTree as ET

_NS = "http://www.rte-france.com/dynawo"


def _desc_path(dynawo_exe: str, lib: str) -> str | None:
    if not dynawo_exe:
        return None
    path = os.path.join(os.path.dirname(dynawo_exe), "ddb", f"{lib}.desc.xml")
    return path if os.path.isfile(path) else None


def get_lib_symbols(dynawo_exe: str, lib: str) -> tuple[list[str], list[str]]:
    """Return (variables, parameters) declared by a library's ddb/<lib>.desc.xml.

    A .crv <curve> accepts either kind of name: Dynawo resolves the curve against
    the model's variables first and falls back to its parameters (a parameter curve
    is simply constant over time).
    Returns ([], []) if the executable path is unset, the file is missing, or
    parsing fails.
    """
    desc_path = _desc_path(dynawo_exe, lib)
    if not desc_path:
        return [], []
    try:
        root = ET.parse(desc_path).getroot()
        variables = sorted(
            v.get("name")
            for v in root.findall(f".//{{{_NS}}}variable")
            if v.get("name")
        )
        parameters = sorted(
            p.get("name")
            for p in root.findall(f".//{{{_NS}}}parameter")
            if p.get("name")
        )
        return variables, parameters
    except Exception:
        return [], []


def get_lib_variables(dynawo_exe: str, lib: str) -> list[str]:
    """Return sorted variable names for a library from its ddb/<lib>.desc.xml."""
    return get_lib_symbols(dynawo_exe, lib)[0]


def get_lib_parameters(dynawo_exe: str, lib: str) -> list[str]:
    """Return sorted parameter names for a library from its ddb/<lib>.desc.xml."""
    return get_lib_symbols(dynawo_exe, lib)[1]


def get_lib_parameter_details(dynawo_exe: str, lib: str) -> list[dict]:
    """Return the parameters of ddb/<lib>.desc.xml with what a form needs.

    Same source as get_lib_parameters, but keeping the attributes the name alone
    loses: `value_type` (BOOL/INT/DOUBLE/STRING) decides which input to render,
    and `read_only` marks the parameters Dynawo computes itself — an event
    descriptor declares several of those (event_openOrigin, event_stateEvent1 on
    a quadripole event…) and writing them into a .par is meaningless.
    Ordered by name, and empty when the executable is unset or the file missing.
    """
    desc_path = _desc_path(dynawo_exe, lib)
    if not desc_path:
        return []
    try:
        root = ET.parse(desc_path).getroot()
    except Exception:
        return []
    params = [
        {
            "name":       p.get("name"),
            "value_type": (p.get("valueType") or "").upper(),
            "default":    p.get("defaultValue"),
            "read_only":  (p.get("readOnly") or "false").lower() == "true",
        }
        for p in root.findall(f".//{{{_NS}}}parameter")
        if p.get("name")
    ]
    return sorted(params, key=lambda p: p["name"])
