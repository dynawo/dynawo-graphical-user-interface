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


def parse_dyd_root(content: bytes) -> ET.Element:
    """Parse a .dyd once, for callers that need more than one thing out of it.

    A .dyd of a real case is large enough that parsing it per question asked —
    its models, then its connections — is the difference between a page that
    opens and one that seems stuck."""
    return ET.fromstring(content)


def models_from_root(root: ET.Element) -> dict[str, dict]:
    """The blackBoxModels of an already parsed .dyd. See parse_dyd."""
    models: dict[str, dict] = {}
    for bbm in root.findall(f".//{{{_NS}}}blackBoxModel"):
        dyn_id = bbm.get("id")
        if dyn_id:
            models[dyn_id] = {
                "lib":       bbm.get("lib", ""),
                "dyn_id":    dyn_id,
                "static_id": bbm.get("staticId", ""),
                "parFile":   bbm.get("parFile", ""),
                "parId":     bbm.get("parId", ""),
            }
    return models


def connections_from_root(root: ET.Element) -> list[dict]:
    """The connections of an already parsed .dyd. See parse_dyd_connections."""
    return [
        {
            "id1":  c.get("id1", ""),
            "var1": c.get("var1", ""),
            "id2":  c.get("id2", ""),
            "var2": c.get("var2", ""),
        }
        for c in root.findall(f".//{{{_NS}}}connect")
    ]


def macro_connections_from_root(root: ET.Element, models: dict[str, dict]) -> list[dict]:
    """The connections a .dyd expresses through macro connectors, expanded.

    Dynawo lets a file declare a wiring once as a <macroConnector> and apply it
    with <macroConnect>; powsybl-dynawo writes the events of a security analysis
    that way, so a reader that only knows <connect> sees those models wired to
    nothing. The placeholders a macro connector may carry (@STATIC_ID@, @NAME@,
    @INDEX@) are filled from the macroConnect attributes and from the staticId
    of the models being connected — for a network event, that static id is
    precisely the equipment the event acts on.
    """
    connectors: dict[str, list[tuple[str, str]]] = {}
    for mc in root.findall(f".//{{{_NS}}}macroConnector"):
        mc_id = mc.get("id")
        if mc_id:
            connectors[mc_id] = [
                (c.get("var1", ""), c.get("var2", ""))
                for c in mc.findall(f"{{{_NS}}}connect")
            ]

    connections: list[dict] = []
    for use in root.findall(f".//{{{_NS}}}macroConnect"):
        pairs = connectors.get(use.get("connector", ""))
        if not pairs:
            continue
        id1, id2 = use.get("id1", ""), use.get("id2", "")
        static1 = models.get(id1, {}).get("static_id", "")
        static2 = models.get(id2, {}).get("static_id", "")
        for var1, var2 in pairs:
            connections.append({
                "id1":  id1,
                "var1": _fill(var1, use.get("index1"), use.get("name1"), static1 or static2),
                "id2":  id2,
                "var2": _fill(var2, use.get("index2"), use.get("name2"), static2 or static1),
            })
    return connections


def _fill(var: str, index: str | None, name: str | None, static_id: str) -> str:
    for placeholder, value in (("@INDEX@", index), ("@NAME@", name), ("@STATIC_ID@", static_id)):
        var = var.replace(placeholder, value or "")
    return var


def parse_dyd(content: bytes) -> dict[str, dict]:
    """Return {dyn_id: {lib, dyn_id, static_id, parFile, parId}} for every blackBoxModel.

    Keyed on the model's `id`, which Dynawo mandates on every blackBoxModel and
    requires to be unique inside a .dyd — unlike `staticId`, which is optional and
    only present for models bound to an IIDM network element. `id` is also the key
    the rest of the format uses: a .crv <curve model="..."> names it, and the
    parFile/parId parameter binding is carried per model. Models with no staticId
    (OmegaRef, faults, events, …) are therefore listed like any other, with
    `static_id` set to "" — only the network view, which has to place a model on
    the single-line diagram, cares about that field being non-empty.
    """
    return models_from_root(parse_dyd_root(content))


def parse_dyd_connections(content: bytes) -> list[dict]:
    """Return [{id1, var1, id2, var2}] for every <connect> of a .dyd.

    The counterpart of parse_dyd: a blackBoxModel says what a model is, a
    connect says what it acts on. Reading an events file back needs both — the
    model gives the library and its parameter set, the connect gives the object
    the event was applied to.
    """
    return connections_from_root(parse_dyd_root(content))
