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
import re
import tempfile
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api.concurrency import pypowsybl_slot
from api.dependencies import get_session
from api.routers.loadflow import pypowsybl_pool
from api.session_store import UserSession
from backend.association_catalogue import get_catalogue
from backend.association_generate import (
    ORIGIN,
    adapt_dyd,
    adapt_jobs,
    dropped_assignments,
    generate_in_worker,
    read_dynawo_version,
    session_names,
    solver_type,
)
from backend.association_par import (
    FILE,
    GENERIC_PAR_PATH,
    MANUAL,
    assemble_par,
    check_rule_parameters,
    file_set_ids,
    for_powsybl,
    generic_par,
    set_ids,
    spec_key,
)
from backend.association_rules import (
    attribute_values,
    describe_attributes,
    element_table,
    get_element_type,
    load_element_types,
    models_for_element_type,
    resolve,
)
from backend.association_script import build_script
from backend.jobs_parser import read_network_par, read_simulation_config, read_solver_info
from backend.user_config import load_user_config

router = APIRouter(tags=["dynamic-association"])


class Condition(BaseModel):
    attribute: str
    operator: str
    value: Any = None


class Rule(BaseModel):
    element_type: str
    conditions: list[Condition] = []
    model: str
    # Parameter set source: "file" (par_file + parameter_set_id), "generic" or
    # "manual" (parameter_set_id, default: the model name).
    par_source: str = "file"
    par_file: str = ""
    parameter_set_id: str = ""


class RulesRequest(BaseModel):
    rules: list[Rule]


class GenerationOptions(BaseModel):
    # Reference .jobs for network/solver parameters, time window and outputs
    # (default: the session's only user .jobs).
    reference_jobs: str | None = None
    # Overrides of the reference job; without a file, an empty set is created.
    network_par_file: str | None = None
    network_par_id: str | None = None
    solver_lib: str | None = None
    solver_par_file: str | None = None
    solver_par_id: str | None = None
    start_time: float | None = None
    stop_time: float | None = None


class GenerateRequest(GenerationOptions):
    rules: list[Rule]

# Role of each generated .par, used to keep filled sets on regeneration.
_PAR_ROLES = {"models.par": "models", "network.par": "network", "solvers.par": "solver"}
_DEFAULT_SOLVER_LIB = "dynawo_SolverIDA"

# PowSyBl error for a missing parameter value, and the maximum number of
# placeholder rounds.
_MISSING_PARAMETER = re.compile(r"Parameter (\S+) not found in set (\S+)")
_MAX_PLACEHOLDER_ROUNDS = 20


def _catalogue() -> list[dict]:
    try:
        return get_catalogue()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read the pypowsybl model catalogue: {exc}")


def _network(session: UserSession):
    if session.network is None:
        raise HTTPException(status_code=404, detail="No network loaded")
    return session.network


def _element_type(type_id: str) -> dict:
    entry = get_element_type(type_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown element type '{type_id}'")
    return entry


def _is_generated(session: UserSession, name: str) -> bool:
    return session.uploaded_files_info.get(name, {}).get("origin") == ORIGIN


def _session_file(session: UserSession, name: str, what: str) -> str:
    if not session.session_manager.has_file(name):
        raise HTTPException(status_code=400, detail=f"{what} '{name}' is not in the session")
    return session.session_manager.get_path(name)


def _read(session: UserSession, name: str) -> bytes:
    """Current content of a session file (get_raw() returns the uploaded bytes)."""
    with open(session.session_manager.get_path(name), "rb") as fh:
        return fh.read()


def _user_par_names(session: UserSession) -> list[str]:
    """User .par files of the session, generated ones excluded."""
    return sorted(n for n, m in session.uploaded_files_info.items()
                  if m.get("ftype") == "par" and not _is_generated(session, n))


def _user_par_sets(session: UserSession) -> dict[str, set[str]]:
    """Set ids of each user .par file (cached per file version)."""
    sets = {}
    for name in _user_par_names(session):
        ids = file_set_ids(session.session_manager.get_path(name))
        if ids is not None:
            sets[name] = set(ids)
    return sets


def _generic_sets() -> set[str] | None:
    ids = file_set_ids(GENERIC_PAR_PATH)
    return set(ids) if ids is not None else None


def _tables(session: UserSession) -> dict:
    """Element tables cache of the loaded network, reset when it changes."""
    cache = session.association_tables
    if cache.get("network") is not session.network:
        cache.clear()
        cache.update(network=session.network, tables={})
    return cache["tables"]


def _table(session: UserSession, entry: dict):
    tables = _tables(session)
    if entry["id"] not in tables:
        tables[entry["id"]] = element_table(_network(session), entry)
    return tables[entry["id"]]


def _previous_generated_par(session: UserSession, role: str) -> bytes | None:
    name = next((n for n, m in session.uploaded_files_info.items()
                 if _is_generated(session, n) and m.get("role") == role), None)
    return _read(session, name) if name else None


def _reference_jobs(session: UserSession, requested: str | None) -> str | None:
    """Reference .jobs: the requested one, else the session's only user .jobs."""
    if requested:
        if session.uploaded_files_info.get(requested, {}).get("ftype") != "jobs":
            raise HTTPException(status_code=400, detail=f"'{requested}' is not a .jobs file of the session")
        return requested
    candidates = [n for n, m in session.uploaded_files_info.items()
                  if m.get("ftype") == "jobs" and not _is_generated(session, n)]
    return candidates[0] if len(candidates) == 1 else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/catalogue")
def get_association_catalogue(kind: Literal["equipment", "automation_system"] | None = None):
    """Model categories and models of the running pypowsybl build."""
    categories = _catalogue()
    if kind is not None:
        categories = [c for c in categories if c["kind"] == kind]
    return {"categories": categories}


@router.get("/element-types")
def get_element_types():
    """IIDM element types a rule can target."""
    return {"element_types": [{"id": t["id"], "label": t["label"]} for t in load_element_types()]}


@router.get("/element-types/{type_id}/attributes")
def get_element_type_attributes(type_id: str, session: UserSession = Depends(get_session)):
    """Attributes a condition can test on this element type."""
    entry = _element_type(type_id)
    with pypowsybl_slot():
        df = _table(session, entry)
    return {"element_type": type_id, "count": len(df), "attributes": describe_attributes(df)}


@router.get("/element-types/{type_id}/values")
def get_attribute_values(type_id: str, attribute: str, search: str = "", limit: int = 50,
                         session: UserSession = Depends(get_session)):
    """Values of an attribute containing `search`, for proposing ids and the like."""
    entry = _element_type(type_id)
    with pypowsybl_slot():
        df = _table(session, entry)
    if attribute not in df.columns:
        raise HTTPException(status_code=404, detail=f"Unknown attribute '{attribute}'")
    values, total = attribute_values(df, attribute, search, max(1, min(limit, 500)))
    return {"values": values, "total": total}


@router.get("/models")
def get_models(element_type: str | None = None):
    """Equipment models, those fitting `element_type` first."""
    entry = _element_type(element_type) if element_type else None
    return {"models": models_for_element_type(entry, _catalogue())}


@router.get("/rules")
def get_rules(session: UserSession = Depends(get_session)):
    return {"rules": session.association_rules}


@router.put("/rules")
def put_rules(req: RulesRequest, session: UserSession = Depends(get_session)):
    """Store the rules with the session."""
    session.association_rules = [r.model_dump() for r in req.rules]
    return {"rules": session.association_rules}


@router.get("/context")
def get_context(session: UserSession = Depends(get_session)):
    """Session state used by the page."""
    exe = session.dynawo_executable or load_user_config().get("dynawo_executable", "")
    user_jobs = sorted(n for n, m in session.uploaded_files_info.items()
                       if m.get("ftype") == "jobs" and not _is_generated(session, n))
    return {
        "network_file":    session.network_name if session.network is not None else None,
        "dynawo":          exe if exe and os.path.isfile(exe) else None,
        "jobs_files":      user_jobs,
        "default_reference_jobs": _reference_jobs(session, None),
        "generated_files": sorted(n for n in session.uploaded_files_info if _is_generated(session, n)),
        "script_available": session.association_last_generation is not None,
        # Absent keys: never set.
        "options":         session.association_options,
        "last_result":     session.association_last_result,
    }


@router.put("/options")
def put_options(req: GenerationOptions, session: UserSession = Depends(get_session)):
    """Store the generation settings with the session."""
    session.association_options = req.model_dump()
    return {"options": session.association_options}


@router.get("/parameter-sources")
def get_parameter_sources(session: UserSession = Depends(get_session)):
    """User .par files with their set ids, and the generic .par if available."""
    generic = file_set_ids(GENERIC_PAR_PATH)
    files = [{"name": n, "sets": file_set_ids(session.session_manager.get_path(n)) or []}
             for n in _user_par_names(session)]
    return {"files": files, "generic": {"available": generic is not None, "sets": generic or []}}


@router.post("/resolve")
def post_resolve(req: RulesRequest, session: UserSession = Depends(get_session)):
    """Apply the rules to the loaded network without generating."""
    network = _network(session)
    catalogue = _catalogue()
    par_sets, generic_sets = _user_par_sets(session), _generic_sets()
    with pypowsybl_slot():
        return resolve(network, [r.model_dump() for r in req.rules], catalogue,
                       lambda rule: check_rule_parameters(rule, par_sets, generic_sets), _tables(session))


def _network_or_solver_par(
    session: UserSession, par_file: str | None, set_id: str, role: str, what: str,
) -> tuple[bytes, str, bool]:
    """(.par content, set id, left to fill) for the network or solver parameters:
    the user's set, else an empty set (kept from the previous generation).
    """
    if par_file:
        _session_file(session, par_file, f"{what} parameters file")
        content = _read(session, par_file)
        if set_id not in set_ids(content):
            raise HTTPException(status_code=400, detail=f"{what} parameter set '{set_id}' is not in '{par_file}'")
        spec = (FILE, par_file, set_id)
        par, ids, _ = assemble_par([spec], {par_file: content}, None)
        return par, ids[spec], False
    spec = (MANUAL, "", set_id)
    par, ids, to_fill = assemble_par([spec], {}, None, _previous_generated_par(session, role))
    return par, ids[spec], bool(to_fill)


@router.post("/generate")
def post_generate(req: GenerateRequest, session: UserSession = Depends(get_session)):
    """Generate the association with PowSyBl and add the .jobs/.dyd/.par to the
    session, replacing previously generated files. Rules with errors are skipped.
    """
    network = _network(session)
    iidm_name = session.network_name
    _session_file(session, iidm_name, "Network file")

    # The fake Dynawo reports the version of the selected Dynawo.
    exe = session.dynawo_executable or load_user_config().get("dynawo_executable", "")
    if not exe or not os.path.isfile(exe):
        raise HTTPException(status_code=400, detail="No Dynawo selected: choose one in the Dynawo Version page first")
    try:
        dynawo_version = read_dynawo_version(exe)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=f"Could not get the version of the selected Dynawo: {exc}")

    # Settings from the reference job unless given.
    reference = _reference_jobs(session, req.reference_jobs)
    ref_network_par, ref_network_id, ref_solver, ref_config = (None, None, None, None)
    if reference:
        ref_path = session.session_manager.get_path(reference)
        ref_network_par, ref_network_id = read_network_par(ref_path)
        ref_solver = read_solver_info(ref_path)
        ref_config = read_simulation_config(ref_path)
    solver_lib = req.solver_lib or (ref_solver.lib if ref_solver else None) or _DEFAULT_SOLVER_LIB
    powsybl_solver = solver_type(solver_lib)
    if powsybl_solver is None:
        raise HTTPException(status_code=400, detail=f"Solver '{solver_lib}' is not supported by PowSyBl (IDA or SIM only)")
    start_time = req.start_time if req.start_time is not None else (ref_config.start_time if ref_config else 0.0)
    stop_time = req.stop_time if req.stop_time is not None else (ref_config.stop_time if ref_config else 30.0)
    if stop_time <= start_time:
        raise HTTPException(status_code=400, detail="stop_time must be greater than start_time")

    rules = [r.model_dump() for r in req.rules]
    par_sets, generic_sets = _user_par_sets(session), _generic_sets()
    network_par, network_id, network_to_fill = _network_or_solver_par(
        session, req.network_par_file or ref_network_par,
        req.network_par_id or ref_network_id or "Network", "network", "Network",
    )
    solver_par, solver_id, solver_to_fill = _network_or_solver_par(
        session, req.solver_par_file or (ref_solver.par_file if ref_solver else None),
        req.solver_par_id or (ref_solver.par_id if ref_solver else None) or powsybl_solver,
        "solver", "Solver",
    )

    # PowSyBl reads converted copies of the .par files (for_powsybl); the
    # session keeps the originals.
    with tempfile.TemporaryDirectory() as tmp_dir:
        def powsybl_copy(name: str, content: bytes, placeholders: dict[str, list[str]]) -> str:
            path = os.path.join(tmp_dir, name)
            with open(path, "wb") as fh:
                fh.write(for_powsybl(content, placeholders))
            return path

        with pypowsybl_slot():
            resolution = resolve(network, rules, _catalogue(),
                                 lambda rule: check_rule_parameters(rule, par_sets, generic_sets), _tables(session))
            if not resolution["assignments"]:
                raise HTTPException(status_code=400, detail="The rules put a model on no element: nothing to generate")
            specs = [spec_key(rules[a["rule"]]) for a in resolution["assignments"]]
            # Only the files the rules use are read.
            par_files = {f: _read(session, f) for source, f, _ in set(specs) if source == FILE}
            models_par, set_of, models_to_fill = assemble_par(
                specs, par_files, generic_par(), _previous_generated_par(session, "models"),
            )
            assignments = [{**a, "parameter_set_id": set_of[spec_key(rules[a["rule"]])]}
                           for a in resolution["assignments"]]

            # PowSyBl reads some values (e.g. generator_H): sets left to fill get a
            # placeholder per missing value and must be regenerated once filled.
            fillable = {
                "models.par":  set(models_to_fill),
                "network.par": {network_id} if network_to_fill else set(),
                "solvers.par": {solver_id} if solver_to_fill else set(),
            }
            placeholders: dict[str, dict[str, list[str]]] = {name: {} for name in fillable}
            for _ in range(_MAX_PLACEHOLDER_ROUNDS):
                result = pypowsybl_pool().submit(
                    generate_in_worker,
                    session.session_manager.get_path(iidm_name), assignments,
                    {
                        "parametersFile":         powsybl_copy("models.par", models_par, placeholders["models.par"]),
                        "network.parametersFile": powsybl_copy("network.par", network_par, placeholders["network.par"]),
                        "network.parametersId":   network_id,
                        "solver.type":            powsybl_solver,
                        "solver.parametersFile":  powsybl_copy("solvers.par", solver_par, placeholders["solvers.par"]),
                        "solver.parametersId":    solver_id,
                    },
                    start_time, stop_time, dynawo_version,
                ).result()
                missing = _MISSING_PARAMETER.search(result["error"] or "")
                if not missing:
                    break
                parameter, set_id = missing.groups()
                par_name = next((n for n, ids in fillable.items() if set_id in ids), None)
                if par_name is None or parameter in placeholders[par_name].get(set_id, []):
                    break  # missing from a user set: reported below
                placeholders[par_name].setdefault(set_id, []).append(parameter)
    if result["error"]:
        raise HTTPException(status_code=422, detail=f"PowSyBl could not generate the association: {result['error']}")
    result["files"].update({"models.par": models_par, "network.par": network_par, "solvers.par": solver_par})

    # Replace the previous generation, never a file of the user's.
    for name in [n for n in session.uploaded_files_info if _is_generated(session, n)]:
        session.session_manager.remove_file(name)
        del session.uploaded_files_info[name]
    taken = set(session.session_manager.files)
    names = session_names(sorted(result["files"]), taken)
    reference_bytes = _read(session, reference) if reference else None
    written = []
    for powsybl_name, data in sorted(result["files"].items()):
        if powsybl_name.endswith(".jobs"):
            data = adapt_jobs(data, names, iidm_name, reference_bytes)
        elif powsybl_name.endswith(".dyd"):
            data = adapt_dyd(data, names)
        name = names[powsybl_name]
        info = session.session_manager.add_file(name, data)
        session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype, "origin": ORIGIN}
        if powsybl_name in _PAR_ROLES:
            session.uploaded_files_info[name]["role"] = _PAR_ROLES[powsybl_name]
        written.append(name)

    to_fill = [{"file": names.get("models.par"), "set": s, "for": "models",
                "needed_by_powsybl": placeholders["models.par"].get(s, [])} for s in models_to_fill]
    if network_to_fill:
        to_fill.append({"file": names.get("network.par"), "set": network_id, "for": "network",
                        "needed_by_powsybl": placeholders["network.par"].get(network_id, [])})
    if solver_to_fill:
        to_fill.append({"file": names.get("solvers.par"), "set": solver_id, "for": "solver",
                        "needed_by_powsybl": placeholders["solvers.par"].get(solver_id, [])})
    dropped = dropped_assignments(assignments, result["files"]["powsybl_dynawo.dyd"], result["report"])
    catalogue_models = {m["name"]: c["name"] for c in _catalogue() for m in c["models"]}
    session.association_last_generation = {
        "rules": rules,
        # Set id of each rule in the generated models .par (None if skipped).
        "set_ids": [None if report["errors"] else set_of.get(spec_key(rule), spec_key(rule)[2] or None)
                    for rule, report in zip(rules, resolution["rules"])],
        "model_category": {r["model"]: catalogue_models[r["model"]] for r in rules if r["model"] in catalogue_models},
        "iidm": iidm_name,
        "models_par": names["models.par"],
        "network_par": names["network.par"], "network_id": network_id,
        "solver_type": powsybl_solver, "solver_par": names["solvers.par"], "solver_id": solver_id,
        "start": start_time, "stop": stop_time,
        "dynawo_version": dynawo_version,
    }
    session.association_last_result = {
        "files":          written,
        "jobs":           names.get("powsybl_dynawo.jobs"),
        "reference_jobs": reference,
        "dynawo_version": dynawo_version,
        "rules":          resolution["rules"],
        "assigned":       len(assignments) - len(dropped),
        "dropped":        dropped,
        "to_fill":        to_fill,
        "report":         result["report"],
    }
    return session.association_last_result


@router.get("/script")
def get_script(session: UserSession = Depends(get_session)):
    """Last generation as a standalone pypowsybl script."""
    if session.association_last_generation is None:
        raise HTTPException(status_code=404, detail="Nothing generated yet: generate the association first")
    network = _network(session)
    with pypowsybl_slot():
        script = build_script(session.association_last_generation, network)
    return Response(
        content=script, media_type="text/x-python",
        headers={"Content-Disposition": 'attachment; filename="dynamic_association.py"'},
    )
