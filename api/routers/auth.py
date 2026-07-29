#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from fastapi import APIRouter, Cookie
from fastapi.responses import JSONResponse

from api.session_store import store

router = APIRouter(tags=["auth"])


@router.get("/health")
def health():
    return {"router": "auth", "status": "ok"}


@router.delete("/session")
def clear_session(session_id: str = Cookie(default=None)):
    if session_id:
        store.delete(session_id)
    response = JSONResponse({"ok": True})
    response.delete_cookie("session_id")
    return response
