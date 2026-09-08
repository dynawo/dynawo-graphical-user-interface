#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import io
import os
import xml.etree.ElementTree as ET

from backend.models import StagedEvent

_NS = "http://www.rte-france.com/dynawo"


def par_filename_for(dyd_filename: str) -> str:
    """The .par that goes with a .dyd: the same name, the other extension.

    The user names the .dyd; the .par is never chosen separately because the two
    are written together and only ever reference each other."""
    return f"{os.path.splitext(dyd_filename)[0]}.par"


def _parse(content: bytes) -> ET.Element:
    """Parse keeping comments, so rewriting a file a human wrote does not strip them."""
    return ET.fromstring(content, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))


def _serialize(root: ET.Element) -> bytes:
    ET.indent(root, space="  ")
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, xml_declaration=True, encoding="UTF-8")
    return buf.getvalue()


def build_events_dyd(
    events: list[StagedEvent],
    par_filename: str,
    existing: bytes | None = None,
    event_libs: set[str] | None = None,
) -> tuple[bytes, set[str]]:
    """The .dyd for `events`, merged into `existing` when the file already exists.

    Merged rather than rebuilt because an events file is not always a file of
    events only: a .dyd describing the network can declare a couple of events
    among its models, and saving from this page must not cost the user the rest
    of it. What is replaced is exactly what this page manages — the models whose
    library the catalogue offers, and the connections that belong to them.
    Everything else, comments included, is left where it is.

    Returns the bytes and the parameter set ids the removed event models used, so
    their sets can be dropped from the .par in the same pass.
    """
    if existing is None:
        ET.register_namespace("dyn", _NS)
        root = ET.Element(f"{{{_NS}}}dynamicModelsArchitecture")
        dropped_par_ids: set[str] = set()
    else:
        root = _parse(existing)
        root, dropped_par_ids = _strip_events(root, event_libs or set())

    for ev in events:
        bbm = ET.SubElement(root, f"{{{_NS}}}blackBoxModel")
        bbm.set("id", ev.model_id)
        bbm.set("lib", ev.lib)
        bbm.set("parFile", par_filename)
        bbm.set("parId", ev.model_id)
    for ev in events:
        for c in ev.connections:
            conn = ET.SubElement(root, f"{{{_NS}}}connect")
            conn.set("id1", ev.model_id)
            conn.set("var1", c.var1)
            conn.set("id2", c.id2)
            conn.set("var2", c.var2)

    ET.register_namespace("dyn", _NS)
    return _serialize(root), dropped_par_ids


def _strip_events(root: ET.Element, event_libs: set[str]) -> tuple[ET.Element, set[str]]:
    """Remove the event models of a .dyd and the connections wiring them."""
    event_ids: set[str] = set()
    dropped_par_ids: set[str] = set()
    for bbm in root.findall(f"{{{_NS}}}blackBoxModel"):
        if bbm.get("lib") in event_libs:
            event_ids.add(bbm.get("id", ""))
            if bbm.get("parId"):
                dropped_par_ids.add(bbm.get("parId"))
            root.remove(bbm)
    for conn in root.findall(f"{{{_NS}}}connect"):
        if conn.get("id1") in event_ids or conn.get("id2") in event_ids:
            root.remove(conn)
    return root, dropped_par_ids


def build_events_par(
    events: list[StagedEvent],
    existing: bytes | None = None,
    drop_set_ids: set[str] | None = None,
) -> bytes:
    """The .par holding one <set> per event, merged into `existing` when there is one.

    Same reasoning as the .dyd: the file may also hold the parameter sets of the
    models the network is made of, and only the sets of the events this page
    wrote are replaced."""
    if existing is None:
        ET.register_namespace("", _NS)
        root = ET.Element(f"{{{_NS}}}parametersSet")
    else:
        root = _parse(existing)
        stale = (drop_set_ids or set()) | {ev.model_id for ev in events}
        for par_set in root.findall(f"{{{_NS}}}set"):
            if par_set.get("id") in stale:
                root.remove(par_set)

    for ev in events:
        par_set = ET.SubElement(root, f"{{{_NS}}}set")
        par_set.set("id", ev.model_id)
        for p in ev.parameters:
            par = ET.SubElement(par_set, f"{{{_NS}}}par")
            par.set("type", p.type)
            par.set("name", p.name)
            par.set("value", p.value)

    ET.register_namespace("", _NS)
    return _serialize(root)
