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
from api.routers.loadflow import restart_pypowsybl_pool
from api.session_store import UserSession
from backend.download_manager import DownloadManager
from backend.powsybl_config import (
    clear_dynaflow_home, get_dynaflow_home, set_dynaflow_home,
    get_starting_point_mode, set_starting_point_mode,
)
from backend.user_config import load_user_config, save_user_config

router = APIRouter(tags=["dynaflow_launcher"])

_VERSIONS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "dynaflow_versions.json"
)
_DOWNLOAD_BASE = os.path.join(
    os.path.expanduser("~"), ".config", "dynawo_ihm", "dynaflow_versions"
)


def _version_dir(os_key: str, version: str) -> str:
    return os.path.join(_DOWNLOAD_BASE, os_key, version)


def _is_downloaded(os_key: str, version: str) -> bool:
    d = _version_dir(os_key, version)
    return os.path.isdir(d) and bool(os.listdir(d))


def _find_dynaflow_home(extract_dir: str) -> str | None:
    """Return the directory containing dynaflow-launcher.sh (its parent),
    since DynaFlow's homeDir config points at a directory, not the script itself.
    Mirrors dynawo_version.py's _find_dynawo_exe, which looks for dynawo.sh the
    same way — the release packaging puts the launcher script next to bin/, lib/, etc.
    """
    for root, _, files in os.walk(extract_dir):
        for f in files:
            if f in ("dynaflow-launcher.sh", "dynaflow-launcher.bat"):
                path = os.path.join(root, f)
                if f.endswith(".sh") and not os.access(path, os.X_OK):
                    os.chmod(path, os.stat(path).st_mode | 0o111)
                return root
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
    active_home = get_dynaflow_home()
    result = []
    for os_key, versions in versions_data.items():
        for version in versions:
            if _is_downloaded(os_key, version):
                d = _version_dir(os_key, version)
                home = _find_dynaflow_home(d)
                in_use = bool(
                    home and active_home
                    and os.path.normpath(home) == os.path.normpath(active_home)
                )
                result.append({
                    "os_key": os_key,
                    "version": version,
                    "home_dir": home,
                    "in_use": in_use,
                })
    return result


class DownloadRequest(BaseModel):
    os_key: str
    version: str


@router.post("/download")
def start_download(req: DownloadRequest, session: UserSession = Depends(get_session)):
    existing: DownloadManager | None = session.active_dynaflow_download
    if existing is not None and existing.is_running():
        raise HTTPException(status_code=409, detail="A download is already in progress for this session")
    versions_data = _load_versions()
    url = versions_data.get(req.os_key, {}).get(req.version, {}).get("url", "")
    if not url:
        raise HTTPException(status_code=404, detail="Version not found")
    if _is_downloaded(req.os_key, req.version):
        raise HTTPException(status_code=409, detail="Already downloaded")
    dest_dir = _version_dir(req.os_key, req.version)
    dm = DownloadManager()
    dm.start(url, dest_dir)
    session.active_dynaflow_download = dm
    session.active_dynaflow_download_target = (req.os_key, req.version)
    return {"ok": True}


@router.get("/download/active")
def get_active_download(session: UserSession = Depends(get_session)):
    """Lets the frontend resume showing progress after a remount, instead of
    losing track of an in-progress download and risking a duplicate start."""
    dm: DownloadManager | None = session.active_dynaflow_download
    target = session.active_dynaflow_download_target
    if dm is None or not dm.is_running() or target is None:
        return {"os_key": None, "version": None}
    return {"os_key": target[0], "version": target[1]}


@router.get("/download/progress")
async def download_progress(session: UserSession = Depends(get_session)):
    async def event_stream():
        dm: DownloadManager | None = session.active_dynaflow_download
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
        session.active_dynaflow_download = None
        session.active_dynaflow_download_target = None

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/use/{os_key}/{version}")
def use_version(os_key: str, version: str):
    d = _version_dir(os_key, version)
    if not _is_downloaded(os_key, version):
        raise HTTPException(status_code=404, detail="Version not downloaded")
    home = _find_dynaflow_home(d)
    if not home:
        raise HTTPException(
            status_code=404,
            detail="dynaflow-launcher executable not found in extracted folder",
        )
    set_dynaflow_home(home)
    restart_pypowsybl_pool()
    # Read-merge-write: never clobber other keys (e.g. dynawo_executable) in config.json
    save_user_config({**load_user_config(), "dynaflow_launcher_version": f"{os_key}:{version}"})
    return {"home_dir": home}


@router.delete("/remove/{os_key}/{version}")
def remove_version(os_key: str, version: str):
    d = _version_dir(os_key, version)
    if not os.path.isdir(d):
        raise HTTPException(status_code=404, detail="Version not found on disk")
    # Versions baked into the Docker image are published here as symlinks by
    # docker-entrypoint.sh. rmtree refuses to act on a symlink, and deleting
    # the link would be pointless anyway since the next start recreates it.
    if os.path.islink(d):
        raise HTTPException(
            status_code=400,
            detail="This version ships with the application and cannot be removed",
        )
    active_home = get_dynaflow_home()
    shutil.rmtree(d)
    if active_home and os.path.normpath(active_home).startswith(os.path.normpath(d)):
        clear_dynaflow_home()
    return {"ok": True}


@router.get("/active")
def get_active():
    return {"home_dir": get_dynaflow_home()}


@router.get("/starting-point-mode")
def get_starting_point_mode_endpoint():
    return {"mode": get_starting_point_mode()}


class StartingPointModeRequest(BaseModel):
    mode: str


@router.post("/starting-point-mode")
def set_starting_point_mode_endpoint(req: StartingPointModeRequest):
    if req.mode not in ("WARM", "FLAT"):
        raise HTTPException(status_code=422, detail="mode must be WARM or FLAT")
    set_starting_point_mode(req.mode)
    restart_pypowsybl_pool()
    save_user_config({**load_user_config(), "dynaflow_starting_point_mode": req.mode})
    return {"ok": True, "mode": req.mode}


class HomeDirRequest(BaseModel):
    home_dir: str


@router.post("/home")
def set_home(req: HomeDirRequest):
    """Manually point at a dynaflow-launcher install outside this app's managed downloads."""
    home_dir = req.home_dir.strip()
    if not home_dir:
        raise HTTPException(status_code=400, detail="home_dir is required")
    if not os.path.isdir(home_dir):
        raise HTTPException(status_code=400, detail=f"Not a directory: {home_dir}")
    set_dynaflow_home(home_dir)
    restart_pypowsybl_pool()
    # Manual path is not one of our managed downloads — drop the tracked version key
    cfg = load_user_config()
    cfg.pop("dynaflow_launcher_version", None)
    save_user_config(cfg)
    return {"home_dir": home_dir}
