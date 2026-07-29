#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import gc
import multiprocessing
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.concurrency import pypowsybl_slot
from api.dependencies import get_session
from api.session_store import UserSession
from backend import loadflow_runner
from backend.powsybl_config import bootstrap_new_worker

router = APIRouter(tags=["loadflow"])

# Load-flow runs (OpenLoadFlow
# and DynaFlow) are dispatched to a small pool of persistent worker processes,
# each with its own embedded JVM. "spawn" (not the platform default "fork" on
# Linux) is required here: forking a process that already has pypowsybl's
# embedded JVM initialised is unsafe (JVM threads don't survive fork), so each
# worker must start from a clean interpreter instead of inheriting the parent's.
#
# initializer=bootstrap_new_worker, NOT a function defined in this module:
# unpickling an initializer reference in the freshly-spawned worker requires
# importing its defining module first — if that were this module, importing
# it would transitively `import pypowsybl.loadflow` (via `from backend import
# loadflow_runner` above) before the initializer body ever ran, initialising
# the JVM before it could be redirected. See backend/powsybl_config.py's
# bootstrap_new_worker docstring.
_PYPOWSYBL_WORKERS = int(os.environ.get("PYPOWSYBL_WORKERS", "2"))


def _new_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=_PYPOWSYBL_WORKERS,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=bootstrap_new_worker,
    )


_pypowsybl_pool = _new_pool()
_pool_restart_lock = threading.Lock()


def restart_pypowsybl_pool() -> None:
    """Tear down and respawn the worker pool so a newly chosen DynaFlow
    version takes effect without restarting the whole API process.

    Each worker bakes its config (including dynaflow.homeDir) at spawn time —
    see bootstrap_worker_config_dir in backend/powsybl_config.py — and
    pypowsybl's embedded JVM can't be reconfigured once initialised, so an
    already-running worker keeps using whatever version was active when it
    started. Fresh workers re-read the shared config on spawn, so replacing
    the pool is the only way to pick up the change short of a full restart.
    """
    global _pypowsybl_pool
    with _pool_restart_lock:
        old_pool = _pypowsybl_pool
        _pypowsybl_pool = _new_pool()
        old_pool.shutdown(wait=True)
        # shutdown() closes the worker queues but doesn't unlink their backing
        # semaphores — that only happens when the Queue/Lock objects are
        # actually garbage-collected. Each version switch leaves one more dead
        # pool's worth of semaphores dangling until the next GC pass, which a
        # forceful kill of the app can outrace, surfacing as resource_tracker
        # "leaked semaphore" warnings. Drop the last reference and collect
        # immediately so cleanup happens deterministically, right here, rather
        # than whenever the GC gets around to it.
        del old_pool
        gc.collect()


def shutdown_pypowsybl_pool() -> None:
    _pypowsybl_pool.shutdown(wait=True, cancel_futures=True)


@router.get("/providers")
def get_providers():
    return {
        "providers":    loadflow_runner.get_provider_names(),
        "defaults":     loadflow_runner.params_defaults(),
        "enum_options": loadflow_runner.enum_options(),
    }


@router.get("/provider-parameters/{provider}")
def get_provider_parameter_specs(provider: str):
    return loadflow_runner.provider_parameter_specs(provider)


@router.get("/result")
def get_last_result(session: UserSession = Depends(get_session)):
    if session.lf_result is None:
        raise HTTPException(status_code=404, detail="No load flow result in session")
    return session.lf_result


class LfRunRequest(BaseModel):
    provider: str = "OpenLoadFlow"
    ac: bool = True
    output_filename: str = ""
    iidm_version: str = "1.5"
    # All pypowsybl.loadflow.Parameters fields forwarded as a generic dict.
    # Unknown keys (version mismatch) are silently ignored.
    parameters: dict[str, Any] = {}
    # DynaFlow only: copy the generated .dyd/.par (and anything they
    # reference) into the session before the worker deletes its working
    # directory. Harmless no-op for other providers.
    keep_debug_files: bool = False
    # Which uploaded IIDM to run against — defaults to the currently loaded
    # network (session.network_name) when omitted, so existing callers are
    # unaffected. Lets the user target a different uploaded/result IIDM
    # (e.g. a previous load-flow output) without first loading it as "the"
    # network in Network View.
    input_filename: str | None = None


@router.post("/run")
def run_loadflow(req: LfRunRequest, session: UserSession = Depends(get_session)):
    input_name = req.input_filename or session.network_name
    if input_name is None:
        raise HTTPException(status_code=400, detail="No IIDM file selected")
    if not session.session_manager.has_file(input_name):
        raise HTTPException(status_code=400, detail=f"IIDM file not found in session: {input_name}")
    if session.uploaded_files_info.get(input_name, {}).get("ftype") != "iidm":
        raise HTTPException(status_code=400, detail=f"{input_name} is not an IIDM file")

    out_name = loadflow_runner.resolve_output_name(input_name, req.output_filename, req.provider)
    out_path = session.session_manager.get_path(out_name)
    # Worker loads its own copy from disk — session.network is never mutated
    src_path = session.session_manager.get_path(input_name)
    debug_dest_dir = (
        session.session_manager.working_dir
        if req.provider == "DynaFlow" and req.keep_debug_files
        else None
    )
    # Unlike debug_dest_dir, timeline/constraints/log are always worth keeping —
    # not gated behind "keep input files".
    outputs_dest_dir = session.session_manager.working_dir if req.provider == "DynaFlow" else None

    run_label = "DynaFlow simulation" if req.provider == "DynaFlow" else "Load flow"
    with pypowsybl_slot():
        future = _pypowsybl_pool.submit(
            loadflow_runner.run_loadflow_in_worker,
            src_path, out_path, req.ac, req.parameters, req.provider, req.iidm_version,
            debug_dest_dir, outputs_dest_dir,
        )
        try:
            serialised = future.result()
        except loadflow_runner.LoadFlowStageError as exc:
            session.lf_result = None  # don't let a stale success resurface after a real failure
            if exc.stage == "load":
                raise HTTPException(status_code=500, detail=f"Failed to load network: {exc}")
            if exc.stage == "export":
                raise HTTPException(status_code=500, detail=f"Failed to export result network: {exc}")
            raise HTTPException(status_code=500, detail=f"{run_label} failed: {exc}")

    try:
        info = session.session_manager.register_existing_file(out_name)
        # Always tag as 'iidm': detect_dynawo_type may return None for some
        # XIIDM namespace variants, but we know this file is always an IIDM.
        session.uploaded_files_info[out_name] = {"size": info.size, "ftype": "iidm"}
    except Exception:
        pass  # export succeeded; registration failure is non-fatal

    for name in serialised.get("debug_files", []):
        try:
            info = session.session_manager.register_existing_file(name)
            session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype}
        except Exception:
            pass  # best-effort — the run itself already succeeded

    session.lf_result = serialised
    return serialised


# ── Security analysis (N-1, DynaFlow only) ────────────────────────────────────

class SecurityAnalysisRunRequest(BaseModel):
    input_filename: str | None = None
    # One N-1 contingency per element (pypowsybl add_single_element_contingencies) —
    # any IIDM equipment ID: line, generator, load, transformer, etc.
    element_ids: list[str] = []
    parameters: dict[str, Any] = {}
    # DynaFlow's only security-analysis provider parameter (omitted when unset, like
    # the load-flow provider parameters, so DynaFlow falls back to its own default).
    contingencies_start_time: float | None = None
    # Same meaning as LfRunRequest.keep_debug_files: copy the generated .dyd/.par (and
    # anything they reference) into the session before the worker deletes its working
    # directory. Timeline/constraints/log/lostEquipments are captured regardless.
    keep_debug_files: bool = False


@router.get("/security-analysis/result")
def get_last_security_analysis_result(session: UserSession = Depends(get_session)):
    if session.security_analysis_result is None:
        raise HTTPException(status_code=404, detail="No security analysis result in session")
    return session.security_analysis_result


@router.post("/security-analysis/run")
def run_security_analysis(req: SecurityAnalysisRunRequest, session: UserSession = Depends(get_session)):
    input_name = req.input_filename or session.network_name
    if input_name is None:
        raise HTTPException(status_code=400, detail="No IIDM file selected")
    if not session.session_manager.has_file(input_name):
        raise HTTPException(status_code=400, detail=f"IIDM file not found in session: {input_name}")
    if session.uploaded_files_info.get(input_name, {}).get("ftype") != "iidm":
        raise HTTPException(status_code=400, detail=f"{input_name} is not an IIDM file")
    if not req.element_ids:
        raise HTTPException(status_code=400, detail="No contingency elements selected")

    src_path = session.session_manager.get_path(input_name)
    debug_dest_dir = session.session_manager.working_dir if req.keep_debug_files else None
    # Unlike debug_dest_dir, timeline/constraints/log/lostEquipments are always worth
    # keeping — not gated behind "keep input files" (same asymmetry as /run).
    outputs_dest_dir = session.session_manager.working_dir

    with pypowsybl_slot():
        future = _pypowsybl_pool.submit(
            loadflow_runner.run_security_analysis_in_worker,
            src_path, req.element_ids, req.parameters, req.contingencies_start_time,
            debug_dest_dir, outputs_dest_dir,
        )
        try:
            serialised = future.result()
        except loadflow_runner.LoadFlowStageError as exc:
            session.security_analysis_result = None
            if exc.stage == "load":
                raise HTTPException(status_code=500, detail=f"Failed to load network: {exc}")
            raise HTTPException(status_code=500, detail=f"Security analysis failed: {exc}")
        except RuntimeError as exc:
            session.security_analysis_result = None
            raise HTTPException(status_code=422, detail=str(exc))

    for name in serialised.get("debug_files", []):
        try:
            info = session.session_manager.register_existing_file(name)
            session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype}
        except Exception:
            pass  # best-effort — the run itself already succeeded

    session.security_analysis_result = serialised
    return serialised
