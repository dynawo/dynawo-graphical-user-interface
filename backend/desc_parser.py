#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import os
import xml.etree.ElementTree as ET

_NS = "http://www.rte-france.com/dynawo"


def get_lib_variables(dynawo_exe: str, lib: str) -> list[str]:
    """Return sorted variable names for a library from its ddb/<lib>.desc.xml.
    Returns [] if the executable path is unset, the file is missing, or parsing fails.
    """
    if not dynawo_exe:
        return []
    desc_path = os.path.join(os.path.dirname(dynawo_exe), "ddb", f"{lib}.desc.xml")
    if not os.path.isfile(desc_path):
        return []
    try:
        root = ET.parse(desc_path).getroot()
        return sorted(
            v.get("name")
            for v in root.findall(f".//{{{_NS}}}variable")
            if v.get("name")
        )
    except Exception:
        return []
