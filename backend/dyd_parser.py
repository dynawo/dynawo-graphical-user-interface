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
    root = ET.fromstring(content)
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
