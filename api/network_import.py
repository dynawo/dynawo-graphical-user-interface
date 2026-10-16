#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Session side of converting uploaded networks to XIIDM (backend/network_import.py).

The link between a source and its .xiidm lives in uploaded_files_info: the
source's entry carries source_format, converted_to and, after a failed import,
import_error; the .xiidm's carries converted_from.
"""

import os

from fastapi import HTTPException

from api.concurrency import pypowsybl_slot
from api.session_store import UserSession
from backend.file_type_detector import network_source_format
from backend.network_import import convert_to_xiidm, converted_name, import_error_message
from backend.user_config import get_network_import_parameters


def convert_network_source(session: UserSession, source_name: str,
                           parameters: dict | None = None) -> dict:
    """Convert one uploaded network source to XIIDM and record the link.

    parameters defaults to the ones saved for the source's format. A source
    already converted is converted again into the same .xiidm. Never raises:
    a failed import is recorded on the source as import_error, and leaves an
    earlier conversion in place. Returns the source's metadata.
    """
    sm = session.session_manager
    meta = session.uploaded_files_info[source_name]
    fmt = network_source_format(sm.get_raw(source_name) or b"", source_name)
    meta["source_format"] = fmt
    if parameters is None:
        parameters = get_network_import_parameters(fmt)

    out_name = meta.get("converted_to") or converted_name(source_name, sm.has_file)
    out_path = sm.get_path(out_name)
    try:
        with pypowsybl_slot():
            convert_to_xiidm(sm.get_path(source_name), out_path, fmt, parameters)
    except HTTPException as exc:  # pypowsybl_slot saturated
        meta["import_error"] = str(exc.detail)
        return meta
    except Exception as exc:
        if not sm.has_file(out_name) and os.path.isfile(out_path):
            os.remove(out_path)  # half-written by a failed dump
        meta["import_error"] = import_error_message(exc, fmt)
        return meta

    info = sm.register_existing_file(out_name)
    session.uploaded_files_info[out_name] = {
        "size": info.size, "ftype": "iidm", "converted_from": source_name,
    }
    meta["converted_to"] = out_name
    meta.pop("import_error", None)
    if session.network_name == out_name:
        session.network = None
        session.network_name = None
    return meta


def unlink_conversion(session: UserSession, name: str) -> None:
    """Forget the source/.xiidm link a file is part of, before it is deleted
    or replaced: the other file stays, as an independent one."""
    meta = session.uploaded_files_info.get(name, {})
    other = session.uploaded_files_info.get(meta.get("converted_from") or meta.get("converted_to") or "")
    if other is None:
        return
    if other.get("converted_to") == name:
        other.pop("converted_to", None)
    if other.get("converted_from") == name:
        other.pop("converted_from", None)
