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

_DYN_NS = "http://www.rte-france.com/dynawo"

_ROOT_TAG_TO_TYPE: dict[str, str] = {
    f"{{{_DYN_NS}}}jobs": "jobs",
    f"{{{_DYN_NS}}}dynamicModelsArchitecture": "dyd",
    f"{{{_DYN_NS}}}parametersSet": "par",
    f"{{{_DYN_NS}}}curvesInput": "crv",
    f"{{{_DYN_NS}}}timeline": "timeline",
    f"{{{_DYN_NS}}}constraints": "constraints",
    f"{{{_DYN_NS}}}lostEquipments": "lost_equipments",
}


def detect_dynawo_type(content: bytes, filename: str | None = None) -> str | None:
    """Return 'jobs', 'dyd', 'par', 'crv', 'iidm', 'timeline', 'constraints', 'lost_equipments', 'log', or None."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return "log" if filename and filename.endswith(".log") else None

    tag = root.tag
    if tag in _ROOT_TAG_TO_TYPE:
        return _ROOT_TAG_TO_TYPE[tag]

    # IIDM uses varying namespace URIs; match on local name 'network'
    local = tag.split("}")[-1] if "}" in tag else tag
    if local == "network":
        return "iidm"

    return None
