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
import xml.etree.ElementTree as ET

_NS = "http://www.rte-france.com/dynawo"


def build_crv_bytes(curves: list[dict]) -> bytes:
    """Return CRV XML as bytes without touching the filesystem."""
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}curvesInput")
    for c in curves:
        el = ET.SubElement(root, f"{{{_NS}}}curve")
        el.set("model", c["model"])
        el.set("variable", c["variable"])
    ET.indent(root, space="  ")
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, xml_declaration=True, encoding="UTF-8")
    return buf.getvalue()


def write_crv(crv_path: str, curves: list[dict]) -> None:
    """Overwrite crv_path with the given list of {model, variable} dicts."""
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}curvesInput")
    for c in curves:
        el = ET.SubElement(root, f"{{{_NS}}}curve")
        el.set("model", c["model"])
        el.set("variable", c["variable"])
    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    with open(crv_path, "wb") as fh:
        tree.write(fh, xml_declaration=True, encoding="UTF-8")
