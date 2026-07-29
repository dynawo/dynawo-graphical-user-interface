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


def parse_crv(raw: bytes) -> list[dict]:
    """Parse .crv XML bytes → [{"model": str, "variable": str}, …]. Returns [] on error."""
    try:
        root = ET.fromstring(raw)
        return [
            {"model": c.get("model", ""), "variable": c.get("variable", "")}
            for c in root.findall(f".//{{{_NS}}}curve")
            if c.get("model") and c.get("variable")
        ]
    except Exception:
        return []
