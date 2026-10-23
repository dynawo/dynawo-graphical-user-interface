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
import tempfile

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_session
from api.session_store import UserSession
from backend.association_custom import numeric_version
from backend.association_generate import read_dynawo_version
from backend.compatibility import (
    FULL_UPGRADE_ORIGIN,
    check_job,
    find_update_tool,
    latest_script,
    migration_changes,
    relink,
    run_update,
    update_tool_places,
    update_python,
)
from backend.user_config import load_user_config

router = APIRouter(tags=["compatibility"])

# Tag of the files written by an upgrade in uploaded_files_info.
UPGRADED = "upgrade"


class JobRequest(BaseModel):
    jobs_file: str


def _dynawo(session: UserSession) -> tuple[str, str]:
    """(executable, x.y.z version) of the selected Dynawo."""
    exe = session.dynawo_executable or load_user_config().get("dynawo_executable", "")
    if not exe or not os.path.isfile(exe):
        raise HTTPException(status_code=400, detail="No Dynawo selected: choose one in the Dynawo Version page first")
    try:
        return exe, numeric_version(read_dynawo_version(exe))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"Could not get the version of the selected Dynawo: {exc}")


def _jobs(session: UserSession, name: str) -> str:
    if session.uploaded_files_info.get(name, {}).get("ftype") != "jobs":
        raise HTTPException(status_code=404, detail=f"'{name}' is not a .jobs file of the session")
    return name


def _tool(exe: str) -> tuple[str, str] | None:
    tool, python = find_update_tool(exe), update_python()
    return (tool, python) if tool and python else None


def _tool_missing(exe: str) -> str:
    """Why the update tool cannot be used, and where it is looked for."""
    if find_update_tool(exe):
        return "the update tool needs a Python with lxml (pip install lxml)"
    places = ", ".join(os.path.dirname(p) for p in update_tool_places(exe))
    return f"Dynawo's update tool (updateXML/update.py) was not found in: {places}"


@router.get("/context")
def get_context(jobs_file: str, session: UserSession = Depends(get_session)):
    """Selected Dynawo version and the update tool used, if any."""
    exe, version = _dynawo(session)
    _jobs(session, jobs_file)
    tool = _tool(exe)
    return {
        "dynawo_version": version,
        "update_tool": tool is not None,
        "update_tool_missing": None if tool else _tool_missing(exe),
        "update_tool_path": os.path.dirname(tool[0]) if tool else None,
        "update_tool_latest": latest_script(tool[0]) if tool else None,
    }


@router.post("/check")
def post_check(req: JobRequest, session: UserSession = Depends(get_session)):
    """Compatibility of a job's files with the selected Dynawo, without running
    it: static check against its ddb, then what applying all of Dynawo's
    updates up to that version would change (nothing for files in its format)."""
    exe, version = _dynawo(session)
    _jobs(session, req.jobs_file)
    result = check_job(session.session_manager.working_dir, req.jobs_file, exe)
    result["dynawo_version"] = version
    result["migration"] = None
    tool = _tool(exe)
    if tool is None:
        result["versions"], result["versions_detail"] = "no_tool", _tool_missing(exe)
        return result
    try:
        result["migration"] = migration_changes(*tool, session.session_manager.working_dir, req.jobs_file,
                                                FULL_UPGRADE_ORIGIN, version)
        result["versions"] = "checked"
    except RuntimeError as exc:
        result["versions"], result["versions_detail"] = "error", str(exc)
    return result


@router.post("/upgrade")
def post_upgrade(req: JobRequest, session: UserSession = Depends(get_session)):
    """Upgrade a job's files to the selected Dynawo version by applying all of
    Dynawo's updates up to it. The new files go to a folder of the session
    named after the job and the version; the originals are kept. Upgrading
    again replaces that folder."""
    exe, version = _dynawo(session)
    jobs_file = _jobs(session, req.jobs_file)
    tool = _tool(exe)
    if tool is None:
        raise HTTPException(status_code=400, detail=f"Cannot upgrade: {_tool_missing(exe)}")
    folder = f"{os.path.splitext(jobs_file)[0]}_dynawo{version}"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result = run_update(*tool, session.session_manager.get_path(jobs_file), FULL_UPGRADE_ORIGIN, version, tmp)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=f"The update tool failed: {exc}")
        for name in [n for n, m in session.uploaded_files_info.items()
                     if m.get("origin") == UPGRADED and n.startswith(folder + "/")]:
            session.session_manager.remove_file(name)
            del session.uploaded_files_info[name]
        written = []
        for name, path in sorted(result["files"].items()):
            info = session.session_manager.add_file(f"{folder}/{name}", relink(path, set(result["files"])))
            session.uploaded_files_info[info.name] = {"size": info.size, "ftype": info.ftype,
                                                      "origin": UPGRADED, "dynawo_version": version}
            written.append(info.name)
    return {"folder": folder, "jobs_file": f"{folder}/{os.path.basename(jobs_file)}", "files": written,
            "tickets": result["tickets"], "dynawo_version": version}
