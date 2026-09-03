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
from backend.models import ChangeLogEntry, ParameterChange
from backend.par_parser import macro_usage, parse_par
from backend.par_writer import write_par_values

router = APIRouter(tags=["parameters"])


def _get_par_set(session: UserSession, par_file: str, par_id: str) -> dict:
    par_path = session.session_manager.get_path(par_file)
    if not os.path.isfile(par_path):
        return {"pars": [], "refs": [], "macro_id": None}
    with open(par_path, "rb") as fh:
        sets = parse_par(fh.read())
    return sets.get(par_id, {"pars": [], "refs": [], "macro_id": None})


def _get_macro_siblings(
    session: UserSession, info: dict, models: dict[str, dict], model_id: str, macro_id: str | None,
) -> list[str]:
    """dyn_ids of other dynamic models whose <set> references the same
    <macroParameterSet> as this one — i.e. models that an edit to one of
    this model's shared (macro-sourced) parameters will also affect, even
    though they don't share the same parId/<set> the way `siblings` does.
    """
    if not macro_id:
        return []
    par_path = session.session_manager.get_path(info["parFile"])
    if not os.path.isfile(par_path):
        return []
    with open(par_path, "rb") as fh:
        usage = macro_usage(fh.read())
    sharing_set_ids = set(usage.get(macro_id, [])) - {info["parId"]}
    return sorted({
        m["dyn_id"] for mid, m in models.items()
        if mid != model_id and m["parFile"] == info["parFile"] and m["parId"] in sharing_set_ids
    })


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/modified-files")
def get_modified_par_files(session: UserSession = Depends(get_session)):
    """Return names of uploaded par files whose current content differs from the original."""
    modified = []
    for name, meta in session.uploaded_files_info.items():
        if meta.get("ftype") != "par":
            continue
        orig = session.session_manager.get_raw(name)
        if orig is None:
            continue
        path = session.session_manager.get_path(name)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            if fh.read() != orig:
                modified.append(name)
    return modified


@router.get("/models")
def list_models(session: UserSession = Depends(get_session)):
    """Every dynamic model of the .dyd, whether or not it is bound to a network
    element — models with no staticId (OmegaRef, faults, events, …) carry an
    empty `static_id` and are editable like any other."""
    return [
        {"dyn_id": info["dyn_id"], "static_id": info["static_id"], "lib": info["lib"],
         "parFile": info["parFile"], "parId": info["parId"]}
        for info in get_dyd_models(session).values()
    ]


@router.get("/model/{model_id}")
def get_model_params(model_id: str, session: UserSession = Depends(get_session)):
    models = get_dyd_models(session)
    if model_id not in models:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    info = models[model_id]
    par_set = _get_par_set(session, info["parFile"], info["parId"])

    siblings = [
        m["dyn_id"] for mid, m in models.items()
        if mid != model_id
        and m["parFile"] == info["parFile"]
        and m["parId"] == info["parId"]
    ]
    macro_id = par_set.get("macro_id")
    macro_siblings = _get_macro_siblings(session, info, models, model_id, macro_id)
    return {
        **info, "pars": par_set["pars"], "refs": par_set["refs"],
        "siblings": siblings, "macro_id": macro_id, "macro_siblings": macro_siblings,
    }


class ApplyRequest(BaseModel):
    values: dict[str, str]


@router.put("/model/{model_id}")
def apply_model_params(model_id: str, req: ApplyRequest, session: UserSession = Depends(get_session)):
    models = get_dyd_models(session)
    if model_id not in models:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    info = models[model_id]
    par_path = session.session_manager.get_path(info["parFile"])
    if not os.path.isfile(par_path):
        raise HTTPException(status_code=404, detail=f"Par file {info['parFile']} not found")

    par_set = _get_par_set(session, info["parFile"], info["parId"])
    old_vals = {p["name"]: p["value"] for p in par_set.get("pars", [])}
    types = {p["name"]: p["type"] for p in par_set.get("pars", [])}
    diffs = [
        ParameterChange(name=k, old_value=old_vals.get(k, ""), new_value=v)
        for k, v in req.values.items()
        if old_vals.get(k, "") != v
    ]
    write_par_values(par_path, info["parId"], req.values, types)
    if diffs:
        session.par_change_log.append(ChangeLogEntry(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            dyn_id=info["dyn_id"],
            par_file=info["parFile"],
            set_id=info["parId"],
            changes=diffs,
        ))
    return {"changed": len(diffs)}


@router.post("/restore/{par_file:path}")
def restore_par_file(par_file: str, session: UserSession = Depends(get_session)):
    raw = session.session_manager.get_raw(par_file)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No original bytes for {par_file}")
    par_path = session.session_manager.get_path(par_file)
    with open(par_path, "wb") as fh:
        fh.write(raw)
    session.par_change_log = [e for e in session.par_change_log if e.par_file != par_file]
    return {"ok": True}


@router.get("/changelog")
def get_changelog(session: UserSession = Depends(get_session)):
    return [
        {
            "timestamp": e.timestamp, "dyn_id": e.dyn_id,
            "par_file": e.par_file, "set_id": e.set_id,
            "changes": [{"name": c.name, "old_value": c.old_value, "new_value": c.new_value}
                        for c in e.changes],
        }
        for e in session.par_change_log
    ]


@router.delete("/changelog/{dyn_id}")
def clear_changelog(dyn_id: str, session: UserSession = Depends(get_session)):
    session.par_change_log = [e for e in session.par_change_log if e.dyn_id != dyn_id]
    return {"ok": True}


@router.post("/changelog/{dyn_id}/revert/{timestamp}")
def revert_changelog_entry(
    dyn_id: str,
    timestamp: str,
    session: UserSession = Depends(get_session),
):
    log = session.par_change_log
    idx = next(
        (i for i, e in enumerate(log) if e.dyn_id == dyn_id and e.timestamp == timestamp),
        None,
    )
    if idx is None:
        raise HTTPException(status_code=404, detail="Log entry not found")

    entry = log[idx]
    par_path = session.session_manager.get_path(entry.par_file)
    if not os.path.isfile(par_path):
        raise HTTPException(status_code=404, detail=f"Par file {entry.par_file} not found")

    old_vals = {c.name: c.old_value for c in entry.changes}
    write_par_values(par_path, entry.set_id, old_vals)
    session.par_change_log = [e for i, e in enumerate(log) if i != idx]

    reverted_names = set(old_vals)
    later_overlap = any(
        any(c.name in reverted_names for c in e.changes)
        for e in session.par_change_log[idx:]
    )
    return {"ok": True, "warned": later_overlap}
