#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import io
import logging
import zipfile
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from api.dependencies import get_session
from api.session_store import UserSession
from backend.output_parser import parse_constraints, parse_lost_equipments, parse_timeline

router = APIRouter(tags=["files"])
logger = logging.getLogger(__name__)


def _validate_filename(filename: str | None) -> str:
    """Allow a bare filename or a relative path (e.g. from a dropped folder),
    but reject anything that could escape the session working directory.
    """
    if not filename:
        raise HTTPException(status_code=400, detail="Missing filename")
    normalized = filename.replace("\\", "/")
    parts = normalized.split("/")
    if normalized.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=400, detail=f"Invalid filename: {filename!r}")
    return normalized


def _read_session_file(session: UserSession, filename: str) -> bytes:
    if not session.session_manager.has_file(filename):
        raise HTTPException(status_code=404, detail=f"{filename} not found in session")
    with open(session.session_manager.get_path(filename), "rb") as fh:
        return fh.read()


@router.get("/{filename:path}/timeline")
def get_file_timeline(filename: str, session: UserSession = Depends(get_session)):
    return parse_timeline(_read_session_file(session, filename))


@router.get("/{filename:path}/constraints")
def get_file_constraints(filename: str, session: UserSession = Depends(get_session)):
    return parse_constraints(_read_session_file(session, filename))


@router.get("/{filename:path}/log")
def get_file_log(filename: str, session: UserSession = Depends(get_session)):
    return {"text": _read_session_file(session, filename).decode("utf-8", errors="replace")}


@router.get("/{filename:path}/lost-equipments")
def get_file_lost_equipments(filename: str, session: UserSession = Depends(get_session)):
    return parse_lost_equipments(_read_session_file(session, filename))


def _auto_fix_paths(session: UserSession) -> None:
    """If a jobs file is in the session, rewrite all path references to bare filenames."""
    jobs_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "jobs"]
    if not jobs_files:
        return
    try:
        from backend.autoload import fix_paths_in_session
        jobs_path = Path(session.session_manager.get_path(jobs_files[0]))
        session_dir = Path(session.session_manager.working_dir)
        result = fix_paths_in_session(jobs_path, session_dir)
        logger.info("auto_fix_paths: fixed=%s unresolved=%s", result["fixed"], result["unresolved"])
        # Refresh jobs file metadata (content changed)
        info = session.session_manager.register_existing_file(jobs_files[0])
        session.uploaded_files_info[jobs_files[0]]["size"] = info.size
        # Also refresh DYD files that were rewritten
        for name, meta in session.uploaded_files_info.items():
            if meta.get("ftype") == "dyd":
                try:
                    dyd_info = session.session_manager.register_existing_file(name)
                    session.uploaded_files_info[name]["size"] = dyd_info.size
                except FileNotFoundError:
                    pass
    except Exception:
        logger.exception("auto_fix_paths failed")  # log but never block uploads


@router.post("/upload")
async def upload_files(
    files: list[UploadFile],
    session: UserSession = Depends(get_session),
):
    results = []
    for f in files:
        filename = _validate_filename(f.filename)
        data = await f.read()
        if session.session_manager.has_file(filename):
            continue
        info = session.session_manager.add_file(filename, data)
        session.uploaded_files_info[filename] = {
            "size": info.size,
            "ftype": info.ftype,
        }
        results.append({"name": filename, "size": info.size, "ftype": info.ftype})
    _auto_fix_paths(session)
    return results


@router.get("/")
def list_files(session: UserSession = Depends(get_session)):
    return [
        {"name": name, "size": meta["size"], "ftype": meta["ftype"]}
        for name, meta in session.uploaded_files_info.items()
    ]


@router.delete("/{filename:path}")
def unload_file(filename: str, session: UserSession = Depends(get_session)):
    if not session.session_manager.has_file(filename):
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    session.session_manager.remove_file(filename)
    session.uploaded_files_info.pop(filename, None)
    if session.network_name == filename:
        session.network = None
        session.network_name = None
    for log in (session.par_change_log, session.solver_change_log):
        log[:] = [e for e in log if e.par_file != filename]
    return {"ok": True}


@router.get("/download")
def download_session(session: UserSession = Depends(get_session)):
    working_dir = session.session_manager.working_dir

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(working_dir):
            for name in files:
                abs_path = os.path.join(root, name)
                arc_name = os.path.relpath(abs_path, working_dir)
                zf.write(abs_path, arc_name)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=dynawo_session.zip"},
    )


@router.put("/{filename:path}")
async def replace_file(
    filename: str,
    file: UploadFile,
    session: UserSession = Depends(get_session),
):
    if not session.session_manager.has_file(filename):
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    data = await file.read()
    info = session.session_manager.replace_file(filename, data)
    session.uploaded_files_info[filename] = {"size": info.size, "ftype": info.ftype}
    if session.network_name == filename:
        session.network = None
        session.network_name = None
    for log in (session.par_change_log, session.solver_change_log):
        log[:] = [e for e in log if e.par_file != filename]
    _auto_fix_paths(session)
    return {"name": filename, "size": info.size, "ftype": info.ftype}
