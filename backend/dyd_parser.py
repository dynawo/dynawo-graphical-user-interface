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
