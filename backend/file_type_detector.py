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
import zipfile

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

# Network formats pypowsybl imports besides XML IIDM, by file extension. Dynawo
# reads XML IIDM only, so a file in one of these is converted to .xiidm on
# upload (see backend/network_import.py). Extensions other formats share
# (.json, .txt, .bin, .zip, .xml) are told apart by content in
# network_source_format.
_NETWORK_EXT_TO_FORMAT: dict[str, str] = {
    ".raw": "PSS/E",
    ".rawx": "PSS/E",
    ".uct": "UCTE",
    ".mat": "MATPOWER",
    ".dgs": "POWER-FACTORY",
    ".jiidm": "JIIDM",
    ".biidm": "BIIDM",
}

_RDF_NS = b"http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def _is_cgmes_xml(head: bytes) -> bool:
    """A CGMES profile is an RDF/XML document: rdf:RDF root, RDF namespace."""
    return b"RDF" in head and _RDF_NS in head


def _is_cgmes_zip(content: bytes) -> bool:
    """A zip of CGMES profiles (EQ, SSH, TP, SV, boundary...) — and not a
    Dynawo case archive, which the AutoLoad ZIP flow handles instead."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if any(n.lower().endswith(".jobs") for n in names):
                return False
            for name in names:
                if name.lower().endswith(".xml"):
                    with zf.open(name) as fh:
                        if _is_cgmes_xml(fh.read(2048)):
                            return True
    except (zipfile.BadZipFile, OSError):
        pass
    return False


def network_source_format(content: bytes, filename: str | None) -> str | None:
    """The pypowsybl import format of a non-IIDM network file, or None.

    XML IIDM is not a source format: it is used as is, as type 'iidm'.
    """
    if not filename:
        return None
    ext = os.path.splitext(filename)[1].lower()
    if ext in _NETWORK_EXT_TO_FORMAT:
        return _NETWORK_EXT_TO_FORMAT[ext]
    head = content[:4096]
    if ext == ".zip":
        return "CGMES" if _is_cgmes_zip(content) else None
    if ext == ".xml":
        return "CGMES" if _is_cgmes_xml(head) else None
    if ext == ".json":
        return "JIIDM" if b'"caseDate"' in head and b'"version"' in head else None
    if ext == ".bin":
        return "BIIDM" if b"Binary IIDM" in content[:64] else None
    if ext == ".txt":
        return "IEEE-CDF" if b"BUS DATA FOLLOWS" in head else None
    return None


def detect_dynawo_type(content: bytes, filename: str | None = None) -> str | None:
    """Return 'jobs', 'dyd', 'par', 'crv', 'iidm', 'network_source', 'timeline',
    'constraints', 'lost_equipments', 'log', or None.

    'network_source' is a network in a format other than XML IIDM (needs the
    filename, see network_source_format).
    """
    if network_source_format(content, filename):
        return "network_source"
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
