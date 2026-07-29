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
    """Return {staticId: {lib, dyn_id, parFile, parId}} for every blackBoxModel."""
    root = ET.fromstring(content)
    models: dict[str, dict] = {}
    for bbm in root.findall(f".//{{{_NS}}}blackBoxModel"):
        sid = bbm.get("staticId")
        if sid:
            models[sid] = {
                "lib":    bbm.get("lib", ""),
                "dyn_id": bbm.get("id", ""),
                "parFile": bbm.get("parFile", ""),
                "parId":   bbm.get("parId", ""),
            }
    return models
