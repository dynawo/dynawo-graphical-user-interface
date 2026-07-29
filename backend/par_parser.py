#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import xml.etree.ElementTree as ET

_NS = "http://www.rte-france.com/dynawo"


def _parse_pars_refs(elem) -> tuple[list[dict], list[dict]]:
    pars = [
        {"name": p.get("name", ""), "type": p.get("type", ""), "value": p.get("value", "")}
        for p in elem.findall(f"{{{_NS}}}par")
    ]
    refs = [
        {"name": r.get("name", ""), "origData": r.get("origData", ""), "origName": r.get("origName", "")}
        for r in elem.findall(f"{{{_NS}}}reference")
    ]
    return pars, refs


def parse_par(content: bytes) -> dict[str, dict]:
    """Parse a .par file and return {setId: {pars, refs, macro_id}}.

    pars : [{"name": ..., "type": ..., "value": ..., "shared": bool}]
    refs : [{"name": ..., "origData": ..., "origName": ..., "shared": bool}]
    macro_id : the <macroParSet> this set references, or None.

    Resolves <macroParSet id="X"/> references inside a <set> against the
    matching <macroParameterSet id="X"> block defined elsewhere in the same
    file — common in DynaFlow-generated .par files, where many <set>s share
    one macro definition instead of repeating the same pars/refs. A <set>'s
    own direct <par>/<reference> entries (if any) override same-named ones
    inherited from its macro, mirroring how Dynawo treats macroParSet as
    providing defaults the set can override.

    `shared=True` marks entries that come from the macro (editing them is
    expected to edit the macro itself — see backend/par_writer.py — so the
    UI can warn this also affects every other <set> referencing the same
    macro; see macro_usage() below to find them).
    """
    root = ET.fromstring(content)

    macro_sets: dict[str, tuple[list[dict], list[dict]]] = {
        m.get("id", ""): _parse_pars_refs(m)
        for m in root.findall(f"{{{_NS}}}macroParameterSet")
    }

    sets: dict[str, dict] = {}
    for s in root.findall(f"{{{_NS}}}set"):
        sid = s.get("id", "")
        pars_by_name: dict[str, dict] = {}
        refs_by_name: dict[str, dict] = {}

        macro_ref = s.find(f"{{{_NS}}}macroParSet")
        macro_id = macro_ref.get("id") if macro_ref is not None else None
        if macro_id is not None:
            macro_pars, macro_refs = macro_sets.get(macro_id, ([], []))
            for p in macro_pars:
                pars_by_name[p["name"]] = {**p, "shared": True}
            for r in macro_refs:
                refs_by_name[r["name"]] = {**r, "shared": True}

        own_pars, own_refs = _parse_pars_refs(s)
        for p in own_pars:
            pars_by_name[p["name"]] = {**p, "shared": False}
        for r in own_refs:
            refs_by_name[r["name"]] = {**r, "shared": False}

        sets[sid] = {
            "pars": list(pars_by_name.values()),
            "refs": list(refs_by_name.values()),
            "macro_id": macro_id,
        }
    return sets


def macro_usage(content: bytes) -> dict[str, list[str]]:
    """Return {macro_id: [set_id, ...]} — every <set> referencing each
    <macroParameterSet> via <macroParSet>. Used to tell a user editing a
    shared parameter which other sets (and therefore which other dynamic
    models) the edit will also affect.
    """
    root = ET.fromstring(content)
    usage: dict[str, list[str]] = {}
    for s in root.findall(f"{{{_NS}}}set"):
        macro_ref = s.find(f"{{{_NS}}}macroParSet")
        if macro_ref is not None:
            usage.setdefault(macro_ref.get("id", ""), []).append(s.get("id", ""))
    return usage
