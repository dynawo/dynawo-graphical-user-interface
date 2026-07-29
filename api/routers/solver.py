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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_session
from api.session_store import UserSession
from backend.jobs_parser import read_solver_info
from backend.models import ChangeLogEntry, ParameterChange
from backend.par_parser import parse_par
from backend.par_writer import write_par_values

router = APIRouter(tags=["solver"])


def _get_solver_info(session: UserSession) -> dict:
    jobs_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]
    if not jobs_files:
        raise HTTPException(status_code=404, detail="No .jobs file uploaded")
    jobs_path = session.session_manager.get_path(jobs_files[0])
    info = read_solver_info(jobs_path)
    if info is None:
        raise HTTPException(status_code=404, detail="No <solver> element found in the jobs file")
    if not info.par_file:
        raise HTTPException(status_code=422, detail="Solver has no parFile attribute")
    if not info.par_id:
        raise HTTPException(status_code=422, detail="Solver has no parId attribute")
    return {"lib": info.lib, "parFile": info.par_file, "parId": info.par_id}


def _get_par_set(session: UserSession, par_file: str, par_id: str) -> dict:
    par_path = session.session_manager.get_path(par_file)
    if not os.path.isfile(par_path):
        return {"pars": [], "refs": []}
    with open(par_path, "rb") as fh:
        sets = parse_par(fh.read())
    return sets.get(par_id, {"pars": [], "refs": []})


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/is-modified")
def get_solver_is_modified(session: UserSession = Depends(get_session)):
    """Return whether the solver par file differs from the original uploaded bytes."""
    try:
        info = _get_solver_info(session)
    except HTTPException:
        return {"modified": False}
    orig = session.session_manager.get_raw(info["parFile"])
    if orig is None:
        return {"modified": False}
    path = session.session_manager.get_path(info["parFile"])
    if not os.path.isfile(path):
        return {"modified": False}
    with open(path, "rb") as fh:
        return {"modified": fh.read() != orig}


@router.get("/info")
def get_solver_info(session: UserSession = Depends(get_session)):
    return _get_solver_info(session)


@router.get("/parameters")
def get_solver_parameters(session: UserSession = Depends(get_session)):
    info = _get_solver_info(session)
    par_set = _get_par_set(session, info["parFile"], info["parId"])
    return {**info, "pars": par_set["pars"], "refs": par_set["refs"]}


class ApplySolverRequest(BaseModel):
    values: dict[str, str]


@router.put("/parameters")
def apply_solver_parameters(req: ApplySolverRequest, session: UserSession = Depends(get_session)):
    info = _get_solver_info(session)
    par_path = session.session_manager.get_path(info["parFile"])
    if not os.path.isfile(par_path):
        raise HTTPException(status_code=404, detail=f"Par file {info['parFile']} not found")

    par_set = _get_par_set(session, info["parFile"], info["parId"])
    old_vals = {p["name"]: p["value"] for p in par_set.get("pars", [])}
    diffs = [
        ParameterChange(name=k, old_value=old_vals.get(k, ""), new_value=v)
        for k, v in req.values.items()
        if old_vals.get(k, "") != v
    ]
    write_par_values(par_path, info["parId"], req.values)
    if diffs:
        session.solver_change_log.append(ChangeLogEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            dyn_id="solver",
            par_file=info["parFile"],
            set_id=info["parId"],
            changes=diffs,
        ))
    return {"changed": len(diffs)}


@router.post("/restore")
def restore_solver_par(session: UserSession = Depends(get_session)):
    info = _get_solver_info(session)
    raw = session.session_manager.get_raw(info["parFile"])
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No original bytes for {info['parFile']}")
    par_path = session.session_manager.get_path(info["parFile"])
    with open(par_path, "wb") as fh:
        fh.write(raw)
    session.solver_change_log = []
    return {"ok": True}


@router.get("/changelog")
def get_solver_changelog(session: UserSession = Depends(get_session)):
    return [
        {
            "timestamp": e.timestamp,
            "par_file": e.par_file,
            "set_id": e.set_id,
            "changes": [{"name": c.name, "old_value": c.old_value, "new_value": c.new_value}
                        for c in e.changes],
        }
        for e in session.solver_change_log
    ]


@router.delete("/changelog")
def clear_solver_changelog(session: UserSession = Depends(get_session)):
    session.solver_change_log = []
    return {"ok": True}


@router.post("/changelog/revert/{timestamp}")
def revert_solver_changelog_entry(
    timestamp: str,
    session: UserSession = Depends(get_session),
):
    log = session.solver_change_log
    idx = next((i for i, e in enumerate(log) if e.timestamp == timestamp), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Log entry not found")

    entry = log[idx]
    par_path = session.session_manager.get_path(entry.par_file)
    if not os.path.isfile(par_path):
        raise HTTPException(status_code=404, detail=f"Par file {entry.par_file} not found")

    old_vals = {c.name: c.old_value for c in entry.changes}
    write_par_values(par_path, entry.set_id, old_vals)
    session.solver_change_log = [e for i, e in enumerate(log) if i != idx]

    reverted_names = set(old_vals)
    later_overlap = any(
        any(c.name in reverted_names for c in e.changes)
        for e in session.solver_change_log[idx:]
    )
    return {"ok": True, "warned": later_overlap}
