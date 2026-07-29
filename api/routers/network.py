#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.concurrency import pypowsybl_slot
from api.dependencies import get_session
from api.session_store import UserSession
from backend.dyd_parser import parse_dyd
from backend.jobs_parser import find_final_state_iidm
from backend.network_diff import LEGEND as DIFF_LEGEND, DiffParams
from backend.network_loader import (
    get_searchable_elements,
    get_sld,
    get_sld_diff,
    load_network_from_path,
    network_area_diagram_diff_svg,
    network_area_diagram_svg,
    network_summary,
    voltage_level_ids,
)
from backend.par_parser import parse_par

router = APIRouter(tags=["network"])

_DYN_PALETTE = [
    "#4CAF50", "#2196F3", "#FF9800", "#E91E63",
    "#9C27B0", "#00BCD4", "#795548", "#FF5722",
]


def _lib_colors(dyn_models: dict) -> dict[str, str]:
    libs = sorted({info["lib"] for info in dyn_models.values()})
    return {lib: _DYN_PALETTE[i % len(_DYN_PALETTE)] for i, lib in enumerate(libs)}


def _get_dyn_models(session: UserSession) -> dict:
    dyd_files = [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == "dyd"]
    if not dyd_files:
        return {}
    raw = session.session_manager.get_raw(dyd_files[0])
    if not raw:
        return {}
    try:
        return parse_dyd(raw)
    except Exception:
        return {}


def _get_all_par_data(session: UserSession) -> dict:
    result: dict[str, dict] = {}
    for name, meta in session.uploaded_files_info.items():
        if not name.endswith(".par"):
            continue
        path = session.session_manager.get_path(name)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    result[name] = parse_par(fh.read())
            except Exception:
                pass
    return result


def _parse_feeders(metadata_json: str) -> dict[str, str]:
    """Extract {nodeId: nextVlId} for all feeder nodes in the SLD metadata."""
    try:
        meta = json.loads(metadata_json)
    except Exception:
        return {}
    feeders: dict[str, str] = {}
    for node in meta.get("nodes", []):
        nxt = node.get("nextVId")
        if nxt:
            feeders[node["id"]] = nxt
    return feeders


# ── Existing endpoints ─────────────────────────────────────────────────────────

@router.post("/load")
def load_network(filename: str, session: UserSession = Depends(get_session)):
    if not session.session_manager.has_file(filename):
        raise HTTPException(status_code=404, detail=f"{filename} not found in session")
    ftype = session.uploaded_files_info.get(filename, {}).get("ftype")
    if ftype != "iidm":
        raise HTTPException(status_code=400, detail=f"{filename} is not an IIDM file")
    path = session.session_manager.get_path(filename)
    try:
        session.network = load_network_from_path(path)
        session.network_name = filename
        session.nad_cache.clear()
        session.sld_cache.clear()
        session.diff_nad_cache.clear()
        session.diff_sld_cache.clear()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load network: {exc}")
    return {"ok": True, "filename": filename}


@router.get("/summary")
def get_summary(session: UserSession = Depends(get_session)):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    return {
        "filename": session.network_name,
        "summary": network_summary(session.network),
    }


# ── New endpoints ──────────────────────────────────────────────────────────────

@router.get("/voltage-levels")
def get_voltage_levels(session: UserSession = Depends(get_session)):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    return {"vl_ids": voltage_level_ids(session.network)}


def _nad_cache_key(base: str, vl_ids: list[str] | None, depth: int) -> str:
    scope = ",".join(sorted(vl_ids)) if vl_ids else "full"
    return f"{base}:{scope}:{depth}"


@router.get("/nad")
def get_nad(
    vl_ids: list[str] = Query(default=None),
    depth: int = 0,
    session: UserSession = Depends(get_session),
):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    cache_key = _nad_cache_key(session.network_name or "", vl_ids, depth)
    if cache_key in session.nad_cache:
        return Response(content=session.nad_cache[cache_key], media_type="image/svg+xml")
    with pypowsybl_slot():
        try:
            svg = network_area_diagram_svg(session.network, voltage_level_ids=vl_ids, depth=depth)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"NAD rendering failed: {exc}")
    session.nad_cache[cache_key] = svg
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/sld/{vl_id}")
def get_sld_endpoint(vl_id: str, session: UserSession = Depends(get_session)):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    if vl_id in session.sld_cache:
        return session.sld_cache[vl_id]
    with pypowsybl_slot():
        try:
            sld = get_sld(session.network, vl_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"SLD rendering failed: {exc}")
    result = {"svg": sld.svg, "feeders": _parse_feeders(sld.metadata)}
    session.sld_cache[vl_id] = result
    return result


@router.get("/elements")
def get_elements(session: UserSession = Depends(get_session)):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    return {"elements": get_searchable_elements(session.network)}


# ── Final-state IIDM endpoints ────────────────────────────────────────────────

def _load_final_state_network(session: UserSession, run_id: int):
    run = next((r for r in session.sim_runs if r.run_id == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    path = find_final_state_iidm(session.session_manager.working_dir, run.output_dir)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Final-state IIDM not found for run {run_id}",
        )
    try:
        return load_network_from_path(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load final-state IIDM: {exc}")


@router.get("/run/{run_id}/nad")
def get_final_state_nad(
    run_id: int,
    vl_ids: list[str] = Query(default=None),
    depth: int = 0,
    session: UserSession = Depends(get_session),
):
    cache_key = _nad_cache_key(f"run_{run_id}", vl_ids, depth)
    if cache_key in session.nad_cache:
        return Response(content=session.nad_cache[cache_key], media_type="image/svg+xml")
    network = _load_final_state_network(session, run_id)
    with pypowsybl_slot():
        try:
            svg = network_area_diagram_svg(network, voltage_level_ids=vl_ids, depth=depth)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"NAD rendering failed: {exc}")
    session.nad_cache[cache_key] = svg
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/run/{run_id}/voltage-levels")
def get_final_state_voltage_levels(run_id: int, session: UserSession = Depends(get_session)):
    network = _load_final_state_network(session, run_id)
    return {"vl_ids": voltage_level_ids(network)}


@router.get("/run/{run_id}/sld/{vl_id}")
def get_final_state_sld(run_id: int, vl_id: str, session: UserSession = Depends(get_session)):
    cache_key = f"run_{run_id}:{vl_id}"
    if cache_key in session.sld_cache:
        return session.sld_cache[cache_key]
    network = _load_final_state_network(session, run_id)
    with pypowsybl_slot():
        try:
            sld = get_sld(network, vl_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"SLD rendering failed: {exc}")
    result = {"svg": sld.svg, "feeders": _parse_feeders(sld.metadata)}
    session.sld_cache[cache_key] = result
    return result


@router.get("/run/{run_id}/elements")
def get_final_state_elements(run_id: int, session: UserSession = Depends(get_session)):
    network = _load_final_state_network(session, run_id)
    return {"elements": get_searchable_elements(network)}


# ── Named-file IIDM endpoints (e.g. Load Flow results) ────────────────────────

def _load_named_network(session: UserSession, filename: str):
    if not session.session_manager.has_file(filename):
        raise HTTPException(status_code=404, detail=f"{filename} not found in session")
    ftype = session.uploaded_files_info.get(filename, {}).get("ftype")
    if ftype != "iidm":
        raise HTTPException(status_code=400, detail=f"{filename} is not an IIDM file")
    path = session.session_manager.get_path(filename)
    try:
        return load_network_from_path(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load IIDM file: {exc}")


@router.get("/file/{filename:path}/nad")
def get_file_nad(
    filename: str,
    vl_ids: list[str] = Query(default=None),
    depth: int = 0,
    session: UserSession = Depends(get_session),
):
    cache_key = _nad_cache_key(f"file_{filename}", vl_ids, depth)
    if cache_key in session.nad_cache:
        return Response(content=session.nad_cache[cache_key], media_type="image/svg+xml")
    network = _load_named_network(session, filename)
    with pypowsybl_slot():
        try:
            svg = network_area_diagram_svg(network, voltage_level_ids=vl_ids, depth=depth)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"NAD rendering failed: {exc}")
    session.nad_cache[cache_key] = svg
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/file/{filename:path}/voltage-levels")
def get_file_voltage_levels(filename: str, session: UserSession = Depends(get_session)):
    network = _load_named_network(session, filename)
    return {"vl_ids": voltage_level_ids(network)}


@router.get("/file/{filename:path}/sld/{vl_id}")
def get_file_sld(filename: str, vl_id: str, session: UserSession = Depends(get_session)):
    cache_key = f"file_{filename}:{vl_id}"
    if cache_key in session.sld_cache:
        return session.sld_cache[cache_key]
    network = _load_named_network(session, filename)
    with pypowsybl_slot():
        try:
            sld = get_sld(network, vl_id)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"SLD rendering failed: {exc}")
    result = {"svg": sld.svg, "feeders": _parse_feeders(sld.metadata)}
    session.sld_cache[cache_key] = result
    return result


@router.get("/file/{filename:path}/elements")
def get_file_elements(filename: str, session: UserSession = Depends(get_session)):
    network = _load_named_network(session, filename)
    return {"elements": get_searchable_elements(network)}


# ── Diff endpoints (final-state vs. uploaded IIDM) ────────────────────────────

@router.get("/diff/legend")
def get_diff_legend():
    """Return the colour legend used by the diff visualisation."""
    return DIFF_LEGEND


_DEFAULT_QUANTITIES = ['voltage', 'current', 'active_power']


def _build_diff_params(
    moderate_pct: float,
    large_pct: float,
    moderate_colour: str,
    large_colour: str,
    neg_moderate_colour: str,
    neg_large_colour: str,
    signed: bool,
    quantities: list[str],
) -> DiffParams:
    return DiffParams(
        moderate_threshold=moderate_pct / 100,
        large_threshold=large_pct / 100,
        moderate_colour=moderate_colour,
        large_colour=large_colour,
        neg_moderate_colour=neg_moderate_colour,
        neg_large_colour=neg_large_colour,
        signed=signed,
        quantities=frozenset(quantities),
    )


def _params_digest(params: DiffParams) -> str:
    return ":".join([
        str(params.moderate_threshold), str(params.large_threshold),
        params.moderate_colour, params.large_colour,
        params.neg_moderate_colour, params.neg_large_colour,
        str(params.signed), ",".join(sorted(params.quantities)),
    ])


def _diff_nad_response(
    session: UserSession, source_key: str, net_compare, params: DiffParams,
    vl_ids: list[str] | None = None, depth: int = 0,
) -> Response:
    if session.network is None:
        raise HTTPException(status_code=404, detail="No reference network loaded in session")
    scope = ",".join(sorted(vl_ids)) if vl_ids else "full"
    cache_key = f"{source_key}:{_params_digest(params)}:{scope}:{depth}"
    if cache_key in session.diff_nad_cache:
        return Response(content=session.diff_nad_cache[cache_key], media_type="image/svg+xml")
    with pypowsybl_slot():
        try:
            svg = network_area_diagram_diff_svg(
                session.network, net_compare, params, voltage_level_ids=vl_ids, depth=depth)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Diff NAD rendering failed: {exc}")
    session.diff_nad_cache[cache_key] = svg
    return Response(content=svg, media_type="image/svg+xml")


def _diff_sld_response(session: UserSession, source_key: str, net_compare, vl_id: str, params: DiffParams) -> dict:
    if session.network is None:
        raise HTTPException(status_code=404, detail="No reference network loaded in session")
    cache_key = f"{source_key}:{vl_id}:{_params_digest(params)}"
    if cache_key in session.diff_sld_cache:
        return session.diff_sld_cache[cache_key]
    with pypowsybl_slot():
        try:
            sld = get_sld_diff(session.network, net_compare, vl_id, params)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Diff SLD rendering failed: {exc}")
    result = {"svg": sld.svg, "feeders": _parse_feeders(sld.metadata)}
    session.diff_sld_cache[cache_key] = result
    return result


@router.get("/run/{run_id}/diff/nad")
def get_diff_nad(
    run_id: int,
    moderate_pct: float = 1.0,
    large_pct: float = 5.0,
    moderate_colour: str = '#faad14',
    large_colour: str = '#f5222d',
    neg_moderate_colour: str = '#69c0ff',
    neg_large_colour: str = '#1890ff',
    signed: bool = False,
    quantities: list[str] = Query(default=_DEFAULT_QUANTITIES),
    vl_ids: list[str] = Query(default=None),
    depth: int = 0,
    session: UserSession = Depends(get_session),
):
    net_final = _load_final_state_network(session, run_id)
    params = _build_diff_params(
        moderate_pct, large_pct,
        moderate_colour, large_colour,
        neg_moderate_colour, neg_large_colour,
        signed, quantities,
    )
    return _diff_nad_response(session, f"run_{run_id}", net_final, params, vl_ids, depth)


@router.get("/run/{run_id}/diff/sld/{vl_id}")
def get_diff_sld(
    run_id: int,
    vl_id: str,
    moderate_pct: float = 1.0,
    large_pct: float = 5.0,
    moderate_colour: str = '#faad14',
    large_colour: str = '#f5222d',
    neg_moderate_colour: str = '#69c0ff',
    neg_large_colour: str = '#1890ff',
    signed: bool = False,
    quantities: list[str] = Query(default=_DEFAULT_QUANTITIES),
    session: UserSession = Depends(get_session),
):
    net_final = _load_final_state_network(session, run_id)
    params = _build_diff_params(
        moderate_pct, large_pct,
        moderate_colour, large_colour,
        neg_moderate_colour, neg_large_colour,
        signed, quantities,
    )
    return _diff_sld_response(session, f"run_{run_id}", net_final, vl_id, params)


@router.get("/file/{filename:path}/diff/nad")
def get_file_diff_nad(
    filename: str,
    moderate_pct: float = 1.0,
    large_pct: float = 5.0,
    moderate_colour: str = '#faad14',
    large_colour: str = '#f5222d',
    neg_moderate_colour: str = '#69c0ff',
    neg_large_colour: str = '#1890ff',
    signed: bool = False,
    quantities: list[str] = Query(default=_DEFAULT_QUANTITIES),
    vl_ids: list[str] = Query(default=None),
    depth: int = 0,
    session: UserSession = Depends(get_session),
):
    net_compare = _load_named_network(session, filename)
    params = _build_diff_params(
        moderate_pct, large_pct,
        moderate_colour, large_colour,
        neg_moderate_colour, neg_large_colour,
        signed, quantities,
    )
    return _diff_nad_response(session, f"file_{filename}", net_compare, params, vl_ids, depth)


@router.get("/file/{filename:path}/diff/sld/{vl_id}")
def get_file_diff_sld(
    filename: str,
    vl_id: str,
    moderate_pct: float = 1.0,
    large_pct: float = 5.0,
    moderate_colour: str = '#faad14',
    large_colour: str = '#f5222d',
    neg_moderate_colour: str = '#69c0ff',
    neg_large_colour: str = '#1890ff',
    signed: bool = False,
    quantities: list[str] = Query(default=_DEFAULT_QUANTITIES),
    session: UserSession = Depends(get_session),
):
    net_compare = _load_named_network(session, filename)
    params = _build_diff_params(
        moderate_pct, large_pct,
        moderate_colour, large_colour,
        neg_moderate_colour, neg_large_colour,
        signed, quantities,
    )
    return _diff_sld_response(session, f"file_{filename}", net_compare, vl_id, params)


@router.get("/dyn-models")
def get_dyn_models(session: UserSession = Depends(get_session)):
    models = _get_dyn_models(session)
    if not models:
        return {}
    colors = _lib_colors(models)
    par_data = _get_all_par_data(session)
    result = {}
    for sid, info in models.items():
        par_set = par_data.get(info["parFile"], {}).get(info["parId"], {"pars": [], "refs": []})
        result[sid] = {
            "dyn_id":  info["dyn_id"],
            "lib":     info["lib"],
            "parFile": info["parFile"],
            "parId":   info["parId"],
            "color":   colors.get(info["lib"], "#FF9800"),
            "pars":    par_set.get("pars", []),
            "refs":    par_set.get("refs", []),
        }
    return result
