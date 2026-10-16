#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Import parameters of network formats, and importing a source again with them."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_session
from api.network_import import convert_network_source
from api.session_store import UserSession
from backend.network_import import import_parameter_specs
from backend.user_config import get_network_import_parameters, save_network_import_parameters

router = APIRouter(tags=["network-import"])


@router.get("/parameters")
def get_import_parameters(format: str):
    """The import parameters offered for a format (as Load Flow's provider
    parameters: name, category, description, type, default, possible_values),
    and the values saved for it. format is a query parameter: "PSS/E" has a slash."""
    try:
        specs = import_parameter_specs(format)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unknown network format {format!r}: {exc}")
    return {"format": format, "specs": specs, "saved": get_network_import_parameters(format)}


class ReimportRequest(BaseModel):
    filename: str
    # Only the parameters differing from pypowsybl's defaults.
    parameters: dict = {}
    # Keep these parameters for the next files of this format.
    save: bool = False


@router.post("/reimport")
def reimport(req: ReimportRequest, session: UserSession = Depends(get_session)):
    """Convert an uploaded network source again, with the given parameters,
    into the same .xiidm. An import error comes back as import_error, not as
    an HTTP error, so the dialog can show it next to the parameters."""
    meta = session.uploaded_files_info.get(req.filename)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"{req.filename} not found in session")
    if meta.get("ftype") != "network_source":
        raise HTTPException(status_code=400, detail=f"{req.filename} is not a network to import")
    meta = convert_network_source(session, req.filename, req.parameters)
    if req.save and not meta.get("import_error"):
        save_network_import_parameters(meta["source_format"], req.parameters)
    return {"name": req.filename, **meta}
