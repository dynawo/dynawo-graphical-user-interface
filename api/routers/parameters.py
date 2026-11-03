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
from backend.jobs_parser import job_relative, read_network_par
from backend.models import ChangeLogEntry, ParameterChange
from backend.par_parser import macro_usage, parse_par, reference_names
from backend.par_writer import set_references, write_par_values

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


def _editable_models(session: UserSession) -> dict[str, dict]:
    """The .dyd models, then the network set of each jobs file, which no .dyd
    declares (the solver set has its own page). `kind` tells them apart."""
    models = {dyn_id: {**info, "kind": "model"} for dyn_id, info in get_dyd_models(session).items()}
    for name, meta in session.uploaded_files_info.items():
        path = session.session_manager.get_path(name)
        if meta.get("ftype") != "jobs" or not os.path.isfile(path):
            continue
        par_file, par_id = read_network_par(path)
        if par_file and par_id:
            dyn_id = f"NETWORK [{name}]"
            models[dyn_id] = {"dyn_id": dyn_id, "static_id": "", "lib": "DYNModelNetwork", "kind": "network",
                              "parFile": job_relative(name, par_file), "parId": par_id}
    return models


# IIDM data of the usual initial values, by suffix of the parameter name
# (Dynawo's naming), when no .par of the session tells otherwise.
_CONVENTIONAL_REFERENCES = {"P0Pu": "p_pu", "Q0Pu": "q_pu", "U0Pu": "v_pu", "UPhase0": "angle_pu"}


def _session_references(session: UserSession) -> dict[str, dict[str, int]]:
    """{parameter name: {origName: uses}} of the references of the session's .par files."""
    used: dict[str, dict[str, int]] = {}
    for name, meta in session.uploaded_files_info.items():
        path = session.session_manager.get_path(name)
        if meta.get("ftype") != "par" or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                pairs = reference_names(fh.read())
        except Exception:
            continue
        for par_name, orig_name in pairs:
            counts = used.setdefault(par_name, {})
            counts[orig_name] = counts.get(orig_name, 0) + 1
    return used


def _reference_suggestions(pars: list[dict], used: dict[str, dict[str, int]]) -> dict[str, str]:
    """origName suggested for each parameter: the one the session's .par
    files use most for that name, else the conventional one."""
    suggestions = {}
    for par in pars:
        name = par["name"]
        if name in used:
            suggestions[name] = max(used[name].items(), key=lambda item: item[1])[0]
        else:
            suffix = next((s for s in _CONVENTIONAL_REFERENCES if name.endswith("_" + s)), None)
            if suffix:
                suggestions[name] = _CONVENTIONAL_REFERENCES[suffix]
    return suggestions


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
    empty `static_id` and are editable like any other — then the network and
    the network set of each jobs file."""
    return [
        {"dyn_id": info["dyn_id"], "static_id": info["static_id"], "lib": info["lib"],
         "parFile": info["parFile"], "parId": info["parId"], "kind": info["kind"]}
        for info in _editable_models(session).values()
    ]


@router.get("/model/{model_id}")
def get_model_params(model_id: str, session: UserSession = Depends(get_session)):
    models = _editable_models(session)
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
    used = _session_references(session)
    return {
        **info, "pars": par_set["pars"], "refs": par_set["refs"],
        "siblings": siblings, "macro_id": macro_id, "macro_siblings": macro_siblings,
        "reference_suggestions": _reference_suggestions(par_set["pars"], used),
        "orig_names": sorted({o for counts in used.values() for o in counts} | set(_CONVENTIONAL_REFERENCES.values())),
    }


class ApplyRequest(BaseModel):
    values: dict[str, str]


@router.put("/model/{model_id}")
def apply_model_params(model_id: str, req: ApplyRequest, session: UserSession = Depends(get_session)):
    models = _editable_models(session)
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


class ReferencesRequest(BaseModel):
    # Parameter name -> origName of the IIDM reference replacing its value.
    to_reference: dict[str, str] = {}
    # References turned back into parameters to fill.
    to_value: list[str] = []


@router.put("/model/{model_id}/references")
def apply_model_references(model_id: str, req: ReferencesRequest, session: UserSession = Depends(get_session)):
    """Give parameters of the model's set by IIDM references, or back by values.
    Only the set's own entries: a macro's are shared with other sets."""
    models = _editable_models(session)
    if model_id not in models:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")
    info = models[model_id]
    par_path = session.session_manager.get_path(info["parFile"])
    if not os.path.isfile(par_path):
        raise HTTPException(status_code=404, detail=f"Par file {info['parFile']} not found")
    par_set = _get_par_set(session, info["parFile"], info["parId"])
    pars = {p["name"]: p for p in par_set["pars"]}
    refs = {r["name"]: r for r in par_set["refs"]}
    for name, orig_name in req.to_reference.items():
        if name not in pars or pars[name]["shared"]:
            raise HTTPException(status_code=400, detail=f"'{name}' is not a parameter of set {info['parId']} itself")
        if not orig_name.strip():
            raise HTTPException(status_code=400, detail=f"No IIDM data given for '{name}'")
    for name in req.to_value:
        if name not in refs or refs[name]["shared"]:
            raise HTTPException(status_code=400, detail=f"'{name}' is not a reference of set {info['parId']} itself")
    set_references(
        par_path, info["parId"],
        {name: (orig_name.strip(), pars[name]["type"] or "DOUBLE") for name, orig_name in req.to_reference.items()},
        {name: refs[name]["type"] or "DOUBLE" for name in req.to_value},
    )
    return {"references": len(req.to_reference), "values": len(req.to_value)}


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
