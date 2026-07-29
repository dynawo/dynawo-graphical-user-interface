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
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.dependencies import get_session
from api.session_store import UserSession
from backend.download_manager import DownloadManager
from backend.user_config import (
    add_local_executable,
    clear_default_executable,
    get_local_executables,
    load_user_config,
    remove_local_executable,
    save_user_config,
)

router = APIRouter(tags=["dynawo_version"])

_VERSIONS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "dynawo_versions.json"
)
_DOWNLOAD_BASE = os.path.join(
    os.path.expanduser("~"), ".config", "dynawo_ihm", "dynawo_versions"
)


def _version_dir(os_key: str, version: str) -> str:
    return os.path.join(_DOWNLOAD_BASE, os_key, version)


def _is_downloaded(os_key: str, version: str) -> bool:
    d = _version_dir(os_key, version)
    return os.path.isdir(d) and bool(os.listdir(d))


def _find_dynawo_exe(extract_dir: str) -> str | None:
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f == "dynawo.sh":
                path = os.path.join(root, f)
                if not os.access(path, os.X_OK):
                    os.chmod(path, os.stat(path).st_mode | 0o111)
                return path
    return None


def _load_versions() -> dict:
    try:
        with open(_VERSIONS_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/versions")
def get_versions():
    return _load_versions()


@router.get("/downloaded")
def get_downloaded():
    versions_data = _load_versions()
    result = []
    for os_key, versions in versions_data.items():
        for version in versions:
            if _is_downloaded(os_key, version):
                d = _version_dir(os_key, version)
                exe = _find_dynawo_exe(d)
                result.append({"os_key": os_key, "version": version, "exe": exe})
    return result


class DownloadRequest(BaseModel):
    os_key: str
    version: str


@router.post("/download")
def start_download(req: DownloadRequest, session: UserSession = Depends(get_session)):
    versions_data = _load_versions()
    url = versions_data.get(req.os_key, {}).get(req.version, {}).get("url", "")
    if not url:
        raise HTTPException(status_code=404, detail="Version not found")
    if _is_downloaded(req.os_key, req.version):
        raise HTTPException(status_code=409, detail="Already downloaded")
    dest_dir = _version_dir(req.os_key, req.version)
    dm = DownloadManager()
    dm.start(url, dest_dir)
    session.active_download = dm
    session.active_download_target = (req.os_key, req.version)
    return {"ok": True}


@router.get("/download/progress")
async def download_progress(session: UserSession = Depends(get_session)):
    async def event_stream():
        dm: DownloadManager | None = getattr(session, "active_download", None)
        if dm is None:
            yield 'data: {"done": true}\n\n'
            return
        while dm.is_running():
            state = dm.state
            yield f'data: {{"fraction": {state.fraction:.3f}, "text": "{state.text}", "done": false}}\n\n'
            await asyncio.sleep(0.5)
        state = dm.state
        error = state.error or ""
        yield f'data: {{"fraction": 1.0, "text": "{state.text}", "done": true, "error": "{error}"}}\n\n'
        session.active_download = None
        session.active_download_target = None

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/use/{os_key}/{version}")
def use_version(os_key: str, version: str, session: UserSession = Depends(get_session)):
    d = _version_dir(os_key, version)
    if not _is_downloaded(os_key, version):
        raise HTTPException(status_code=404, detail="Version not downloaded")
    exe = _find_dynawo_exe(d)
    if not exe:
        raise HTTPException(status_code=404, detail="dynawo.sh not found in extracted folder")
    session.dynawo_executable = exe
    save_user_config({**load_user_config(), "dynawo_executable": exe})
    return {"exe": exe}


@router.delete("/remove/{os_key}/{version}")
def remove_version(os_key: str, version: str, session: UserSession = Depends(get_session)):
    d = _version_dir(os_key, version)
    if not os.path.isdir(d):
        raise HTTPException(status_code=404, detail="Version not found on disk")
    shutil.rmtree(d)
    if session.dynawo_executable and session.dynawo_executable.startswith(d):
        session.dynawo_executable = None
    return {"ok": True}


@router.get("/executable")
def get_executable(session: UserSession = Depends(get_session)):
    exe = session.dynawo_executable or load_user_config().get("dynawo_executable", "")
    if exe and not session.dynawo_executable:
        session.dynawo_executable = exe
    return {"exe": exe}


class ExeRequest(BaseModel):
    exe: str


@router.post("/executable")
def set_executable(req: ExeRequest, session: UserSession = Depends(get_session)):
    exe = req.exe.strip()
    if exe and not os.path.isfile(exe):
        raise HTTPException(status_code=400, detail=f"File not found: {exe}")
    session.dynawo_executable = exe or None
    if exe:
        add_local_executable(exe)
    else:
        clear_default_executable()
    return {"exe": exe}


@router.get("/local")
def get_local():
    return [{"exe": exe} for exe in get_local_executables()]


@router.delete("/local")
def delete_local(req: ExeRequest, session: UserSession = Depends(get_session)):
    exe = req.exe.strip()
    remove_local_executable(exe)
    if session.dynawo_executable == exe:
        session.dynawo_executable = None
    return {"ok": True}
