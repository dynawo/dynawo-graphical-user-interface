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
from api.dyd_models import get_dyd_models
from api.session_store import UserSession
from backend.crv_parser import parse_crv
from backend.crv_writer import build_crv_bytes, write_crv
from backend.desc_parser import get_lib_symbols
from backend.jobs_parser import read_all_file_refs, read_crv_input_file, write_crv_reference_to_jobs
from backend.models import CrvChangeLogEntry, CurveChange

router = APIRouter(tags=["curves"])


# ── Session file helpers ──────────────────────────────────────────────────────

def _get_jobs_files(session: UserSession) -> list[str]:
    return [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]


def _session_name(session: UserSession, ref: str | None) -> str | None:
    """Map a reference read out of a jobs/dyd file to a session file name.

    Autoload rewrites references to bare filenames in the session directory, but
    a jobs file uploaded as-is can still carry a path — match on the basename in
    that case."""
    if not ref:
        return None
    if session.session_manager.has_file(ref):
        return ref
    base = os.path.basename(ref)
    return base if session.session_manager.has_file(base) else None


def _first_crv_file(session: UserSession) -> str | None:
    crv_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "crv"]
    return crv_files[0] if crv_files else None


def _job_dyd_names(session: UserSession, jobs_file: str) -> list[str] | None:
    """.dyd files this jobs file references, as session file names.

    None when none of them can be resolved — the caller then falls back to every
    .dyd of the session rather than showing an empty model list."""
    path = session.session_manager.get_path(jobs_file)
    if not os.path.isfile(path):
        return None
    names = [n for n in (_session_name(session, d) for d in read_all_file_refs(path)["dyd"]) if n]
    return names or None


def _dyd_lib_map(session: UserSession, jobs_file: str | None) -> dict[str, str]:
    """Return {dyn_id: lib} for the models a .crv of this job may target.

    A .crv <curve> names its target by the .dyd model id, so every model is a
    valid curve target — including those with no staticId (OmegaRef, faults,
    events, …). Scoped to the jobs file's own .dyd files when one is given, so
    two jobs sharing a session don't show each other's models."""
    dyd_names = _job_dyd_names(session, jobs_file) if jobs_file else None
    return {dyn_id: info["lib"] for dyn_id, info in get_dyd_models(session, dyd_names).items()}


def _crv_ref_of(session: UserSession, jobs_file: str) -> str | None:
    """The raw inputFile of the jobs file's <curves> element, or None."""
    path = session.session_manager.get_path(jobs_file)
    return read_crv_input_file(path) if os.path.isfile(path) else None


def _resolve_crv(session: UserSession, jobs_file: str | None) -> str | None:
    """The .crv file to edit for `jobs_file`, or the session's single .crv when
    no job is named (the pre-multi-job behaviour, kept for callers that don't
    care which job they act on)."""
    if jobs_file is None:
        return _first_crv_file(session)
    if not session.session_manager.has_file(jobs_file):
        raise HTTPException(status_code=404, detail=f"{jobs_file} not found in session")
    return _session_name(session, _crv_ref_of(session, jobs_file))


def _require_crv(session: UserSession, jobs_file: str | None) -> str:
    crv_name = _resolve_crv(session, jobs_file)
    if not crv_name:
        detail = (f"No .crv file linked in {jobs_file}" if jobs_file else "No .crv file in session")
        raise HTTPException(status_code=404, detail=detail)
    return crv_name


def _suggested_crv_filename(session: UserSession, jobs_file: str | None = None) -> str:
    """Suggest a .crv filename for a job, or from the files already in the session."""
    if jobs_file:
        # A jobs file can name a .crv that was never uploaded — reuse that name so
        # creating the file makes the existing reference resolve.
        ref = _crv_ref_of(session, jobs_file)
        if ref:
            return os.path.basename(ref)
        return f"{os.path.splitext(jobs_file)[0]}.crv"
    for ftype in ("dyd", "jobs"):
        names = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == ftype]
        if names:
            stem = os.path.splitext(names[0])[0]
            return f"{stem}.crv"
    return "curves.crv"


def _read_curves(session: UserSession, crv_name: str) -> list[dict]:
    path = session.session_manager.get_path(crv_name)
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as fh:
        return parse_crv(fh.read())


def _log(session: UserSession, crv_name: str, changes: list[CurveChange], timestamp: str) -> None:
    if changes:
        session.crv_change_log.append(CrvChangeLogEntry(
            timestamp=timestamp,
            crv_file=crv_name,
            changes=changes,
        ))


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/targets")
def list_targets(session: UserSession = Depends(get_session)):
    """One entry per jobs file, naming the .crv each one drives.

    The Edit Curves page edits the .crv of a single job at a time; this is the
    list it offers. `crv_file` is null when the job links no curves file (or
    links one that isn't in the session — `crv_ref` then says which), and the
    page offers to create it. Several jobs can legitimately point at the same
    .crv: `shared_with` names the others so an edit's reach stays visible."""
    entries = []
    for jobs_file in _get_jobs_files(session):
        ref = _crv_ref_of(session, jobs_file)
        crv = _session_name(session, ref)
        entries.append({
            "jobs_file":   jobs_file,
            "crv_file":    crv,
            "crv_ref":     ref,
            "curve_count": len(_read_curves(session, crv)) if crv else 0,
        })
    for e in entries:
        e["shared_with"] = [
            o["jobs_file"] for o in entries
            if o is not e and e["crv_file"] and o["crv_file"] == e["crv_file"]
        ]
    return {"targets": entries, "orphan_crv": None if any(e["crv_file"] for e in entries) else _first_crv_file(session)}


@router.get("/init-info")
def get_init_info(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    """Return the suggested .crv filename and whether any jobs file will be patched."""
    return {
        "suggested_filename": _suggested_crv_filename(session, jobs_file),
        "has_jobs": len(_get_jobs_files(session)) > 0,
    }


class InitRequest(BaseModel):
    crv_filename: str
    # Which jobs file to link the new .crv into. None keeps the original
    # behaviour of linking it into every job of the session.
    jobs_file: str | None = None


@router.post("/init")
def init_crv(req: InitRequest, session: UserSession = Depends(get_session)):
    """Create an empty .crv file and link it in one jobs file, or in all of them."""
    name = req.crv_filename.strip()
    if not name:
        raise HTTPException(status_code=422, detail="crv_filename must not be empty")
    if req.jobs_file is not None and not session.session_manager.has_file(req.jobs_file):
        raise HTTPException(status_code=404, detail=f"{req.jobs_file} not found in session")

    # Don't wipe a .crv that already exists — link the existing one instead.
    if not session.session_manager.has_file(name):
        info = session.session_manager.add_file(name, build_crv_bytes([]))
        session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype}

    targets = [req.jobs_file] if req.jobs_file else _get_jobs_files(session)
    patched_jobs: list[str] = []
    for jobs_name in targets:
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
def list_curves(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    crv_name = _require_crv(session, jobs_file)

    orig_raw = session.session_manager.get_raw(crv_name)
    crv_path = session.session_manager.get_path(crv_name)

    orig_curves = parse_crv(orig_raw) if orig_raw else []

    current_raw = b""
    if os.path.isfile(crv_path):
        with open(crv_path, "rb") as fh:
            current_raw = fh.read()
    current_curves = parse_crv(current_raw) if current_raw else []

    modified = bool(orig_raw and current_raw and current_raw != orig_raw)

    lib_map = _dyd_lib_map(session, jobs_file)
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
        "jobs_file": jobs_file,
        "modified": modified,
        "groups": [
            {"model": model, "lib": lib_map.get(model, ""), "curves": curves}
            for model, curves in grouped.items()
        ],
        "dyd_models": lib_map,
    }


@router.get("/catalogue")
def get_variable_catalogue(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    """Return descriptor-file variables and parameters per model.

    Requires a configured Dynawo executable. Both kinds are valid curve targets:
    Dynawo resolves a .crv <curve> variable against the model variables first and
    falls back to its parameters."""
    exe = session.dynawo_executable
    if not exe or not os.path.isfile(exe):
        return {"available": False, "catalogue": {}}
    lib_map = _dyd_lib_map(session, jobs_file)
    lib_cache: dict[str, tuple[list[str], list[str]]] = {}
    catalogue: dict[str, dict] = {}
    for dyn_id, lib in lib_map.items():
        if lib not in lib_cache:
            lib_cache[lib] = get_lib_symbols(exe, lib)
        variables, parameters = lib_cache[lib]
        if variables or parameters:
            catalogue[dyn_id] = {"lib": lib, "variables": variables, "parameters": parameters}
    return {"available": True, "catalogue": catalogue}


class CurveItem(BaseModel):
    model: str
    variable: str


class ApplyRequest(BaseModel):
    curves: list[CurveItem]
    # The job whose .crv the `curves` list describes. None = the session's single
    # .crv, i.e. the behaviour from before jobs could be edited separately.
    jobs_file: str | None = None
    # Also replay this edit — the curves added and removed, not the whole list —
    # on every other job's .crv file.
    apply_to_all: bool = False


@router.put("/apply")
def apply_curves(req: ApplyRequest, session: UserSession = Depends(get_session)):
    crv_name = _require_crv(session, req.jobs_file)
    crv_path = session.session_manager.get_path(crv_name)
    if not os.path.isfile(crv_path):
        raise HTTPException(status_code=404, detail=f"{crv_name} not found on disk")

    with open(crv_path, "rb") as fh:
        current = parse_crv(fh.read())

    current_set = {(c["model"], c["variable"]) for c in current}
    new_set     = {(c.model, c.variable) for c in req.curves}
    # Request order, so a curve added here lands in the same place everywhere.
    added   = [(c.model, c.variable) for c in req.curves if (c.model, c.variable) not in current_set]
    removed = [k for k in current_set - new_set]

    timestamp = datetime.now().isoformat(timespec="seconds")
    changes = (
        [CurveChange(model=m, variable=v, action="removed") for m, v in removed] +
        [CurveChange(model=m, variable=v, action="added")   for m, v in added]
    )

    write_crv(crv_path, [{"model": c.model, "variable": c.variable} for c in req.curves])
    _log(session, crv_name, changes, timestamp)

    results = [{
        "jobs_file": req.jobs_file,
        "crv_file":  crv_name,
        "added":     len(added),
        "removed":   len(removed),
        "skipped":   [],
    }]

    if req.apply_to_all and (added or removed):
        results += _replay_on_other_jobs(session, req.jobs_file, crv_name, added, removed, timestamp)

    return {"changed": len(changes), "results": results}


def _replay_on_other_jobs(
    session: UserSession,
    edited_jobs_file: str | None,
    edited_crv: str,
    added: list[tuple[str, str]],
    removed: list[tuple[str, str]],
    timestamp: str,
) -> list[dict]:
    """Apply the same additions and removals to every other job's .crv file.

    The *edit* is replayed, not the whole curve list: a curve a job holds that
    the edited one never had stays untouched, so jobs keep their own extras.
    A curve is only added to a job whose .dyd actually declares that model —
    Dynawo would otherwise warn about an unknown model and produce nothing —
    and the skipped ones are reported back so the page can say so."""
    removed_set = set(removed)
    results: list[dict] = []
    done: set[str] = {edited_crv}

    for jobs_file in _get_jobs_files(session):
        if jobs_file == edited_jobs_file:
            continue
        crv_name = _session_name(session, _crv_ref_of(session, jobs_file))
        if not crv_name:
            results.append({
                "jobs_file": jobs_file, "crv_file": None,
                "added": 0, "removed": 0, "skipped": [],
                "note": "no curves file linked — skipped",
            })
            continue
        if crv_name in done:
            # Two jobs sharing one .crv: already written, don't count it twice.
            results.append({
                "jobs_file": jobs_file, "crv_file": crv_name,
                "added": 0, "removed": 0, "skipped": [],
                "note": "shares a curves file already updated",
            })
            continue
        done.add(crv_name)

        known_models = _dyd_lib_map(session, jobs_file)
        current = _read_curves(session, crv_name)
        current_keys = [(c["model"], c["variable"]) for c in current]
        current_set = set(current_keys)

        applicable = [k for k in added if k[0] in known_models and k not in current_set]
        skipped    = [f"{m} — {v}" for m, v in added if m not in known_models]
        to_remove  = removed_set & current_set

        if not applicable and not to_remove:
            results.append({
                "jobs_file": jobs_file, "crv_file": crv_name,
                "added": 0, "removed": 0, "skipped": skipped,
                "note": "already up to date" if not skipped else None,
            })
            continue

        ordered = [{"model": m, "variable": v} for m, v in current_keys if (m, v) not in to_remove]
        ordered += [{"model": m, "variable": v} for m, v in applicable]
        write_crv(session.session_manager.get_path(crv_name), ordered)

        _log(session, crv_name, (
            [CurveChange(model=m, variable=v, action="removed") for m, v in to_remove] +
            [CurveChange(model=m, variable=v, action="added")   for m, v in applicable]
        ), timestamp)

        results.append({
            "jobs_file": jobs_file, "crv_file": crv_name,
            "added": len(applicable), "removed": len(to_remove),
            "skipped": skipped,
        })
    return results


@router.post("/restore")
def restore_crv(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    crv_name = _require_crv(session, jobs_file)
    raw = session.session_manager.get_raw(crv_name)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No original bytes for {crv_name}")
    with open(session.session_manager.get_path(crv_name), "wb") as fh:
        fh.write(raw)
    # Only this file's history is undone — another job's .crv is untouched.
    session.crv_change_log = [e for e in session.crv_change_log if e.crv_file != crv_name]
    return {"ok": True, "crv_file": crv_name}


def _log_entries(session: UserSession, crv_name: str | None) -> list[CrvChangeLogEntry]:
    if crv_name is None:
        return session.crv_change_log
    return [e for e in session.crv_change_log if e.crv_file == crv_name]


@router.get("/changelog")
def get_changelog(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    crv_name = _resolve_crv(session, jobs_file) if jobs_file else None
    return [
        {
            "id":        e.entry_id,
            "timestamp": e.timestamp,
            "crv_file":  e.crv_file,
            "changes": [
                {"model": c.model, "variable": c.variable, "action": c.action}
                for c in e.changes
            ],
        }
        for e in _log_entries(session, crv_name)
    ]


@router.delete("/changelog")
def clear_changelog(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    crv_name = _resolve_crv(session, jobs_file) if jobs_file else None
    if crv_name is None:
        session.crv_change_log = []
    else:
        session.crv_change_log = [e for e in session.crv_change_log if e.crv_file != crv_name]
    return {"ok": True}


@router.post("/changelog/revert/{entry_id}")
def revert_changelog_entry(entry_id: str, session: UserSession = Depends(get_session)):
    log = session.crv_change_log
    idx = next((i for i, e in enumerate(log) if e.entry_id == entry_id), None)
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
        for e in session.crv_change_log[idx:] if e.crv_file == entry.crv_file
    )
    return {"ok": True, "warned": later_overlap}
