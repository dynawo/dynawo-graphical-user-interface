#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Helpers for pypowsybl's parameter tables (load-flow provider parameters,
network import parameters): its spec strings to native values and back."""

from typing import Any


def parse_bracket_list(s: str) -> list[str]:
    """Parse pypowsybl's "[A, B, C]" spec-string format into a list."""
    s = (s or "").strip()
    if not (s.startswith("[") and s.endswith("]")):
        return []
    inner = s[1:-1].strip()
    return [v.strip() for v in inner.split(",")] if inner else []


def parse_default(raw: str, ptype: str):
    """Parse a pypowsybl parameter default into a JSON-friendly native type."""
    raw = raw or ""
    if ptype == "BOOLEAN":
        return raw.strip().lower() == "true"
    if ptype == "STRING_LIST":
        return parse_bracket_list(raw)
    if ptype in ("DOUBLE", "INTEGER"):
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        if val != val:  # NaN
            return None
        return int(val) if ptype == "INTEGER" else val
    return raw


def serialize_param(val: Any, ptype: str) -> str | None:
    """Convert a native-typed UI value back into the string wire format
    pypowsybl expects for a provider or import parameter.

    Returns None when the value should be omitted entirely (unset/empty)
    rather than sent as an empty string: some providers (e.g. DynaFlow's
    `precision`, whose default is NaN) try to parse the string as a number
    on the Java side and raise NumberFormatException: empty String if given "".
    Omitting the key lets the provider fall back to its own internal default.
    """
    if ptype == "BOOLEAN":
        return "true" if val else "false"
    if ptype == "STRING_LIST":
        if isinstance(val, (list, tuple)):
            return ",".join(str(v) for v in val) if val else None
        return None if val in (None, "") else str(val)
    if val is None or val == "":
        return None
    return str(val)
