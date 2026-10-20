#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.events_reader import read_events
from backend.events_writer import build_events_dyd, build_events_par
from backend.par_parser import parse_par


def _write_and_read(events, catalogue, object_types):
    """Write the events as the GUI does, then read the files back."""
    dyd, _ = build_events_dyd(events, "events.par")
    par_sets = {sid: s["pars"] for sid, s in parse_par(build_events_par(events)).items()}
    return read_events(dyd, par_sets, catalogue, object_types)


def test_round_trip_gives_back_the_staged_events(staged_events, event_catalogue, object_types):
    events, skipped, other_models = _write_and_read(staged_events, event_catalogue, object_types)

    assert skipped == [] and other_models == 0
    read = {e["model_id"]: e for e in events}
    for staged in staged_events:
        got = read[staged.model_id]
        assert got["event_id"] == staged.event_id
        assert got["kind"] == staged.kind
        assert got["target_id"] == staged.target_id
        assert got["lib"] == staged.lib
        assert [(p["name"], p["value"], p["type"]) for p in got["parameters"]] == \
               [(p.name, p.value, p.type) for p in staged.parameters]
        assert [(c["var1"], c["id2"], c["var2"]) for c in got["connections"]] == \
               [(c.var1, c.id2, c.var2) for c in staged.connections]


def test_events_of_same_lib_are_told_apart_by_their_fixed_value(event_catalogue, object_types, make_event):
    def load_event(model_id: str, event_id: str, open_: str):
        return make_event(event_id, model_id, "EventConnectedStatus", "network", "LOAD1",
                          [("event_tEvent", "1", "DOUBLE"), ("event_open", open_, "BOOL")],
                          [("event_state1_value", "NETWORK", "LOAD1_state_value")])

    staged = [load_event("EV_OFF", "network_load_disconnection", "true"),
              load_event("EV_ON", "network_load_connection", "false")]

    events, _, _ = _write_and_read(staged, event_catalogue, object_types)

    assert {e["model_id"]: e["event_id"] for e in events} == {
        "EV_OFF": "network_load_disconnection",
        "EV_ON": "network_load_connection",
    }


def test_events_of_same_lib_and_value_are_told_apart_by_equipment(event_catalogue, object_types, make_event):
    staged = [make_event("network_generator_disconnection", "EV_GEN_OFF", "EventConnectedStatus",
                         "network", "GEN1",
                         [("event_tEvent", "1", "DOUBLE"), ("event_open", "true", "BOOL")],
                         [("event_state1_value", "NETWORK", "GEN1_state_value")])]

    events, _, _ = _write_and_read(staged, event_catalogue, object_types)

    assert events[0]["event_id"] == "network_generator_disconnection"


def test_longest_matching_static_id_is_the_target(event_catalogue, object_types, make_event):
    staged = [make_event("network_line_disconnection", "EV", "EventQuadripoleDisconnection",
                         "network", "L1-2-1", [("event_tEvent", "1", "DOUBLE")],
                         [("event_state1_value", "NETWORK", "L1-2-1_state_value")])]

    events, _, _ = _write_and_read(staged, event_catalogue, object_types)

    assert events[0]["target_id"] == "L1-2-1"


def test_file_mixing_network_models_and_events(data_dir, event_catalogue, object_types):
    dyd = (data_dir / "events/network_with_events.dyd").read_bytes()
    par_sets = {sid: s["pars"]
                for sid, s in parse_par((data_dir / "events/network_with_events.par").read_bytes()).items()}

    events, skipped, other_models = read_events(dyd, par_sets, event_catalogue, object_types)

    assert other_models == 1                                 # GEN1
    assert [s["model_id"] for s in skipped] == ["GEN1"]
    targets = {e["model_id"]: e["target_id"] for e in events}
    # MACRO_EVENT is wired by a macroConnect: its target comes from its staticId.
    assert targets == {"OLD_EVENT": "L1-2", "MACRO_EVENT": "L1-2-1"}


def test_unreadable_events_are_reported_with_a_reason(event_catalogue, object_types):
    dyd = b"""<dyn:dynamicModelsArchitecture xmlns:dyn="http://www.rte-france.com/dynawo">
      <dyn:blackBoxModel id="NOT_WIRED" lib="EventQuadripoleDisconnection" parFile="e.par" parId="NOT_WIRED"/>
      <dyn:blackBoxModel id="UNKNOWN_LINE" lib="EventQuadripoleDisconnection" parFile="e.par" parId="UNKNOWN_LINE"/>
      <dyn:connect id1="UNKNOWN_LINE" var1="event_state1_value" id2="NETWORK" var2="L9-9_state_value"/>
    </dyn:dynamicModelsArchitecture>"""

    events, skipped, other_models = read_events(dyd, {}, event_catalogue, object_types)

    assert events == [] and other_models == 0
    reasons = {s["model_id"]: s["reason"] for s in skipped}
    assert "no connection" in reasons["NOT_WIRED"]
    assert "L9-9_state_value" in reasons["UNKNOWN_LINE"]
