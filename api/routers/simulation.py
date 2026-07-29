#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import asyncio
import json
import math
import os
import re
import shutil
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.dependencies import get_session
from api.session_store import UserSession
from backend.curves_loader import load_curves_csv, read_crv_info
from backend.jobs_parser import (
    find_final_state_iidm,
    read_crv_input_file,
    read_final_state_info,
    read_log_filename,
    read_network_iidm,
    read_simulation_config,
    write_network_iidm,
    write_simulation_config,
)
from backend.models import RunRecord, SimulationConfig
from backend.output_parser import (
    find_constraints_xml,
    find_dynawo_log,
    find_lost_equipments_xml,
    find_timeline_xml,
    parse_constraints,
    parse_lost_equipments,
    parse_timeline,
)
from backend.simulation_runner import SimulationRunner

router = APIRouter(tags=["simulation"])

_MAX_POINTS = 5000
_MAX_CONCURRENT_SIMS = int(os.environ.get("MAX_CONCURRENT_SIMS", "4"))
_global_sim_semaphore = threading.Semaphore(_MAX_CONCURRENT_SIMS)
_STANDALONE = os.environ.get("STANDALONE", "0").lower() in ("1", "true", "yes")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _jobs_path(session: UserSession, jobs_file: str) -> str:
    if not session.session_manager.has_file(jobs_file):
        raise HTTPException(status_code=404, detail=f"{jobs_file} not found in session")
    return session.session_manager.get_path(jobs_file)


def _get_run(session: UserSession, run_id: int) -> RunRecord:
    for r in session.sim_runs:
        if r.run_id == run_id:
            return r
    raise HTTPException(status_code=404, detail=f"Run {run_id} not found")


def _next_run_id(session: UserSession) -> int:
    """A fresh id, never reused — deleting a run must not free its id for reuse,
    since stale per-run caches (curves, NAD/SLD) are keyed by run_id."""
    run_id = session.next_run_id
    session.next_run_id += 1
    return run_id


def _run_log_filename(session: UserSession, run: RunRecord) -> str:
    """The jobs file can point its <logs> appender at a name other than
    "dynawo.log" — resolve the actual one configured for this run."""
    if not session.session_manager.has_file(run.jobs_file):
        return "dynawo.log"
    return read_log_filename(session.session_manager.get_path(run.jobs_file))


def _sample(series: list, max_pts: int) -> list:
    if len(series) <= max_pts:
        return series
    step = len(series) / max_pts
    return [series[int(i * step)] for i in range(max_pts)]


def _clean(v: float) -> float | None:
    """Return None for NaN/Inf so JSON serialisation doesn't crash."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


# ── Config endpoints ───────────────────────────────────────────────────────────

@router.get("/config/{jobs_file:path}")
def get_config(jobs_file: str, session: UserSession = Depends(get_session)):
    path = _jobs_path(session, jobs_file)
    cfg = read_simulation_config(path)
    return {
        "start_time": cfg.start_time,
        "stop_time": cfg.stop_time,
        "output_dir": cfg.output_dir,
        "iidm_file": read_network_iidm(path),
    }


class ConfigRequest(BaseModel):
    jobs_file: str
    start_time: float
    stop_time: float
    output_dir: str


@router.put("/config")
def put_config(req: ConfigRequest, session: UserSession = Depends(get_session)):
    if req.stop_time <= req.start_time:
        raise HTTPException(status_code=422, detail="stop_time must be greater than start_time")
    path = _jobs_path(session, req.jobs_file)
    write_simulation_config(path, SimulationConfig(
        start_time=req.start_time,
        stop_time=req.stop_time,
        output_dir=req.output_dir,
    ))
    return {"ok": True}


# ── Run management ────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    jobs_file: str
    start_time: float
    stop_time: float
    base_output_dir: str = "outputs"
    iidm_override: str | None = None


@router.post("/run")
def start_run(req: RunRequest, session: UserSession = Depends(get_session)):
    if req.stop_time <= req.start_time:
        raise HTTPException(status_code=422, detail="stop_time must be greater than start_time")
    if session.dynawo_executable is None:
        raise HTTPException(status_code=422, detail="No Dynawo executable configured")
    if os.path.isabs(req.base_output_dir.strip()) and not _STANDALONE:
        raise HTTPException(
            status_code=422,
            detail="Absolute output paths are not allowed on a shared server. Use a relative path (e.g. 'outputs').",
        )

    # Per-user lock: prevents race condition when the same session fires two requests
    if not session.sim_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A simulation is already running for this session")

    # Global semaphore: caps total concurrent Dynawo processes across all users
    if not _global_sim_semaphore.acquire(blocking=False):
        session.sim_lock.release()
        raise HTTPException(
            status_code=503,
            detail=f"Server is at capacity ({_MAX_CONCURRENT_SIMS} simultaneous simulations). Try again shortly.",
        )

    jobs_path = _jobs_path(session, req.jobs_file)
    run_id = _next_run_id(session)
    base = req.base_output_dir.strip() or "outputs"
    output_dir = f"{base}_run{run_id}"

    write_simulation_config(jobs_path, SimulationConfig(
        start_time=req.start_time,
        stop_time=req.stop_time,
        output_dir=output_dir,
    ))

    if req.iidm_override:
        iidm_path = session.session_manager.get_path(req.iidm_override)
        if not os.path.isfile(iidm_path):
            session.sim_lock.release()
            _global_sim_semaphore.release()
            raise HTTPException(status_code=400, detail=f"IIDM file not found in session: {req.iidm_override}")
        try:
            write_network_iidm(jobs_path, req.iidm_override)
        except Exception as exc:
            session.sim_lock.release()
            _global_sim_semaphore.release()
            raise HTTPException(status_code=500, detail=f"Failed to patch IIDM reference: {exc}")

    fsi = read_final_state_info(jobs_path)

    exe = session.dynawo_executable
    cmd = [exe, "jobs", jobs_path]
    dynawo_dir = os.path.dirname(os.path.abspath(exe))
    env = {
        **os.environ,
        "PATH": os.path.join(dynawo_dir, "bin") + os.pathsep + os.environ.get("PATH", ""),
        "DYNAWO_HOME": dynawo_dir,
    }
    working_dir = session.session_manager.working_dir

    runner = SimulationRunner()
    session.active_runner = runner
    session.sim_runs.append(RunRecord(
        run_id=run_id,
        label=f"Run {run_id}",
        jobs_file=req.jobs_file,
        output_dir=output_dir,
        returncode=None,
        output="",
        start_time=req.start_time,
        stop_time=req.stop_time,
        exports_final_iidm=fsi["exports_iidm"],
        started_at=time.time(),
    ))

    def on_done(result):
        run = _get_run(session, run_id)
        run.returncode = result.returncode
        run.output = result.output
        run.finished_at = time.time()
        _global_sim_semaphore.release()
        session.sim_lock.release()

    runner.start(cmd, cwd=working_dir, on_done=on_done, env=env)
    return {"run_id": run_id, "output_dir": output_dir}


@router.get("/{run_id}/output")
async def stream_output(run_id: int, session: UserSession = Depends(get_session)):
    _get_run(session, run_id)  # 404 if not found

    async def gen():
        runner = session.active_runner
        if runner is None:
            run = _get_run(session, run_id)
            if run.output:
                yield f"data: {json.dumps({'line': run.output})}\n\n"
            yield f"data: {json.dumps({'done': True, 'returncode': run.returncode})}\n\n"
            return

        while runner.is_running():
            for line in runner.flush_new_lines():
                yield f"data: {json.dumps({'line': line})}\n\n"
            await asyncio.sleep(0.2)

        # Drain any remaining lines after process exits
        for line in runner.flush_new_lines():
            yield f"data: {json.dumps({'line': line})}\n\n"

        run = _get_run(session, run_id)
        yield f"data: {json.dumps({'done': True, 'returncode': run.returncode})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs")
def list_runs(session: UserSession = Depends(get_session)):
    working_dir = session.session_manager.working_dir
    return [
        {
            "run_id":               r.run_id,
            "label":                r.label,
            "jobs_file":            r.jobs_file,
            "output_dir":           r.output_dir,
            "returncode":           r.returncode,
            "start_time":           r.start_time,
            "stop_time":            r.stop_time,
            "started_at":           r.started_at,
            "finished_at":          r.finished_at,
            "has_output":           bool(r.output),
            "exports_final_iidm":   r.exports_final_iidm,
            "has_final_state_iidm": find_final_state_iidm(working_dir, r.output_dir) is not None,
            "has_timeline":         find_timeline_xml(os.path.join(working_dir, r.output_dir)) is not None,
            "has_constraints":      find_constraints_xml(os.path.join(working_dir, r.output_dir)) is not None,
            "has_log":              find_dynawo_log(
                                        os.path.join(working_dir, r.output_dir), _run_log_filename(session, r),
                                    ) is not None,
            "has_lost_equipments":  find_lost_equipments_xml(os.path.join(working_dir, r.output_dir)) is not None,
        }
        for r in reversed(session.sim_runs)
    ]


class LabelRequest(BaseModel):
    label: str


@router.put("/{run_id}/label")
def rename_run(run_id: int, req: LabelRequest, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    if not req.label.strip():
        raise HTTPException(status_code=422, detail="Label cannot be empty")
    run.label = req.label.strip()
    return {"ok": True}


@router.delete("/{run_id}")
def delete_run(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    working_dir = session.session_manager.working_dir
    out_path = os.path.join(working_dir, run.output_dir)
    if os.path.isdir(out_path):
        shutil.rmtree(out_path)
    session.sim_runs = [r for r in session.sim_runs if r.run_id != run_id]
    return {"ok": True}


@router.post("/cancel")
def cancel_run(session: UserSession = Depends(get_session)):
    if session.active_runner and session.active_runner.is_running():
        session.active_runner.cancel()
    return {"ok": True}


@router.get("/{run_id}/output-text")
def get_output_text(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    return run.output


@router.post("/restore/{jobs_file:path}")
def restore_jobs(jobs_file: str, session: UserSession = Depends(get_session)):
    raw = session.session_manager.get_raw(jobs_file)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No original bytes for {jobs_file}")
    path = _jobs_path(session, jobs_file)
    with open(path, "wb") as fh:
        fh.write(raw)
    return {"ok": True}


# ── Curves ────────────────────────────────────────────────────────────────────

@router.get("/{run_id}/curves/info")
def get_curves_info(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    jobs_path = _jobs_path(session, run.jobs_file)
    crv_file = read_crv_input_file(jobs_path)
    if not crv_file:
        return {}
    crv_path = session.session_manager.get_path(crv_file)
    info = read_crv_info(crv_path)
    return {col: list(pair) for col, pair in info.items()}


@router.get("/{run_id}/curves/data")
def get_curves_data(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    working_dir = session.session_manager.working_dir
    df = load_curves_csv(os.path.join(working_dir, run.output_dir))
    if df is None:
        raise HTTPException(status_code=404, detail="No curves CSV found for this run")

    time_col = df.columns[0]
    signal_cols = list(df.columns[1:])
    time_vals = df[time_col].tolist()

    # Sample if too many points
    indices: list[int]
    if len(time_vals) > _MAX_POINTS:
        step = len(time_vals) / _MAX_POINTS
        indices = [int(i * step) for i in range(_MAX_POINTS)]
    else:
        indices = list(range(len(time_vals)))

    return {
        "time": [_clean(time_vals[i]) for i in indices],
        "signals": {
            col: [_clean(df[col].iloc[i]) for i in indices]
            for col in signal_cols
        },
    }


# ── Timeline / constraints / log ──────────────────────────────────────────────

@router.get("/{run_id}/timeline")
def get_run_timeline(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    output_dir = os.path.join(session.session_manager.working_dir, run.output_dir)
    path = find_timeline_xml(output_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="No timeline found for this run")
    with open(path, "rb") as fh:
        return parse_timeline(fh.read())


@router.get("/{run_id}/constraints")
def get_run_constraints(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    output_dir = os.path.join(session.session_manager.working_dir, run.output_dir)
    path = find_constraints_xml(output_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="No constraints found for this run")
    with open(path, "rb") as fh:
        return parse_constraints(fh.read())


@router.get("/{run_id}/log")
def get_run_log(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    output_dir = os.path.join(session.session_manager.working_dir, run.output_dir)
    path = find_dynawo_log(output_dir, _run_log_filename(session, run))
    if path is None:
        raise HTTPException(status_code=404, detail="No log file found for this run")
    with open(path, "rb") as fh:
        return {"text": fh.read().decode("utf-8", errors="replace")}


@router.get("/{run_id}/lost-equipments")
def get_run_lost_equipments(run_id: int, session: UserSession = Depends(get_session)):
    run = _get_run(session, run_id)
    output_dir = os.path.join(session.session_manager.working_dir, run.output_dir)
    path = find_lost_equipments_xml(output_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="No lost equipment found for this run")
    with open(path, "rb") as fh:
        return parse_lost_equipments(fh.read())
