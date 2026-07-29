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

# Must run before any import that pulls in pypowsybl: the GraalVM native
# image reads the powsybl.config.dirs system property at init time.
from backend.powsybl_config import bootstrap_powsybl_config_dir
bootstrap_powsybl_config_dir()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import admin, auth, autoload, curves, dynaflow_launcher, dynawo_version, files, loadflow, network, parameters, simulation, solver
from api.session_store import store

app = FastAPI(title="Dynawo GUI API", version="0.1.0")


def _eviction_loop(interval: int = 300) -> None:
    while True:
        threading.Event().wait(interval)
        evicted = store.evict_expired()
        if evicted:
            print(f"[session] evicted {evicted} idle session(s)")

threading.Thread(target=_eviction_loop, daemon=True).start()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router,            prefix="/api/admin")
app.include_router(autoload.router,         prefix="/api/autoload")
app.include_router(auth.router,             prefix="/api/auth")
app.include_router(dynawo_version.router,   prefix="/api/dynawo")
app.include_router(dynaflow_launcher.router, prefix="/api/dynaflow-launcher")
app.include_router(files.router,            prefix="/api/files")
app.include_router(network.router,          prefix="/api/network")
app.include_router(loadflow.router,         prefix="/api/loadflow")
app.include_router(curves.router,           prefix="/api/curves")
app.include_router(parameters.router,       prefix="/api/parameters")
app.include_router(simulation.router,       prefix="/api/simulation")
app.include_router(solver.router,           prefix="/api/solver")


@app.on_event("shutdown")
def _shutdown_pypowsybl_pool():
    # wait=True: blocks until worker processes have actually exited, not just
    # been signalled to. Needed for two things to complete before this
    # process itself exits: (1) closing the semaphores their internal work
    # queue uses, which is what the resource_tracker "leaked semaphore
    # objects" warning on uvicorn shutdown was about; (2) each worker's own
    # atexit hook (registered in backend.powsybl_config.bootstrap_new_worker)
    # that deletes its private tmp-dir — with wait=False this handler could
    # return, and uvicorn proceed to exit, before a worker had actually run
    # that hook, leaking the directory (confirmed empirically).
    loadflow.shutdown_pypowsybl_pool()


@app.get("/api/health")
def health():
    return {"status": "ok"}
