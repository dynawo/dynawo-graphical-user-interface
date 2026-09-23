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
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_session
from api.dyd_models import get_dyd_models
from api.session_store import UserSession
from backend.desc_parser import get_lib_parameter_details, get_lib_symbols, get_lib_variable_types
from backend.dyd_parser import parse_dyd
from backend.events_catalogue import (
    CATALOGUE_PATH,
    event_covers_lib,
    explain_unresolved,
    load_events_catalogue,
    match_connection,
    patterns_resolve,
    resolve_fixed_parameter,
)
from backend.events_reader import read_events
from backend.events_writer import build_events_dyd, build_events_par, par_filename_for
from backend.jobs_parser import read_all_file_refs, remove_dyd_reference_from_jobs, write_dyd_reference_to_jobs
from backend.models import EventConnection, EventParameter, StagedEvent
from backend.network_loader import get_searchable_elements, load_network_from_path
from backend.par_parser import parse_par

router = APIRouter(tags=["events"])

# Dynawo's internal network model does have a descriptor, ddb/DYNModelNetwork.desc.xml,
# and it is what gives the network side of a connection its variables. They are
# declared once for every kind of element, the object's static id standing in as
# @ID@ — so a connection to a network object is that list with the id filled in.
NETWORK_MODEL_LIB = "DYNModelNetwork"
NETWORK_ID_PLACEHOLDER = "@ID@"

# IIDM element type (as get_searchable_elements spells it) → the equipment kind
# an events.xml <event appliesTo="…"> names. Types absent from this map simply
# have no event offered for them yet; adding one is a matter of extending the
# catalogue and this line.
_IIDM_TO_EQUIPMENT = {
    "line":                      "LINE",
    "two_winding_transformer":   "TRANSFORMER",
    "three_winding_transformer": "TRANSFORMER_3W",
    "load":                      "LOAD",
    "generator":                 "GENERATOR",
    "battery":                   "BATTERY",
    "shunt_compensator":         "SHUNT",
    "static_var_compensator":    "SVC",
    "dangling_line":             "DANGLING_LINE",
    "hvdc_line":                 "HVDC",
}

# Descriptor-less fallback for a dynamic model: the library name usually says
# what the model represents. Only consulted when no Dynawo executable is
# configured, so the page still lists something instead of going blank.
_LIB_KEYWORDS = [("line", "LINE"), ("transformer", "TRANSFORMER"),
                 ("load", "LOAD"), ("generator", "GENERATOR")]


# ── Session helpers ───────────────────────────────────────────────────────────

def _session_name(session: UserSession, ref: str | None) -> str | None:
    """Map a file reference read out of a jobs file to a session file name."""
    if not ref:
        return None
    if session.session_manager.has_file(ref):
        return ref
    base = os.path.basename(ref)
    return base if session.session_manager.has_file(base) else None


def _current_bytes(session: UserSession, name: str | None) -> bytes | None:
    """What a session file holds right now, read from disk.

    Not the bytes it was uploaded with: another page may have written to it
    since (the parameters page edits .par files in place), and merging events
    into a stale copy would undo those edits."""
    if not name or not session.session_manager.has_file(name):
        return None
    path = session.session_manager.get_path(name)
    if not os.path.isfile(path):
        return session.session_manager.get_raw(name)
    with open(path, "rb") as fh:
        return fh.read()


def _file_signature(session: UserSession, name: str) -> tuple:
    """What identifies a file's content cheaply: its size and modification time."""
    try:
        stat = os.stat(session.session_manager.get_path(name))
        return (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return (0, 0)


def _files_of_type(session: UserSession, ftype: str) -> list[str]:
    return [n for n, m in session.uploaded_files_info.items() if m.get("ftype") == ftype]


def _job_dyd_names(session: UserSession, jobs_file: str) -> list[str] | None:
    """The .dyd files a jobs file references, as session file names.

    None when none resolve, so the caller falls back to every .dyd of the
    session rather than showing an empty model list."""
    path = session.session_manager.get_path(jobs_file)
    if not os.path.isfile(path):
        return None
    names = [n for n in (_session_name(session, d) for d in read_all_file_refs(path)["dyd"]) if n]
    return names or None


def _dyd_models(session: UserSession, jobs_file: str | None) -> dict[str, dict]:
    """The .dyd models an event of this job may target, scoped to that job's
    own .dyd files so two jobs sharing a session don't show each other's.

    Cached on the .dyd files themselves: the object search asks for this on
    every keystroke, and parsing a case's .dyd again each time is what made the
    search lag behind the typing."""
    if jobs_file and not session.session_manager.has_file(jobs_file):
        raise HTTPException(status_code=404, detail=f"{jobs_file} not found in session")
    dyd_names = _job_dyd_names(session, jobs_file) if jobs_file else None
    names = sorted(dyd_names if dyd_names is not None else _files_of_type(session, "dyd"))
    key = tuple((n, _file_signature(session, n)) for n in names)

    cache = session.events_cache.setdefault("dyd_models", {})
    hit = cache.get(jobs_file or "")
    if hit and hit[0] == key:
        return hit[1]
    models = get_dyd_models(session, dyd_names)
    cache[jobs_file or ""] = (key, models)
    return models


_network_load_lock = threading.Lock()


def _ensure_network(session: UserSession):
    """The session's IIDM network, loading it on first use.

    The Events page needs the static ids without the user having visited the
    Network View first, so it loads the session's IIDM itself when nothing has
    yet. Returns None when the session has no IIDM, or it fails to load — the
    page then offers dynamic-model targets only.

    Serialized: the page's first render asks for the objects and for the event
    files at once, and both land here. Without the lock they would both load the
    same network — twice the work, on the one request where the user is already
    waiting, and two threads driving pypowsybl over the same file.
    """
    if session.network is not None:
        return session.network
    iidm_files = _files_of_type(session, "iidm")
    if not iidm_files:
        return None
    with _network_load_lock:
        # Another request may have loaded it while this one waited.
        if session.network is not None:
            return session.network
        try:
            session.network = load_network_from_path(session.session_manager.get_path(iidm_files[0]))
            session.network_name = iidm_files[0]
        except Exception:
            return None
    return session.network


def _iidm_types(session: UserSession) -> dict[str, str]:
    """{static id: IIDM element type} for the loaded network, or {}.

    Walking every element of a real network takes long enough that doing it per
    request — let alone per file of a request — is what makes the page feel
    stuck, so the answer is kept until another network is loaded."""
    network = _ensure_network(session)
    if network is None:
        return {}
    # Key and value are published together, in one assignment. Split over two —
    # key first, value after the scan — a second request arriving in between
    # would see the key it expects and read a value that is not there yet.
    key = (session.network_name, id(network))
    cache = session.events_cache
    hit = cache.get("iidm_types")
    if hit is not None and hit[0] == key:
        return hit[1]
    types = {e["id"]: e["type"] for e in get_searchable_elements(network)}
    # An HVDC link is an object of its own to the network model — events act on
    # the link, through the state of each converter — but the diagram search
    # get_searchable_elements serves has no use for it, so it is added here
    # rather than there.
    try:
        for hvdc_id in network.get_hvdc_lines().index:
            types[str(hvdc_id)] = "hvdc_line"
    except Exception:
        pass
    cache["iidm_types"] = (key, types)
    return types


# ── Catalogue helpers ─────────────────────────────────────────────────────────

_catalogue_cache: tuple[float, list[dict]] | None = None


def _catalogue_mtime() -> float:
    try:
        return os.stat(CATALOGUE_PATH).st_mtime
    except OSError:
        return 0.0


def _catalogue() -> list[dict]:
    """The parsed catalogue, re-read only when resources/events.xml changes.

    Every endpoint needs it, and editing the file during a session must still
    take effect — hence the mtime rather than a read-once."""
    global _catalogue_cache
    mtime = _catalogue_mtime()
    if _catalogue_cache is None or _catalogue_cache[0] != mtime:
        events = load_events_catalogue()
        if not events:
            raise HTTPException(status_code=500, detail="Event catalogue resources/events.xml is missing or invalid")
        _catalogue_cache = (mtime, events)
    return _catalogue_cache[1]


def _event_by_id(event_id: str) -> dict:
    event = next((e for e in _catalogue() if e["id"] == event_id), None)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Unknown event '{event_id}'")
    return event


def _infer_equipment_type(lib: str) -> str:
    low = lib.lower()
    return next((eq for kw, eq in _LIB_KEYWORDS if kw in low), "")


class _LibSymbols:
    """Per-request cache of ddb descriptor lookups, keyed by library name.

    A .dyd holds one model per network element but only a handful of distinct
    libraries, so reading each descriptor once keeps target listing cheap on a
    large network."""

    def __init__(self, exe: str | None):
        self.exe = exe if exe and os.path.isfile(exe) else None
        self._cache: dict[str, list[str]] = {}
        self._params: dict[str, list[dict]] = {}

    @property
    def available(self) -> bool:
        return self.exe is not None

    def variables(self, lib: str) -> list[str]:
        if not self.available:
            return []
        if lib not in self._cache:
            self._cache[lib] = get_lib_symbols(self.exe, lib)[0]
        return self._cache[lib]

    def network_variables(self, static_id: str) -> list[str]:
        """The network model's variables as they read for one object."""
        return [v.replace(NETWORK_ID_PLACEHOLDER, static_id)
                for v in self.variables(NETWORK_MODEL_LIB)]

    def variable_types(self, lib: str) -> dict[str, str]:
        """{variable: valueType} of a library — what a connection must agree on."""
        if not self.available:
            return {}
        key = f"types:{lib}"
        if key not in self._params:
            self._params[key] = get_lib_variable_types(self.exe, lib)
        return self._params[key]

    def network_variable_types(self, static_id: str) -> dict[str, str]:
        """The network model's variable types as they read for one object."""
        return {name.replace(NETWORK_ID_PLACEHOLDER, static_id): t
                for name, t in self.variable_types(NETWORK_MODEL_LIB).items()}

    def parameters(self, lib: str) -> list[dict]:
        if not self.available:
            return []
        key = f"params:{lib}"
        if key not in self._params:
            self._params[key] = get_lib_parameter_details(self.exe, lib)
        return self._params[key]

    def fixed_values(self, event: dict) -> tuple[dict[str, str], list[dict]]:
        """What the event imposes, under the names this install gives them.

        Returns (resolved, unresolved): {parameter name: value} for each
        <fixedParameter> that designates exactly one parameter of the event
        library's descriptor, and the ones that do not — kept so the page can
        say why a parameter it would normally set is in the form instead."""
        descriptor = self.parameters(event["lib"])
        resolved: dict[str, str] = {}
        unresolved: list[dict] = []
        for spec in event["fixed_parameters"]:
            name = resolve_fixed_parameter(spec, descriptor)
            if name is None:
                unresolved.append({"pattern": spec["pattern"] or spec["name"], "value": spec["value"]})
            else:
                resolved[name] = spec["value"]
        return resolved, unresolved


def _modelled_static_ids(models: dict[str, dict], events: list[dict]) -> set[str]:
    """The static ids a dynamic model stands for, and which the network model
    therefore does not simulate.

    Event models are left out although they may carry a staticId of their own —
    powsybl-dynawo writes the events of a security analysis that way. Such a
    model acts on the equipment, it does not replace it: counting it here would
    hide that equipment from the network list, and refuse an event on it."""
    event_libs = {e["lib"] for e in events}
    return {info["static_id"] for info in models.values()
            if info["static_id"] and info["lib"] not in event_libs}


def _fits_equipment(event: dict, equipment_type: str) -> bool:
    """Whether an event is meant for this kind of object.

    An event with no appliesTo fits any of them: what it can act on is decided
    by its patterns against the object's descriptor, not by the equipment it
    stands for — a switch-off signal is the same port whatever the model is."""
    return not event["equipment_type"] or event["equipment_type"] == equipment_type


def _events_for_network_target(events: list[dict], static_id: str, equipment_type: str, symbols: _LibSymbols) -> list[str]:
    """The network events offered for one IIDM object.

    The equipment kind selects them, and — when a Dynawo executable is
    configured — each one still has to be wirable: its patterns must find a
    variable on both the event library and the network model. An event whose
    library the install does not ship is dropped rather than offered and then
    failing at the parameter step."""
    candidates = [e for e in events if e["scope"] == "network" and _fits_equipment(e, equipment_type)]
    if not symbols.available:
        return [e["id"] for e in candidates]
    network_vars = symbols.network_variables(static_id)
    return [
        e["id"] for e in candidates
        if patterns_resolve(e, symbols.variables(e["lib"]), network_vars,
                            symbols.variable_types(e["lib"]), symbols.network_variable_types(static_id))
    ]


def _events_for_dynamic_target(events: list[dict], lib: str, equipment_type: str, symbols: _LibSymbols) -> list[str]:
    """The dynamic events wirable to one .dyd model.

    Decided on the library's descriptor rather than on the equipment kind: an
    event applies when the library declares a variable for each of its
    model-side connections. That is the real condition, and it keeps the
    catalogue free of a per-library list. Without a Dynawo executable there is
    no descriptor to check, so the equipment kind is used as a coarse fallback.
    """
    dynamic = [e for e in events if e["scope"] == "dynamic" and event_covers_lib(e, lib)]
    lib_vars = symbols.variables(lib)
    if not lib_vars:
        # No descriptor at all: without a Dynawo executable nothing can be
        # checked and the equipment kind is the only guide, but when one is
        # configured and still has no descriptor for this library, the model
        # cannot be wired — offering events for it only leads to a form whose
        # object side has nothing to pick.
        if symbols.available:
            return []
        return [e["id"] for e in dynamic if _fits_equipment(e, equipment_type)]
    return [e["id"] for e in dynamic
            if patterns_resolve(e, symbols.variables(e["lib"]), lib_vars,
                                symbols.variable_types(e["lib"]), symbols.variable_types(lib))]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/catalogue")
def get_catalogue(session: UserSession = Depends(get_session)):
    """The events resources/events.xml offers, with their family.

    `parametrisable` says whether the event's own parameter form can be built:
    it needs the library's ddb descriptor, hence a configured Dynawo executable.
    """
    events = _catalogue()
    exe = session.dynawo_executable
    available = bool(exe and os.path.isfile(exe))
    return {
        "dynawo_available": available,
        "events": [
            {
                "id":             e["id"],
                "scope":          e["scope"],
                "family_label":   e["family_label"],
                "label":          e["label"],
                "lib":            e["lib"],
                "equipment_type": e["equipment_type"],
                "parametrisable": available,
            }
            for e in events
        ],
        "families": [
            {"scope": s, "label": next(e["family_label"] for e in events if e["scope"] == s),
             "description": next(e["family_description"] for e in events if e["scope"] == s)}
            for s in dict.fromkeys(e["scope"] for e in events)
        ],
    }


def _note_unsupported(collected: dict, events: list[dict], scope: str, label: str, equipment: str,
                      symbols: _LibSymbols, target_variables: list[str], target_types: dict[str, str]) -> None:
    """Record that objects of this library, or of this kind, are offered nothing.

    Counted per group and explained once: the reason is a property of the
    library or of the equipment kind, never of the individual object."""
    key = (scope, label)
    if key in collected:
        collected[key]["count"] += 1
        return

    candidates = [e for e in events if e["scope"] == scope and _fits_equipment(e, equipment)
                  and (scope != "dynamic" or event_covers_lib(e, label))]
    if not candidates:
        reason = "no event for this kind of object"
    elif not symbols.available:
        reason = "no Dynawo executable configured"
    elif not target_variables:
        reason = "no descriptor in the installed Dynawo"
    else:
        reasons = [
            explain_unresolved(e, symbols.variables(e["lib"]), target_variables,
                               symbols.variable_types(e["lib"]), target_types)
            for e in candidates
        ]
        reason = next((r for r in reasons if r), "its ports do not match")
    collected[key] = {"scope": scope, "label": label, "count": 1, "reason": reason}


@router.get("/targets")
def list_targets(
    jobs_file: str | None = None,
    q: str = "",
    kind: str | None = None,
    limit: int = 200,
    session: UserSession = Depends(get_session),
):
    """The objects an event can be applied to, matching the search `q`.

    Two kinds, which is exactly the choice the user makes on the page:
      * kind="network" — an IIDM id the network model simulates itself;
      * kind="dynamic" — the id of a blackBoxModel of the job's .dyd files.
    The two never overlap. An IIDM object a .dyd model is declared for is
    simulated by that model, not by the network one, so an event wired to
    NETWORK for it would act on nothing: it is offered under its model only.

    Filtered and capped server-side: a large network has tens of thousands of
    ids, and the page only ever shows a searchable list of them.
    """
    events = _catalogue()
    symbols = _LibSymbols(session.dynawo_executable)
    needle = q.strip().lower()
    # Why objects are missing from the list, gathered per library and per kind of
    # equipment rather than per object: a case has thousands of models and a
    # handful of libraries, and the answer is the same for all models of one.
    unsupported: dict[tuple, dict] = {}
    models = _dyd_models(session, jobs_file)
    iidm_types = _iidm_types(session)
    modelled_static_ids = _modelled_static_ids(models, events)

    targets: list[dict] = []

    excluded_modelled = 0
    if kind in (None, "network"):
        for static_id, iidm_type in iidm_types.items():
            equipment = _IIDM_TO_EQUIPMENT.get(iidm_type, "")
            if static_id in modelled_static_ids:
                # Counted rather than silently dropped, so the page can say why
                # an object the user expects is not in the list.
                if _events_for_network_target(events, static_id, equipment, symbols):
                    excluded_modelled += 1
                continue
            event_ids = _events_for_network_target(events, static_id, equipment, symbols)
            # Only equipment the catalogue could speak about is reported: a
            # voltage level or a busbar is not something an event acts on, and
            # listing them as unsupported would bury what matters.
            if not event_ids and iidm_type in _IIDM_TO_EQUIPMENT:
                _note_unsupported(unsupported, events, "network", equipment or iidm_type,
                                  equipment, symbols, symbols.network_variables(static_id),
                                  symbols.network_variable_types(static_id))
            if event_ids:
                targets.append({
                    "kind":           "network",
                    "id":             static_id,
                    "equipment_type": equipment,
                    "iidm_type":      iidm_type,
                    "lib":            None,
                    "static_id":      static_id,
                    "event_ids":      event_ids,
                })

    if kind in (None, "dynamic"):
        # The events already written into the job are models like any other, and
        # a job may declare several .dyd files holding some. They are not
        # equipment an event can act on, so they are left out of the list — and
        # out of what it reports as unsupported, where they would be noise.
        event_libs = {e["lib"] for e in events}
        for dyn_id, info in models.items():
            lib = info["lib"]
            if lib in event_libs:
                continue
            iidm_type = iidm_types.get(info["static_id"], "")
            equipment = _IIDM_TO_EQUIPMENT.get(iidm_type) or _infer_equipment_type(lib)
            event_ids = _events_for_dynamic_target(events, lib, equipment, symbols)
            if not event_ids:
                _note_unsupported(unsupported, events, "dynamic", lib, equipment, symbols,
                                  symbols.variables(lib), symbols.variable_types(lib))
            if event_ids:
                targets.append({
                    "kind":           "dynamic",
                    "id":             dyn_id,
                    "equipment_type": equipment,
                    "iidm_type":      iidm_type,
                    "lib":            lib,
                    "static_id":      info["static_id"],
                    "event_ids":      event_ids,
                })

    if needle:
        targets = [t for t in targets
                   if needle in t["id"].lower() or needle in (t["lib"] or "").lower()]
    targets.sort(key=lambda t: (t["kind"], t["id"]))
    total = len(targets)
    return {
        "targets":           targets[:max(limit, 0)],
        "total":             total,
        "truncated":         total > max(limit, 0),
        "network_loaded":    bool(iidm_types),
        "network_file":      session.network_name,
        "dyd_model_count":   len(models),
        # IIDM objects with network events in the catalogue, left out because a
        # dynamic model represents them.
        "excluded_modelled": excluded_modelled,
        # Objects no event of the catalogue can act on, and why.
        "unsupported":       sorted(unsupported.values(), key=lambda u: -u["count"])[:10],
        "dynawo_available":  symbols.available,
    }


@router.get("/form")
def get_event_form(
    event_id: str,
    target_id: str,
    kind: str,
    jobs_file: str | None = None,
    session: UserSession = Depends(get_session),
):
    """The parameter form and the connection proposal for one event on one object.

    Parameters come from the event library's ddb/<lib>.desc.xml — the same
    descriptor source the curves page reads — so the form always matches the
    installed Dynawo, and the catalogue has no say in them. Read-only parameters
    are dropped (Dynawo computes them) and so is event_nbEventVariables, which
    the descriptor fixes for the library.

    Connections are proposed, not imposed. Each <connectPattern> is searched in
    the variables of both sides — the event library's on one, the target's on
    the other — and `variables` carries those two lists in full, so the page can
    let the user wire the event himself whenever the catalogue does not allow
    it (`automatic` false) or the proposal does not convince him. Nothing is
    written yet.
    """
    if kind not in ("network", "dynamic"):
        raise HTTPException(status_code=422, detail="kind must be 'network' or 'dynamic'")
    event = _event_by_id(event_id)
    if event["scope"] != kind:
        raise HTTPException(
            status_code=422,
            detail=f"Event '{event_id}' applies to a {event['scope']} object, not a {kind} one",
        )

    symbols = _LibSymbols(session.dynawo_executable)
    lib = None
    if kind == "dynamic":
        info = _dyd_models(session, jobs_file).get(target_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"No dynamic model '{target_id}' in the .dyd files")
        lib = info["lib"]
        target_variables = symbols.variables(lib)
        target_types = symbols.variable_types(lib)
    else:
        target_variables = symbols.network_variables(target_id)
        target_types = symbols.network_variable_types(target_id)

    event_variables = symbols.variables(event["lib"])
    event_types = symbols.variable_types(event["lib"])
    descriptor = get_lib_parameter_details(symbols.exe, event["lib"]) if symbols.available else []

    # A fixed value that does not designate exactly one parameter of this
    # install's descriptor is not imposed: the parameter stays in the form,
    # with no default, for the user to set.
    fixed, unresolved_fixed = symbols.fixed_values(event)
    fields = [
        {"name": p["name"], "value_type": p["value_type"], "default": p["default"]}
        for p in descriptor
        if not p["read_only"] and p["name"] != "event_nbEventVariables" and p["name"] not in fixed
    ]

    connections = [
        match_connection(pattern, event_variables, target_variables, event_types, target_types)
        for pattern in event["patterns"]
    ]

    return {
        "event": {
            "id":             event["id"],
            "label":          event["label"],
            "scope":          event["scope"],
            "lib":            event["lib"],
            "equipment_type": event["equipment_type"],
            # Whether the catalogue allows the page to propose the wiring at
            # all; the page still offers to switch it off per event.
            "automatic":      event["automatic"],
            "destination":    event["destination"],
        },
        # What the second side of a connection is, spelled as the .dyd will: the
        # network model is one model for every object, a dynamic target is itself.
        "connect_to":           "NETWORK" if kind == "network" else target_id,
        "target":               {"id": target_id, "kind": kind, "lib": lib},
        "descriptor_available": symbols.available,
        "descriptor_found":     bool(descriptor),
        "network_model_found":  bool(target_variables) if kind == "network" else None,
        "fields":               fields,
        # Values the event imposes — shown, never edited: they are what makes
        # it this event rather than another using the same library.
        "fixed":                [{"name": n, "value": v} for n, v in fixed.items()],
        "unresolved_fixed":     unresolved_fixed,
        "connections":          connections,
        "variables":            {"event": event_variables, "target": target_variables},
        # So a user wiring by hand is only offered variables of the same type as
        # the one picked on the other side.
        "variable_types":       {"event": event_types, "target": target_types},
    }



# ── Staging ───────────────────────────────────────────────────────────────────
#
# An event is composed, added to a list, and only written when the user asks for
# the files: a study usually applies several events at once, and they all land in
# the same .dyd and .par. Until then they live on the session as StagedEvent.

class ConnectionItem(BaseModel):
    var1: str
    var2: str


class StageRequest(BaseModel):
    event_id: str
    target_id: str
    kind: str
    # Values for the parameters of the event library's descriptor, by name.
    parameters: dict[str, str] = {}
    # The wiring as the page settled it: proposed from the catalogue's patterns,
    # or picked by the user. Never re-derived here, so what is written is what
    # was shown.
    connections: list[ConnectionItem] = []
    # Overrides the generated blackBoxModel id.
    model_id: str | None = None
    jobs_file: str | None = None


class UpdateRequest(BaseModel):
    """A change to a staged event. Every field is optional: what is left out
    keeps the value the event was staged with."""
    parameters: dict[str, str] | None = None
    connections: list[ConnectionItem] | None = None
    model_id: str | None = None
    jobs_file: str | None = None


class WriteRequest(BaseModel):
    dyd_filename: str
    # The job to declare the new .dyd in. None writes the files without touching
    # any job — useful to inspect them before wiring them in.
    jobs_file: str | None = None
    # Replace files of that name already in the session (a previous write).
    overwrite: bool = False


def _suggested_model_id(event: dict, target_id: str) -> str:
    """A readable, unique-per-object id for the blackBoxModel to create.

    The scope prefix of the catalogue id is dropped — it is already implied by
    the object the event is attached to — leaving e.g. EVT_L1-2-1_line_disconnection."""
    short = event["id"]
    for prefix in ("network_", "dynamic_"):
        if short.startswith(prefix):
            short = short[len(prefix):]
    return f"EVT_{target_id}_{short}"


def _unique_model_id(session: UserSession, base: str, exclude_entry_id: str | None = None) -> str:
    """`base`, suffixed if a .dyd of the session or another staged event took it.

    Model ids must be unique across the whole job, not just across the file being
    written: a duplicate would make Dynawo reject the .dyd. `exclude_entry_id`
    leaves out the event being edited, so re-applying its own id is not treated
    as a clash with itself."""
    taken = set(_dyd_models(session, None)) | {
        e.model_id for e in session.staged_events if e.entry_id != exclude_entry_id
    }
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _typed_parameters(symbols: _LibSymbols, lib: str, values: dict[str, str],
                      fixed: dict[str, str] | None = None) -> list[EventParameter]:
    """Pair each submitted value with the type its descriptor declares.

    The descriptor is what types a .par entry, so a value for a parameter it does
    not declare — or one Dynawo computes itself — is refused rather than written
    into a file Dynawo would then reject. Values are checked against the type
    here too, for the same reason: a malformed .par only fails much later, in the
    middle of a simulation."""
    if not symbols.available:
        raise HTTPException(
            status_code=422,
            detail="No Dynawo executable is configured: the parameters of an event cannot be typed without its descriptor.",
        )
    descriptor = {p["name"]: p for p in get_lib_parameter_details(symbols.exe, lib)}
    if not descriptor:
        raise HTTPException(status_code=422, detail=f"The configured Dynawo install has no ddb/{lib}.desc.xml")
    # What the event imposes wins over whatever was sent: a connection event
    # posted with event_open=true would silently become a disconnection.
    values = {**values, **(fixed or {})}

    parameters: list[EventParameter] = []
    for name, raw in values.items():
        spec = descriptor.get(name)
        if spec is None:
            raise HTTPException(status_code=422, detail=f"{lib} declares no parameter '{name}'")
        if spec["read_only"]:
            raise HTTPException(status_code=422, detail=f"'{name}' is computed by Dynawo and cannot be set")
        value = raw.strip()
        if not value:
            raise HTTPException(status_code=422, detail=f"'{name}' has no value")
        _check_value(name, value, spec["value_type"])
        parameters.append(EventParameter(name=name, value=value, type=spec["value_type"]))

    missing = [n for n, p in descriptor.items()
               if not p["read_only"] and n != "event_nbEventVariables" and n not in values]
    if missing:
        raise HTTPException(status_code=422, detail=f"No value given for {', '.join(sorted(missing))}")
    return parameters


def _check_value(name: str, value: str, value_type: str) -> None:
    try:
        if value_type == "BOOL":
            if value.lower() not in ("true", "false"):
                raise ValueError
        elif value_type == "INT":
            int(value)
        elif value_type == "DOUBLE":
            float(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {value_type} for {name}")


@router.get("/staged")
def list_staged(session: UserSession = Depends(get_session)):
    """The events composed so far, in the order they will be written."""
    return {"events": [_staged_view(e) for e in session.staged_events]}


def _staged_view(e: StagedEvent) -> dict:
    return {
        "entry_id":    e.entry_id,
        "event_id":    e.event_id,
        "label":       e.label,
        "target_id":   e.target_id,
        "kind":        e.kind,
        "model_id":    e.model_id,
        "lib":         e.lib,
        "parameters":  [{"name": p.name, "value": p.value, "type": p.type} for p in e.parameters],
        "connections": [{"var1": c.var1, "id2": c.id2, "var2": c.var2} for c in e.connections],
    }


@router.post("/staged")
def stage_event(req: StageRequest, session: UserSession = Depends(get_session)):
    """Add one composed event to the list waiting to be written."""
    event = _validated_event(req.event_id, req.kind)
    symbols = _LibSymbols(session.dynawo_executable)
    target_variables = _target_variables(session, symbols, req.kind, req.target_id, req.jobs_file)

    staged = StagedEvent(
        event_id=event["id"],
        label=event["label"],
        target_id=req.target_id,
        kind=req.kind,
        model_id=_unique_model_id(session, (req.model_id or "").strip() or _suggested_model_id(event, req.target_id)),
        lib=event["lib"],
        parameters=_typed_parameters(symbols, event["lib"], req.parameters, symbols.fixed_values(event)[0]),
        connections=_validated_connections(req.connections, event, req.kind, req.target_id,
                                           symbols.variables(event["lib"]), target_variables),
    )
    session.staged_events.append(staged)
    return _staged_view(staged)


@router.put("/staged/{entry_id}")
def update_staged_event(entry_id: str, req: UpdateRequest, session: UserSession = Depends(get_session)):
    """Change a staged event: its parameters, its wiring, or the model id it will take.

    The event and the object it acts on are not editable — those are what the
    first three steps of the page choose, and changing them makes it a different
    event; removing it and composing another is the way. Everything else is
    re-validated exactly as it was when staged, so an edit cannot slip a value
    past the checks the descriptor imposes. The entry keeps its place in the
    list, since that is the order the .dyd will be written in.
    """
    index = next((i for i, e in enumerate(session.staged_events) if e.entry_id == entry_id), None)
    if index is None:
        raise HTTPException(status_code=404, detail="No staged event with that id")

    current = session.staged_events[index]
    event = _validated_event(current.event_id, current.kind)
    symbols = _LibSymbols(session.dynawo_executable)
    target_variables = _target_variables(session, symbols, current.kind, current.target_id, req.jobs_file)

    model_id = (req.model_id or "").strip() or current.model_id
    session.staged_events[index] = StagedEvent(
        entry_id=current.entry_id,
        event_id=current.event_id,
        label=current.label,
        target_id=current.target_id,
        kind=current.kind,
        model_id=(model_id if model_id == current.model_id
                  else _unique_model_id(session, model_id, exclude_entry_id=entry_id)),
        lib=current.lib,
        parameters=(_typed_parameters(symbols, current.lib, req.parameters, symbols.fixed_values(event)[0])
                    if req.parameters is not None else current.parameters),
        connections=(_validated_connections(req.connections, event, current.kind, current.target_id,
                                            symbols.variables(current.lib), target_variables)
                     if req.connections is not None else current.connections),
    )
    return _staged_view(session.staged_events[index])


def _validated_event(event_id: str, kind: str) -> dict:
    if kind not in ("network", "dynamic"):
        raise HTTPException(status_code=422, detail="kind must be 'network' or 'dynamic'")
    event = _event_by_id(event_id)
    if event["scope"] != kind:
        raise HTTPException(
            status_code=422,
            detail=f"Event '{event_id}' applies to a {event['scope']} object, not a {kind} one",
        )
    return event


def _modelled_by_id(models: dict[str, dict], events: list[dict]) -> set[str]:
    """Ids of the models that stand for a piece of equipment — events excluded."""
    event_libs = {e["lib"] for e in events}
    return {dyn_id for dyn_id, info in models.items()
            if info["static_id"] and info["lib"] not in event_libs}


def _target_variables(session: UserSession, symbols: _LibSymbols, kind: str,
                      target_id: str, jobs_file: str | None) -> list[str]:
    """The variables the object offers the second side of a connection.

    A network event on an object a dynamic model represents is refused here: the
    network model does not simulate that object, so the NETWORK variable the
    event would be wired to exists in name only."""
    if kind == "network":
        models = _dyd_models(session, jobs_file)
        modelled = next((m for m in models.values()
                         if m["static_id"] == target_id
                         and m["dyn_id"] in _modelled_by_id(models, _catalogue())), None)
        if modelled is not None:
            raise HTTPException(
                status_code=422,
                detail=f"{target_id} is represented by the dynamic model {modelled['dyn_id']} "
                       f"({modelled['lib']}); a network event would not act on it",
            )
        return symbols.network_variables(target_id)
    info = _dyd_models(session, jobs_file).get(target_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No dynamic model '{target_id}' in the .dyd files")
    return symbols.variables(info["lib"])


def _validated_connections(items: list["ConnectionItem"], event: dict, kind: str, target_id: str,
                           event_variables: list[str], target_variables: list[str]) -> list[EventConnection]:
    """Check each wire against what the two sides declare, and name the second one.

    Taken as given rather than re-derived from the catalogue's patterns: the page
    may have proposed them, but the user is free to have picked others, and what
    gets written must be what was shown."""
    if not items:
        raise HTTPException(status_code=422, detail="An event with no connection would do nothing")
    # "NETWORK" is the id Dynawo's internal network model answers to in a .dyd;
    # a dynamic event connects to the model the user picked.
    id2 = "NETWORK" if kind == "network" else target_id
    connections: list[EventConnection] = []
    for c in items:
        var1, var2 = c.var1.strip(), c.var2.strip()
        if not var1 or not var2:
            raise HTTPException(status_code=422, detail="Both sides of a connection must name a variable")
        if event_variables and var1 not in event_variables:
            raise HTTPException(status_code=422, detail=f"{event['lib']} declares no variable '{var1}'")
        if target_variables and var2 not in target_variables:
            raise HTTPException(status_code=422, detail=f"{id2} declares no variable '{var2}'")
        connections.append(EventConnection(var1=var1, id2=id2, var2=var2))
    return connections


@router.delete("/staged/{entry_id}")
def unstage_event(entry_id: str, session: UserSession = Depends(get_session)):
    before = len(session.staged_events)
    session.staged_events = [e for e in session.staged_events if e.entry_id != entry_id]
    if len(session.staged_events) == before:
        raise HTTPException(status_code=404, detail="No staged event with that id")
    return {"ok": True, "remaining": len(session.staged_events)}


@router.delete("/staged")
def clear_staged(session: UserSession = Depends(get_session)):
    session.staged_events = []
    return {"ok": True}


# ── Writing ───────────────────────────────────────────────────────────────────

def _suggested_dyd_filename(session: UserSession, jobs_file: str | None) -> str:
    """A free .dyd name for the events, named after the job when there is one."""
    stem = os.path.splitext(jobs_file)[0] if jobs_file else "events"
    base = f"{stem}_events" if jobs_file else "events"
    name = f"{base}.dyd"
    i = 2
    while session.session_manager.has_file(name) or session.session_manager.has_file(par_filename_for(name)):
        name = f"{base}_{i}.dyd"
        i += 1
    return name


@router.get("/write-info")
def get_write_info(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    """What the write form needs: a free filename and the jobs to choose from."""
    return {
        "suggested_filename": _suggested_dyd_filename(session, jobs_file),
        "jobs_files":         _files_of_type(session, "jobs"),
        "staged_count":       len(session.staged_events),
    }


@router.post("/write")
def write_events(req: WriteRequest, session: UserSession = Depends(get_session)):
    """Save the event list into one .dyd and its .par, and declare them in a job.

    Both files are files of their own, never an edit of a .dyd describing the
    model: the events stay separable from the network they act on. The .par
    takes the .dyd's name — they are written as a pair and only reference each
    other — and the job gets one <dynModels dydFile="…"/> pointing at the .dyd,
    which is all Dynawo needs to pick the .par up through it.

    The file is rewritten from the list, so what the list holds after the save is
    exactly what the file holds. The list stays as it is: the page has that file
    open, and saving it is not closing it.
    """
    dyd_name = os.path.basename(req.dyd_filename.strip())
    if not dyd_name:
        raise HTTPException(status_code=422, detail="dyd_filename must not be empty")
    if not dyd_name.endswith(".dyd"):
        dyd_name += ".dyd"
    par_name = par_filename_for(dyd_name)

    if not req.overwrite:
        clash = next((n for n in (dyd_name, par_name) if session.session_manager.has_file(n)), None)
        if clash:
            raise HTTPException(status_code=409, detail=f"{clash} is already in the session — choose another name or overwrite it")

    if req.jobs_file and not session.session_manager.has_file(req.jobs_file):
        raise HTTPException(status_code=404, detail=f"{req.jobs_file} not found in session")

    # Saving no event is meaningful on a file that exists — it takes its events
    # out and leaves whatever else it holds — but would only create two empty
    # files otherwise.
    if not session.staged_events and not session.session_manager.has_file(dyd_name):
        raise HTTPException(status_code=422, detail="No event to write")

    events = list(session.staged_events)
    # Saving over a file keeps whatever it holds besides the events — a .dyd
    # describing the network may declare a few events among its models, and this
    # page must not cost the user the rest of it.
    event_libs = {e["lib"] for e in _catalogue()}
    dyd_bytes, dropped_par_ids = build_events_dyd(
        events, par_name, _current_bytes(session, dyd_name), event_libs,
    )
    par_bytes = build_events_par(events, _current_bytes(session, par_name), dropped_par_ids)
    for name, data in ((dyd_name, dyd_bytes), (par_name, par_bytes)):
        info = session.session_manager.add_file(name, data)
        session.uploaded_files_info[name] = {"size": info.size, "ftype": info.ftype}

    jobs_patched = 0
    if req.jobs_file:
        jobs_path = session.session_manager.get_path(req.jobs_file)
        try:
            jobs_patched = write_dyd_reference_to_jobs(jobs_path, dyd_name)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to patch {req.jobs_file}: {exc}")
        # Refresh the stored bytes so the patched jobs file is what the rest of
        # the app reads back.
        session.session_manager.register_existing_file(req.jobs_file)

    # The list is not cleared: it is the content of the file just written, and
    # the page keeps it open so another event can be added to it straight away.
    return {
        "dyd_file":     dyd_name,
        "par_file":     par_name,
        "events":       len(events),
        "jobs_file":    req.jobs_file,
        "jobs_patched": jobs_patched,
        "model_ids":    [e.model_id for e in events],
    }


# ── Reading events back ───────────────────────────────────────────────────────
#
# Once written and declared in a job, an events .dyd is just a file of the
# session. These endpoints read it back into the staged list, so an event can be
# corrected and the file rewritten — the same round trip the curves page makes
# with the .crv a job points at.

class LoadRequest(BaseModel):
    dyd_file: str


def _par_sets_of(session: UserSession, models: dict[str, dict]) -> dict[str, list[dict]]:
    """The parameter sets the models of a .dyd point at, by set id.

    Every event of a file written here shares one .par, but a hand-written file
    may spread them over several — so each distinct parFile is read, and each
    only once: a .dyd of a real case names the same .par in thousands of models,
    and parsing it per model instead of per file is minutes against seconds."""
    sets: dict[str, list[dict]] = {}
    par_names = {info.get("parFile") for info in models.values() if info.get("parFile")}
    for par_ref in par_names:
        raw = _current_bytes(session, _session_name(session, par_ref))
        if not raw:
            continue
        try:
            for set_id, content in parse_par(raw).items():
                sets.setdefault(set_id, content["pars"])
        except Exception:
            continue
    return sets


def _scan_dyd(session: UserSession, dyd_name: str, with_parameters: bool) -> tuple[list[dict], list[dict], int]:
    """What a .dyd of the session holds: its events, what was left out, and how
    many models are not events.

    Cached per file: the listing runs on every change of the event list, and a
    file that has not moved cannot have started holding different events. The
    signature covers everything the answer depends on — the file itself, the
    catalogue that says what an event is, and the network the static ids are
    matched against.

    `with_parameters` decides whether the .par is read at all. Listing the files
    only needs counts; the values are read when a file is actually opened.
    """
    # Resolved before the key is built, not inside the scan: the first scan of a
    # session is what loads the network, and a key naming the network from
    # before that load could never match the one after it — every call would
    # miss its own entry.
    object_types = {sid: _IIDM_TO_EQUIPMENT.get(t, "") for sid, t in _iidm_types(session).items()}

    key = (_file_signature(session, dyd_name), _catalogue_mtime(),
           session.network_name, with_parameters)
    cache = session.events_cache.setdefault("scans", {})
    hit = cache.get(dyd_name)
    if hit and hit[0] == key:
        return hit[1]

    raw = _current_bytes(session, dyd_name)
    if not raw:
        return [], [], 0
    try:
        models = parse_dyd(raw)
    except Exception:
        return [], [], 0
    par_sets = _par_sets_of(session, models) if with_parameters else {}
    # The reader compares imposed values with what the .par holds, so it gets
    # the catalogue with each <fixedParameter> resolved against this install.
    symbols = _LibSymbols(session.dynawo_executable)
    catalogue = [{**e, "fixed_parameters": symbols.fixed_values(e)[0]} for e in _catalogue()]
    result = read_events(raw, par_sets, catalogue, object_types)
    cache[dyd_name] = (key, result)
    return result


@router.get("/files")
def list_event_files(jobs_file: str | None = None, session: UserSession = Depends(get_session)):
    """The .dyd files of the session that hold events, and who declares them.

    An events file is recognised by what is in it — models of a library the
    catalogue offers — not by its name, so a file written elsewhere is found too.
    `jobs_files` names the jobs declaring each one, since rewriting a file
    changes every job that points at it.

    Given a job, its own events files are listed, plus those no job declares:
    a file just taken out of a job — or written without one — would otherwise
    disappear from the page, with no way left to open it or put it back.
    """
    if jobs_file and not session.session_manager.has_file(jobs_file):
        raise HTTPException(status_code=404, detail=f"{jobs_file} not found in session")
    wanted = _job_dyd_names(session, jobs_file) if jobs_file else None

    declared_by: dict[str, list[str]] = {}
    for job in _files_of_type(session, "jobs"):
        for name in (_job_dyd_names(session, job) or []):
            declared_by.setdefault(name, []).append(job)

    files = []
    for dyd_name in _files_of_type(session, "dyd"):
        if wanted is not None and dyd_name not in wanted and declared_by.get(dyd_name):
            continue
        events, skipped, other_models = _scan_dyd(session, dyd_name, with_parameters=False)
        if events:
            files.append({
                "dyd_file":     dyd_name,
                "event_count":  len(events),
                "skipped":      len(skipped),
                # Non-zero means the file is not only events: saving it keeps
                # those models, and emptying it must not delete the file.
                "other_models": other_models,
                "jobs_files":   declared_by.get(dyd_name, []),
            })
    return {"files": files}


@router.post("/load")
def load_events(req: LoadRequest, session: UserSession = Depends(get_session)):
    """Read an events .dyd back into the staged list, ready to be edited.

    The list is replaced, not added to: it then holds exactly what the file
    holds, so writing it back to the same name reproduces the file with the
    edits — the way the curves page shows the .crv of a job and saves it back.
    Model ids are kept as they are, since they are the ids already written and
    possibly already referenced elsewhere.
    """
    if not session.session_manager.has_file(req.dyd_file):
        raise HTTPException(status_code=404, detail=f"{req.dyd_file} not found in session")
    events, skipped, other_models = _scan_dyd(session, req.dyd_file, with_parameters=True)
    if not events:
        detail = "No event of the catalogue found in that file"
        if skipped:
            detail += f" ({skipped[0]['reason']})"
        raise HTTPException(status_code=422, detail=detail)

    session.staged_events = [
        StagedEvent(
            event_id=e["event_id"],
            label=e["label"],
            target_id=e["target_id"],
            kind=e["kind"],
            model_id=e["model_id"],
            lib=e["lib"],
            parameters=[EventParameter(name=p["name"], value=p["value"], type=p["type"]) for p in e["parameters"]],
            connections=[EventConnection(var1=c["var1"], id2=c["id2"], var2=c["var2"]) for c in e["connections"]],
        )
        for e in events
    ]
    return {
        "source":  req.dyd_file,
        "par_file": par_filename_for(req.dyd_file),
        "other_models": other_models,
        "loaded":  len(session.staged_events),
        "skipped": skipped,
        "events":  [_staged_view(e) for e in session.staged_events],
    }


class UndeclareRequest(BaseModel):
    dyd_file: str
    # The job to take the events out of. None takes them out of every job
    # declaring them, which is what removing a set of events usually means.
    jobs_file: str | None = None
    # Also delete the .dyd and its .par from the session. Off by default: a job
    # can be given its events back by declaring the file again, but a deleted
    # file has to be composed anew.
    delete_files: bool = False


@router.post("/undeclare")
def undeclare_events(req: UndeclareRequest, session: UserSession = Depends(get_session)):
    """Take an events file out of a job, and optionally delete it.

    Removing one event of a file is a different operation: read the file back,
    drop that event from the list and write it again. This one is about the set
    as a whole — the <dynModels> line that makes the job run those events.
    """
    if not session.session_manager.has_file(req.dyd_file):
        raise HTTPException(status_code=404, detail=f"{req.dyd_file} not found in session")
    if req.jobs_file and not session.session_manager.has_file(req.jobs_file):
        raise HTTPException(status_code=404, detail=f"{req.jobs_file} not found in session")

    targets = [req.jobs_file] if req.jobs_file else _files_of_type(session, "jobs")
    updated: list[str] = []
    for jobs_name in targets:
        jobs_path = session.session_manager.get_path(jobs_name)
        if not os.path.isfile(jobs_path):
            continue
        try:
            removed = remove_dyd_reference_from_jobs(jobs_path, req.dyd_file)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to patch {jobs_name}: {exc}")
        if removed:
            session.session_manager.register_existing_file(jobs_name)
            updated.append(jobs_name)

    deleted: list[str] = []
    if req.delete_files:
        for name in (req.dyd_file, par_filename_for(req.dyd_file)):
            if session.session_manager.has_file(name):
                session.session_manager.remove_file(name)
                session.uploaded_files_info.pop(name, None)
                deleted.append(name)

    return {"dyd_file": req.dyd_file, "jobs_updated": updated, "deleted": deleted}
