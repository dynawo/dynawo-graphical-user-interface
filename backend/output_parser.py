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

_CONSTRAINT_FLOAT_ATTRS = ("time", "limit", "value", "acceptableDuration")
_CONSTRAINT_ATTRS = ("modelName", "description", "time", "type", "kind", "limit", "value", "side", "acceptableDuration")
_CONSTRAINT_KEYS = {
    "modelName": "model_name", "description": "description", "time": "time",
    "type": "type", "kind": "kind", "limit": "limit", "value": "value",
    "side": "side", "acceptableDuration": "acceptable_duration",
}


def _to_float(raw: str) -> float | str:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return raw


def parse_timeline(content: bytes) -> list[dict]:
    """Parse a timeline.xml's flat <event time=".." modelName=".." message=".."/> list."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    return [
        {
            "time": _to_float(e.get("time", "")),
            "model_name": e.get("modelName", ""),
            "message": e.get("message", ""),
        }
        for e in root.findall(f"{{{_NS}}}event")
    ]


def parse_constraints(content: bytes) -> list[dict]:
    """Parse a constraints.xml's flat <constraint .../> list.

    Only modelName/description/time are guaranteed; the rest are omitted when absent.
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    result = []
    for c in root.findall(f"{{{_NS}}}constraint"):
        entry = {}
        for attr in _CONSTRAINT_ATTRS:
            val = c.get(attr)
            if val is None:
                continue
            entry[_CONSTRAINT_KEYS[attr]] = _to_float(val) if attr in _CONSTRAINT_FLOAT_ATTRS else val
        result.append(entry)
    return result


def parse_lost_equipments(content: bytes) -> list[dict]:
    """Parse a lostEquipments.xml's flat <lostEquipment id=".." type=".."/> list."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    return [
        {"id": e.get("id", ""), "type": e.get("type", "")}
        for e in root.findall(f"{{{_NS}}}lostEquipment")
    ]


def find_timeline_xml(output_dir: str) -> str | None:
    path = os.path.join(output_dir, "timeLine", "timeline.xml")
    return path if os.path.isfile(path) else None


def find_constraints_xml(output_dir: str) -> str | None:
    path = os.path.join(output_dir, "constraints", "constraints.xml")
    return path if os.path.isfile(path) else None


def find_dynawo_log(output_dir: str, log_filename: str = "dynawo.log") -> str | None:
    """log_filename comes from the jobs file's <logs><appender file=".."/> — it's
    only "dynawo.log" by default, the user can configure a different name."""
    path = os.path.join(output_dir, "logs", log_filename)
    return path if os.path.isfile(path) else None


def find_lost_equipments_xml(output_dir: str) -> str | None:
    path = os.path.join(output_dir, "lostEquipments", "lostEquipments.xml")
    return path if os.path.isfile(path) else None
