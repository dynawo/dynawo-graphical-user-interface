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
import time

from fastapi import APIRouter, Header, HTTPException

from api.session_store import store

router = APIRouter(tags=["admin"])

_ADMIN_KEY = os.environ.get("ADMIN_KEY", "")


def _check_auth(x_admin_key: str | None) -> None:
    if _ADMIN_KEY and x_admin_key != _ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing X-Admin-Key header")


@router.get("/sessions")
def list_sessions(x_admin_key: str | None = Header(default=None)):
    _check_auth(x_admin_key)
    now = time.time()
    with store._lock:
        sessions = list(store._sessions.values())

    return {
        "active_sessions": len(sessions),
        "sessions": [
            {
                "session_id": s.session_id[:8] + "…",
                "idle_seconds": int(now - s.last_active),
                "files": len(s.session_manager.files),
                "network_loaded": s.network is not None,
                "sim_running": s.active_runner is not None and s.active_runner.is_running(),
                "sim_runs": len(s.sim_runs),
            }
            for s in sessions
        ],
    }
