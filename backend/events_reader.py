#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.dyd_parser import (
    connections_from_root,
    macro_connections_from_root,
    models_from_root,
    parse_dyd_root,
)

NETWORK_MODEL_ID = "NETWORK"


def read_events(
    dyd_content: bytes,
    par_sets: dict[str, list[dict]],
    catalogue: list[dict],
    object_types: dict[str, str],
) -> tuple[list[dict], list[dict], int]:
    """Recover the events an already written .dyd holds, as the page staged them.

    Written events are ordinary blackBoxModels, so what makes one an event here
    is its library being one the catalogue offers. `object_types` maps each
    static id of the network to its equipment kind (LINE, GENERATOR, …): it is
    how the object a network event acts on is recognised, and how two events
    written identically for different kinds of object are told apart. Everything the page needs is
    read back from the pair of files: the connections say which object the event
    acts on, `par_sets` (the .dyd's .par, parsed) carries the values.

    Returns (events, skipped, other_models) — skipped naming each model left out
    and why, so a file only partly recognised is reported rather than silently
    halved, and other_models counting the models that are not events at all,
    which is what tells a file of events from a .dyd describing the network that
    happens to declare a few.

    `par_sets` may be empty when only the count of events matters: a .par of a
    real case is large, and reading it to answer "how many events does this file
    hold" would cost more than the answer is worth.
    """
    root = parse_dyd_root(dyd_content)
    models = models_from_root(root)
    connections = connections_from_root(root) + macro_connections_from_root(root, models)
    event_libs = {e["lib"] for e in catalogue}

    events: list[dict] = []
    skipped: list[dict] = []
    other_models = 0
    for model_id, info in models.items():
        if info["lib"] not in event_libs:
            other_models += 1
            skipped.append({"model_id": model_id, "reason": f"{info['lib']} is not an event library of the catalogue"})
            continue
        wires = [c for c in connections if c["id1"] == model_id]
        if not wires:
            skipped.append({"model_id": model_id, "reason": "no connection declares what it acts on"})
            continue

        kind = "network" if all(w["id2"] == NETWORK_MODEL_ID for w in wires) else "dynamic"
        if kind == "network":
            # The staticId of the event model names the equipment outright, and
            # is what a macro-connected event relies on; the variable name is
            # only read when there is none.
            target_id = (info["static_id"] if info["static_id"] in object_types
                         else _network_target(wires, set(object_types)))
        else:
            target_id = wires[0]["id2"]
        if not target_id:
            skipped.append({
                "model_id": model_id,
                "reason": f"no network object matches '{wires[0]['var2']}' — load the IIDM this .dyd was written for",
            })
            continue

        parameters = par_sets.get(info["parId"], [])
        event = _match_event(catalogue, info["lib"], kind, wires, parameters, object_types.get(target_id, ""))
        if event is None:
            skipped.append({"model_id": model_id, "reason": f"the catalogue has no {kind} event using {info['lib']}"})
            continue

        events.append({
            "event_id":    event["id"],
            "label":       event["label"],
            "lib":         info["lib"],
            "model_id":    model_id,
            "kind":        kind,
            "target_id":   target_id,
            "parameters":  parameters,
            "connections": [{"var1": w["var1"], "id2": w["id2"], "var2": w["var2"]} for w in wires],
        })
    return events, skipped, other_models


def _network_target(wires: list[dict], static_ids: set[str]) -> str:
    """Which IIDM object a network connection names.

    A network variable reads `<static id>_<port>_value`, and a static id may
    itself hold underscores, so the id cannot be cut out of the name — it is
    recognised among the ids the network actually has, longest first so that
    `L1-2` never wins over `L1-2-1`."""
    var2 = wires[0]["var2"]
    for static_id in sorted(static_ids, key=len, reverse=True):
        if var2.startswith(f"{static_id}_"):
            return static_id
    return ""


def _match_event(catalogue: list[dict], lib: str, kind: str, wires: list[dict],
                 parameters: list[dict] | None = None, equipment: str = "") -> dict | None:
    """The catalogue event a written model came from.

    The library and the scope narrow it down. What is left is told apart by
    what differs between the candidates, in turn: the kind of object acted on —
    a generator and a load are disconnected through the very same library and
    wiring — then the values the event imposes — a disconnection and a
    connection through EventConnectedStatus differ only in event_open — and
    last their patterns, the one whose fragments are in the variables actually
    wired being the one that produced this model.

    Without `parameters` (a listing that only counts events), the imposed values
    cannot be checked and the first candidate stands for all of them."""
    candidates = [e for e in catalogue if e["lib"] == lib and e["scope"] == kind]
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    if equipment:
        # An event with no appliesTo is a candidate for every kind of object.
        same_kind = [e for e in candidates
                     if not e["equipment_type"] or e["equipment_type"] == equipment]
        if same_kind:
            candidates = same_kind
        if len(candidates) == 1:
            return candidates[0]

    if parameters:
        written = {p["name"]: str(p["value"]).strip().lower() for p in parameters}
        agreeing = [e for e in candidates
                    if all(written.get(n) == str(v).strip().lower() for n, v in e["fixed_parameters"].items())]
        if agreeing:
            candidates = agreeing
        if len(candidates) == 1:
            return candidates[0]
    for event in candidates:
        patterns = event["patterns"]
        if len(patterns) == len(wires) and all(
            p["var1"].lower() in w["var1"].lower() and p["var2"].lower() in w["var2"].lower()
            for p, w in zip(patterns, wires)
        ):
            return event
    return candidates[0]
