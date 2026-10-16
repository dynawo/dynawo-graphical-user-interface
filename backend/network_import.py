#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Conversion of networks in other formats than XML IIDM to .xiidm.

Dynawo reads XML IIDM only (the jobs file's <network iidmFile=...>), so a
network uploaded in another format pypowsybl imports — CGMES, PSS/E, UCTE,
MATPOWER... — is converted once, on upload, to a sibling .xiidm which every
page then uses like any other IIDM. The original is kept, so it can be imported
again with other parameters.
"""

import os
from typing import Any, Callable

import pypowsybl.network as pn

from backend.powsybl_params import parse_bracket_list, parse_default, serialize_param

# The IIDM version written — the one Dynawo reads, as Load Flow's default.
IIDM_VERSION = "1.5"

# Import parameters left out of the GUI: paths on the server, internal
# plumbing, and options for updating an already imported network.
_HIDDEN_PARAMETERS = {
    "iidm.import.cgmes.boundary-location",
    "iidm.import.cgmes.powsybl-triplestore",
    "iidm.import.cgmes.store-cgmes-conversion-context-as-network-extension",
    "iidm.import.cgmes.store-cgmes-model-as-network-extension",
    "iidm.import.cgmes.use-previous-values-during-update",
    "iidm.import.cgmes.post-processors",
}

# CGMES has some 25 parameters. These change what a Dynawo user gets — the IIDM
# ids the .dyd/.par must match, how boundaries are modelled, the topology, how
# a merged model is split — and are shown first; the rest go under Advanced.
_CGMES_MAIN_PARAMETERS = {
    "iidm.import.cgmes.source-for-iidm-id",
    "iidm.import.cgmes.naming-strategy",
    "iidm.import.cgmes.convert-boundary",
    "iidm.import.cgmes.import-node-breaker-as-bus-breaker",
    "iidm.import.cgmes.cgm-with-subnetworks",
    "iidm.import.cgmes.cgm-with-subnetworks-defined-by",
}

# A CGMES model referring to the ENTSO-E boundary set fails with this when the
# boundary profiles are not uploaded with it.
_CGMES_BOUNDARY_HINT = (
    "If this model refers to the ENTSO-E boundary set, add the boundary "
    "profiles (EQ_BD, TP_BD) to the zip next to EQ/SSH/TP/SV."
)


def import_parameter_specs(fmt: str) -> list[dict]:
    """The import parameters of a format the GUI offers, as form specs.

    category is "" for the main ones, "Advanced" for the others.
    """
    df = pn.get_import_parameters(fmt)
    specs = []
    for name, row in df.iterrows():
        if name in _HIDDEN_PARAMETERS:
            continue
        ptype = row.get("type") or "STRING"
        advanced = fmt == "CGMES" and name not in _CGMES_MAIN_PARAMETERS
        specs.append({
            "name": name,
            "category": "Advanced" if advanced else "",
            "description": row.get("description") or "",
            "type": ptype,
            "default": parse_default(row.get("default", ""), ptype),
            "possible_values": parse_bracket_list(row.get("possible_values", "")),
        })
    return specs


def _serialize_parameters(fmt: str, values: dict[str, Any]) -> dict[str, str]:
    """UI values to pypowsybl's strings, dropping unknown and hidden names."""
    types = {s["name"]: s["type"] for s in import_parameter_specs(fmt)}
    out = {}
    for name, val in values.items():
        if name in types:
            s = serialize_param(val, types[name])
            if s is not None:
                out[name] = s
    return out


def converted_name(source_name: str, taken: Callable[[str], bool]) -> str:
    """The .xiidm name for a converted source, next to it: <stem>.xiidm, or
    <stem>_converted[_N].xiidm when a file of that name is already there."""
    stem = os.path.splitext(source_name)[0]
    candidate = f"{stem}.xiidm"
    n = 1
    while taken(candidate):
        candidate = f"{stem}_converted.xiidm" if n == 1 else f"{stem}_converted_{n}.xiidm"
        n += 1
    return candidate


def convert_to_xiidm(src_path: str, out_path: str, fmt: str,
                     parameters: dict[str, Any] | None = None) -> None:
    """Import src_path with pypowsybl and write it to out_path as XIIDM."""
    net = pn.load(src_path, parameters=_serialize_parameters(fmt, parameters or {}))
    net.dump(out_path, format="XIIDM", parameters={"iidm.export.xml.version": IIDM_VERSION})


def import_error_message(exc: Exception, fmt: str) -> str:
    """pypowsybl's error, short enough to show, with a hint where one helps."""
    msg = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if len(msg) > 400:
        msg = msg[:400] + "…"
    if fmt == "CGMES" and ("not found" in msg or "boundary" in msg.lower()):
        msg = f"{msg} — {_CGMES_BOUNDARY_HINT}"
    return msg
