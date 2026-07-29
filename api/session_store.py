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
import threading
import time
import uuid
from dataclasses import dataclass, field

from backend.models import ChangeLogEntry, CrvChangeLogEntry, RunRecord
from backend.session_manager import SessionDir, SessionManager
from backend.user_config import get_default_executable


@dataclass
class UserSession:
    session_id: str
    user: str
    session_dir: SessionDir
    session_manager: SessionManager

    # Network (pypowsybl object — not serializable, stays in RAM)
    network: object | None = None
    network_name: str | None = None

    # Dynawo executable
    dynawo_executable: str | None = None

    # Uploaded files metadata (mirrors st.session_state["uploaded_files_info"])
    uploaded_files_info: dict = field(default_factory=dict)

    # Simulation runs history
    sim_runs: list[RunRecord] = field(default_factory=list)

    # Monotonic counter for run ids — never reused, even after a run is deleted,
    # so caches keyed by run_id (curves, NAD/SLD) can't serve stale data for a
    # different run that happens to land on a recycled id.
    next_run_id: int = 1

    # Per-user simulation lock (prevents concurrent runs for the same session)
    sim_lock: threading.Lock = field(default_factory=threading.Lock)

    # Active simulation runner (one at a time per session)
    active_runner: object | None = None

    # Active download (Dynawo version)
    active_download: object | None = None
    active_download_target: tuple | None = None

    # Active download (DynaFlow-launcher version)
    active_dynaflow_download: object | None = None
    active_dynaflow_download_target: tuple | None = None

    # NAD SVG cache — keyed by cache_key (network_name or "run_{id}")
    nad_cache: dict[str, str] = field(default_factory=dict)

    # SLD cache — keyed by vl_id, "run_{id}:{vl_id}" or "file_{name}:{vl_id}"
    sld_cache: dict[str, dict] = field(default_factory=dict)

    # Diff NAD/SLD caches — keyed by compare-source id plus a params digest
    diff_nad_cache: dict[str, str] = field(default_factory=dict)
    diff_sld_cache: dict[str, dict] = field(default_factory=dict)

    # Last load flow result (for re-display on page revisit)
    lf_result: dict | None = None

    # Last security analysis result (for re-display on page revisit)
    security_analysis_result: dict | None = None

    # Parameter change logs
    par_change_log: list[ChangeLogEntry] = field(default_factory=list)
    solver_change_log: list[ChangeLogEntry] = field(default_factory=list)
    crv_change_log: list[CrvChangeLogEntry] = field(default_factory=list)

    last_active: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._sessions: dict[str, UserSession] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self, user: str) -> UserSession:
        session_dir = SessionDir()
        session_manager = SessionManager(session_dir.path)
        exe = get_default_executable() or None
        session = UserSession(
            session_id=str(uuid.uuid4()),
            user=user,
            session_dir=session_dir,
            session_manager=session_manager,
            dynawo_executable=exe,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> UserSession | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_active = time.time()
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.session_dir.cleanup()

    def evict_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [s for s in self._sessions.values() if now - s.last_active > self._ttl]
            for s in expired:
                del self._sessions[s.session_id]
        for s in expired:
            s.session_dir.cleanup()
        return len(expired)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


store = SessionStore(ttl_seconds=int(os.environ.get("SESSION_TTL", "3600")))
