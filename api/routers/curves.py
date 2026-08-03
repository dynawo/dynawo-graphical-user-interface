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
from backend.crv_parser import parse_crv
from backend.crv_writer import build_crv_bytes, write_crv
from backend.desc_parser import get_lib_variables
from backend.dyd_parser import parse_dyd
from backend.jobs_parser import write_crv_reference_to_jobs
from backend.models import CrvChangeLogEntry, CurveChange

router = APIRouter(tags=["curves"])


def _get_crv_file(session: UserSession) -> str | None:
    crv_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "crv"]
    return crv_files[0] if crv_files else None


def _get_dyd_lib_map(session: UserSession) -> dict[str, str]:
    """Return {dyn_id: lib} from the session's DYD file."""
    dyd_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "dyd"]
    if not dyd_files:
        return {}
    raw = session.session_manager.get_raw(dyd_files[0])
    if not raw:
        return {}
    try:
        return {info["dyn_id"]: info["lib"] for info in parse_dyd(raw).values()}
    except Exception:
        return {}


def _get_jobs_files(session: UserSession) -> list[str]:
    return [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]


def _suggested_crv_filename(session: UserSession) -> str:
    """Suggest a .crv filename based on files already in the session."""
    for ftype in ("dyd", "jobs"):
        names = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == ftype]
        if names:
            stem = os.path.splitext(names[0])[0]
            return f"{stem}.crv"
    return "curves.crv"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/init-info")
def get_init_info(session: UserSession = Depends(get_session)):
    """Return the suggested .crv filename and whether any jobs file will be patched."""
    return {
        "suggested_filename": _suggested_crv_filename(session),
        "has_jobs": len(_get_jobs_files(session)) > 0,
    }


class InitRequest(BaseModel):
    crv_filename: str


@router.post("/init")
def init_crv(req: InitRequest, session: UserSession = Depends(get_session)):
    """Create an empty .crv file and link it in every jobs file in the session."""
    name = req.crv_filename.strip()
    if not name:
        raise HTTPException(status_code=422, detail="crv_filename must not be empty")

    raw = build_crv_bytes([])
    info = session.session_manager.add_file(name, raw)
    session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype}

    patched_jobs: list[str] = []
    for jobs_name in _get_jobs_files(session):
        jobs_path = session.session_manager.get_path(jobs_name)
        if os.path.isfile(jobs_path):
            try:
                write_crv_reference_to_jobs(jobs_path, name)
                # Refresh the stored raw bytes so the patched jobs is visible downstream
                session.session_manager.register_existing_file(jobs_name)
                patched_jobs.append(jobs_name)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"Failed to patch jobs file {jobs_name}: {exc}")

    return {"crv_file": name, "jobs_patched": len(patched_jobs) > 0, "patched_jobs": patched_jobs}


@router.get("/list")
def list_curves(session: UserSession = Depends(get_session)):
    crv_name = _get_crv_file(session)
    if not crv_name:
        raise HTTPException(status_code=404, detail="No .crv file in session")

    orig_raw = session.session_manager.get_raw(crv_name)
    crv_path = session.session_manager.get_path(crv_name)

    orig_curves = parse_crv(orig_raw) if orig_raw else []

    current_raw = b""
    if os.path.isfile(crv_path):
        with open(crv_path, "rb") as fh:
            current_raw = fh.read()
    current_curves = parse_crv(current_raw) if current_raw else []

    modified = bool(orig_raw and current_raw and current_raw != orig_raw)

    lib_map = _get_dyd_lib_map(session)
    orig_set   = {(c["model"], c["variable"]) for c in orig_curves}
    active_set = {(c["model"], c["variable"]) for c in current_curves}

    # Universe = original order first, then any extras present in current file
    seen: set[tuple[str, str]] = set()
    universe: list[dict] = []
    for c in orig_curves:
        key = (c["model"], c["variable"])
        if key not in seen:
            seen.add(key)
            universe.append(c)
    for c in current_curves:
        key = (c["model"], c["variable"])
        if key not in seen:
            seen.add(key)
            universe.append(c)

    grouped: dict[str, list[dict]] = {}
    for c in universe:
        key = (c["model"], c["variable"])
        grouped.setdefault(c["model"], []).append({
            "variable": c["variable"],
            "active":   key in active_set,
            "extra":    key not in orig_set,
        })

    return {
        "crv_file": crv_name,
        "modified": modified,
        "groups": [
            {"model": model, "lib": lib_map.get(model, ""), "curves": curves}
            for model, curves in grouped.items()
        ],
        "dyd_models": lib_map,
    }


@router.get("/catalogue")
def get_variable_catalogue(session: UserSession = Depends(get_session)):
    """Return descriptor-file variables per model. Requires a configured Dynawo executable."""
    exe = session.dynawo_executable
    if not exe or not os.path.isfile(exe):
        return {"available": False, "catalogue": {}}
    lib_map = _get_dyd_lib_map(session)
    lib_cache: dict[str, list[str]] = {}
    catalogue: dict[str, dict] = {}
    for dyn_id, lib in lib_map.items():
        if lib not in lib_cache:
            lib_cache[lib] = get_lib_variables(exe, lib)
        variables = lib_cache[lib]
        if variables:
            catalogue[dyn_id] = {"lib": lib, "variables": variables}
    return {"available": True, "catalogue": catalogue}


class CurveItem(BaseModel):
    model: str
    variable: str


class ApplyRequest(BaseModel):
    curves: list[CurveItem]


@router.put("/apply")
def apply_curves(req: ApplyRequest, session: UserSession = Depends(get_session)):
    crv_name = _get_crv_file(session)
    if not crv_name:
        raise HTTPException(status_code=404, detail="No .crv file in session")
    crv_path = session.session_manager.get_path(crv_name)
    if not os.path.isfile(crv_path):
        raise HTTPException(status_code=404, detail=f"{crv_name} not found on disk")

    with open(crv_path, "rb") as fh:
        current = parse_crv(fh.read())

    current_set = {(c["model"], c["variable"]) for c in current}
    new_set     = {(c.model, c.variable) for c in req.curves}

    changes = (
        [CurveChange(model=m, variable=v, action="removed") for m, v in current_set - new_set] +
        [CurveChange(model=m, variable=v, action="added")   for m, v in new_set - current_set]
    )

    write_crv(crv_path, [{"model": c.model, "variable": c.variable} for c in req.curves])

    if changes:
        session.crv_change_log.append(CrvChangeLogEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            crv_file=crv_name,
            changes=changes,
        ))
    return {"changed": len(changes)}


@router.post("/restore")
def restore_crv(session: UserSession = Depends(get_session)):
    crv_name = _get_crv_file(session)
    if not crv_name:
        raise HTTPException(status_code=404, detail="No .crv file in session")
    raw = session.session_manager.get_raw(crv_name)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No original bytes for {crv_name}")
    with open(session.session_manager.get_path(crv_name), "wb") as fh:
        fh.write(raw)
    session.crv_change_log = []
    return {"ok": True}


@router.get("/changelog")
def get_changelog(session: UserSession = Depends(get_session)):
    return [
        {
            "timestamp": e.timestamp,
            "crv_file":  e.crv_file,
            "changes": [
                {"model": c.model, "variable": c.variable, "action": c.action}
                for c in e.changes
            ],
        }
        for e in session.crv_change_log
    ]


@router.delete("/changelog")
def clear_changelog(session: UserSession = Depends(get_session)):
    session.crv_change_log = []
    return {"ok": True}


@router.post("/changelog/revert/{timestamp}")
def revert_changelog_entry(timestamp: str, session: UserSession = Depends(get_session)):
    log = session.crv_change_log
    idx = next((i for i, e in enumerate(log) if e.timestamp == timestamp), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Log entry not found")

    entry = log[idx]
    crv_path = session.session_manager.get_path(entry.crv_file)
    if not os.path.isfile(crv_path):
        raise HTTPException(status_code=404, detail=f"{entry.crv_file} not found")

    with open(crv_path, "rb") as fh:
        current = parse_crv(fh.read())
    current_set = {(c["model"], c["variable"]) for c in current}

    # Replay the inverse of each change
    for change in entry.changes:
        key = (change.model, change.variable)
        if change.action == "removed":
            current_set.add(key)
        else:
            current_set.discard(key)

    # Restore original order using the session's original bytes as a template
    orig_raw = session.session_manager.get_raw(entry.crv_file)
    orig_list = parse_crv(orig_raw) if orig_raw else []
    orig_keys = [(c["model"], c["variable"]) for c in orig_list]
    ordered = [{"model": m, "variable": v} for m, v in orig_keys if (m, v) in current_set]
    # Any key added that wasn't in the original (shouldn't normally happen)
    orig_set = set(orig_keys)
    ordered += [{"model": m, "variable": v} for m, v in sorted(current_set) if (m, v) not in orig_set]

    write_crv(crv_path, ordered)
    session.crv_change_log = [e for i, e in enumerate(log) if i != idx]

    reverted_keys = {(c.model, c.variable) for c in entry.changes}
    later_overlap = any(
        any((c.model, c.variable) in reverted_keys for c in e.changes)
        for e in session.crv_change_log[idx:]
    )
    return {"ok": True, "warned": later_overlap}
