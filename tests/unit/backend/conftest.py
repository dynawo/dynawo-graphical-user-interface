#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Fixtures shared by the events writer and reader tests."""

import pytest

from backend.events_catalogue import parse_events_catalogue
from backend.models import EventConnection, EventParameter, StagedEvent

# Static id -> equipment kind of the network the events act on, as the
# router builds it from the IIDM. "L1-2-1" shares a prefix with "L1-2" on
# purpose: the reader must not confuse them.
OBJECT_TYPES = {"L1-2": "LINE", "L1-2-1": "LINE", "LOAD1": "LOAD", "GEN1": "GENERATOR"}


@pytest.fixture
def object_types() -> dict[str, str]:
    return dict(OBJECT_TYPES)


@pytest.fixture
def event_catalogue(data_dir) -> list[dict]:
    """tests/data/events/catalogue.xml, as the router hands it to read_events.

    The router resolves each <fixedParameter> against the Dynawo library
    descriptor of this install. The test catalogue names its parameters
    exactly, so the name is used as it is, with no Dynawo needed."""
    entries = parse_events_catalogue((data_dir / "events/catalogue.xml").read_bytes())
    return [{**e, "fixed_parameters": {f["name"]: f["value"] for f in e["fixed_parameters"]}}
            for e in entries]


def _make_event(event_id: str, model_id: str, lib: str, kind: str, target_id: str,
                parameters: list[tuple[str, str, str]], connections: list[tuple[str, str, str]]) -> StagedEvent:
    """A StagedEvent from (name, value, type) and (var1, id2, var2) tuples."""
    return StagedEvent(
        event_id=event_id,
        label=event_id,
        target_id=target_id,
        kind=kind,
        model_id=model_id,
        lib=lib,
        parameters=[EventParameter(name=n, value=v, type=t) for n, v, t in parameters],
        connections=[EventConnection(var1=v1, id2=i2, var2=v2) for v1, i2, v2 in connections],
    )


@pytest.fixture
def make_event():
    """Build a StagedEvent — see _make_event."""
    return _make_event


@pytest.fixture
def staged_events() -> list[StagedEvent]:
    """One network event and one dynamic event."""
    return [
        _make_event(
            "network_line_disconnection", "EV_LINE", "EventQuadripoleDisconnection", "network", "L1-2",
            [("event_tEvent", "1", "DOUBLE"), ("event_disconnectOrigin", "true", "BOOL"),
             ("event_disconnectExtremity", "true", "BOOL")],
            [("event_state1_value", "NETWORK", "L1-2_state_value")],
        ),
        _make_event(
            "dynamic_disconnection", "EV_GEN", "EventSetPointBoolean", "dynamic", "GEN1",
            [("event_tEvent", "2", "DOUBLE"), ("event_stateEvent1", "true", "BOOL")],
            [("event_state1", "GEN1", "generator_switchOffSignal2")],
        ),
    ]
