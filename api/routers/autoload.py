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
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from api.dependencies import get_session
from api.routers.files import _auto_fix_paths
from api.session_store import UserSession
from backend.autoload import fix_paths_in_session, run_pipeline
from backend.jobs_parser import read_all_file_refs

router = APIRouter(tags=["autoload"])


def _get_jobs_path(session: UserSession) -> Path:
    jobs_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]
    if not jobs_files:
        raise HTTPException(status_code=404, detail="No .jobs file uploaded")
    return Path(session.session_manager.get_path(jobs_files[0]))


def _register_results(session: UserSession, result: dict) -> None:
    """Register every newly copied file into the session, then refresh the jobs file."""
    for filename in result["loaded"]:
        try:
            info = session.session_manager.register_existing_file(filename)
            session.uploaded_files_info[filename] = {
                "size": info.size,
                "ftype": info.ftype,
            }
        except FileNotFoundError:
            pass
    # run_pipeline rewrites the jobs file in place — refresh its metadata too
    jobs_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]
    for name in jobs_files:
        try:
            info = session.session_manager.register_existing_file(name)
            session.uploaded_files_info[name]["size"] = info.size
        except FileNotFoundError:
            pass


# ── Directory browser ─────────────────────────────────────────────────────────

@router.get("/browse")
def browse_directory(path: str = "", session: UserSession = Depends(get_session)):
    """List subdirectories at the given path for the server-side directory picker."""
    import pwd
    start = Path(path) if path else Path.home()
    if not start.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {path}")
    try:
        entries = sorted(start.iterdir(), key=lambda e: e.name.lower())
        dirs  = [e.name for e in entries if e.is_dir()  and not e.name.startswith('.')]
        files = [e.name for e in entries if e.is_file() and not e.name.startswith('.')]
        parent = str(start.parent) if start.parent != start else None
        return {
            "current": str(start),
            "parent":  parent,
            "dirs":    dirs,
            "files":   files,
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"Permission denied: {path}")


# ── Base-dir suggestion ────────────────────────────────────────────────────────

@router.get("/suggest-base-dir")
def suggest_base_dir(session: UserSession = Depends(get_session)):
    """Return a suggested base directory derived from absolute paths in the jobs file."""
    jobs_path = _get_jobs_path(session)
    raw_refs = read_all_file_refs(str(jobs_path))

    abs_parents = []
    for role, raw in raw_refs.items():
        raws = raw if role == "dyd" else ([raw] if raw else [])
        for r in raws:
            if r and Path(r).is_absolute():
                abs_parents.append(str(Path(r).parent))

    if not abs_parents:
        return {"suggestion": None}

    try:
        common = os.path.commonpath(abs_parents)
    except ValueError:
        common = abs_parents[0]

    return {"suggestion": common}


# ── Filesystem mode ────────────────────────────────────────────────────────────

@router.get("/preview")
def preview(base_dir: str, session: UserSession = Depends(get_session)):
    """Return the list of files that would be loaded from a filesystem base_dir."""
    jobs_path = _get_jobs_path(session)
    base = Path(base_dir)
    if not base.is_dir():
        raise HTTPException(status_code=422, detail=f"Directory not found: {base_dir}")

    from backend.autoload import collect_dyd_par_refs, resolve_paths
    raw_refs = read_all_file_refs(str(jobs_path))
    resolved = resolve_paths(raw_refs, base)

    entries = []
    for role, path in resolved.items():
        if role == "dyd":
            for i, dyd_path in enumerate(path):
                entries.append({
                    "role":     "dyd",
                    "raw":      raw_refs["dyd"][i],
                    "resolved": str(dyd_path),
                    "exists":   dyd_path.exists(),
                })
        elif path is not None:
            entries.append({
                "role":     role,
                "raw":      raw_refs[role],
                "resolved": str(path),
                "exists":   path.exists(),
            })

    # Add PAR refs from every DYD that exists
    for dyd_path in resolved.get("dyd", []):
        if dyd_path.exists():
            for raw, abs_path in collect_dyd_par_refs(dyd_path):
                entries.append({
                    "role":     "dyd_par",
                    "raw":      raw,
                    "resolved": str(abs_path),
                    "exists":   abs_path.exists(),
                })

    return {"base_dir": base_dir, "files": entries}


class FilesystemRunRequest(BaseModel):
    base_dir: str


@router.post("/run/filesystem")
def run_filesystem(req: FilesystemRunRequest, session: UserSession = Depends(get_session)):
    jobs_path = _get_jobs_path(session)
    base = Path(req.base_dir)
    if not base.is_dir():
        raise HTTPException(status_code=422, detail=f"Directory not found: {req.base_dir}")

    dest = Path(session.session_manager.working_dir)
    result = run_pipeline(jobs_path, base, dest)
    _register_results(session, result)
    _auto_fix_paths(session)
    return result


# ── ZIP mode ───────────────────────────────────────────────────────────────────

@router.post("/run/zip")
async def run_zip(file: UploadFile, session: UserSession = Depends(get_session)):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=422, detail="Uploaded file must be a .zip archive")

    data = await file.read()
    extract_dir = tempfile.mkdtemp(prefix="dynawo_autoload_zip_")
    try:
        with zipfile.ZipFile(data.__class__(data) if not hasattr(data, "read") else data) as _:
            pass
    except zipfile.BadZipFile:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Uploaded file is not a valid ZIP archive")

    try:
        import io as _io
        with zipfile.ZipFile(_io.BytesIO(data)) as zf:
            zf.extractall(extract_dir)

        # Locate the .jobs file inside the extracted tree
        jobs_files = []
        for root, _, files in os.walk(extract_dir):
            for name in files:
                if name.endswith(".jobs"):
                    jobs_files.append(Path(root) / name)

        if not jobs_files:
            raise HTTPException(status_code=422, detail="No .jobs file found in the ZIP archive")
        if len(jobs_files) > 1:
            names = [str(p.relative_to(extract_dir)) for p in jobs_files]
            raise HTTPException(
                status_code=422,
                detail=f"Multiple .jobs files found in ZIP: {', '.join(names)}. Include only one.",
            )

        jobs_path_in_zip = jobs_files[0]
        base_dir = jobs_path_in_zip.parent
        dest = Path(session.session_manager.working_dir)

        # Copy the jobs file itself into dest first (run_pipeline expects it there)
        dest_jobs = dest / jobs_path_in_zip.name
        shutil.copy2(str(jobs_path_in_zip), str(dest_jobs))
        session.session_manager.register_existing_file(jobs_path_in_zip.name)
        session.uploaded_files_info[jobs_path_in_zip.name] = {
            "size": dest_jobs.stat().st_size,
            "ftype": "jobs",
        }

        result = run_pipeline(dest_jobs, base_dir, dest)
        _register_results(session, result)
        _auto_fix_paths(session)
        return result

    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


# ── Fix-paths mode (mode 3) ────────────────────────────────────────────────────

@router.post("/fix-paths")
def fix_paths(session: UserSession = Depends(get_session)):
    """Rewrite jobs + DYD paths to bare filenames matching files already in session."""
    jobs_path = _get_jobs_path(session)
    session_dir = Path(session.session_manager.working_dir)
    result = fix_paths_in_session(jobs_path, session_dir)

    # Re-register the jobs (and dyd) file since its content changed
    for filename in [jobs_path.name]:
        if session.session_manager.has_file(filename):
            try:
                info = session.session_manager.register_existing_file(filename)
                session.uploaded_files_info[filename]["size"] = info.size
            except FileNotFoundError:
                pass

    return result
