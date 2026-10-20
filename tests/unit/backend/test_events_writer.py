#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.dyd_parser import parse_dyd, parse_dyd_connections
from backend.events_writer import build_events_dyd, build_events_par, par_filename_for
from backend.par_parser import parse_par

EVENT_LIBS = {"EventQuadripoleDisconnection", "EventConnectedStatus", "EventSetPointBoolean"}


def test_par_file_is_named_after_the_dyd():
    assert par_filename_for("my_events.dyd") == "my_events.par"


def test_new_dyd_matches_expected_file(staged_events, assert_same_xml):
    content, dropped = build_events_dyd(staged_events, "events.par")

    assert dropped == set()
    assert_same_xml(content, "events/new_events.expected.dyd")


def test_new_par_matches_expected_file(staged_events, assert_same_xml):
    assert_same_xml(build_events_par(staged_events), "events/new_events.expected.par")


def test_merge_into_dyd_replaces_only_the_events(staged_events, data_dir):
    existing = (data_dir / "events/network_with_events.dyd").read_bytes()

    content, dropped = build_events_dyd(staged_events, "events.par", existing, EVENT_LIBS)

    assert dropped == {"OLD_EVENT", "MACRO_EVENT"}
    assert set(parse_dyd(content)) == {"GEN1", "EV_LINE", "EV_GEN"}
    wired = {(c["id1"], c["var2"]) for c in parse_dyd_connections(content) if c["id1"]}
    assert wired == {
        ("GEN1", "BUS1_ACPIN"),
        ("EV_LINE", "L1-2_state_value"),
        ("EV_GEN", "generator_switchOffSignal2"),
    }
    # A macroConnect naming the removed MACRO_EVENT would be rejected by Dynawo.
    assert b"macroConnect " not in content and b"macroConnect>" not in content
    assert b"MACRO_EVENT" not in content
    assert b"GEN1, its" in content                           # the user's comment is kept


def test_merge_into_par_replaces_only_the_event_sets(staged_events, data_dir):
    existing = (data_dir / "events/network_with_events.par").read_bytes()

    content = build_events_par(staged_events, existing, drop_set_ids={"OLD_EVENT", "MACRO_EVENT"})

    sets = parse_par(content)
    assert set(sets) == {"KEEP_ME", "EV_LINE", "EV_GEN"}
    assert sets["KEEP_ME"]["pars"][0]["value"] == "5.4"
    assert b"KEEP_ME is not an event set" in content         # the user's comment is kept


def test_saving_twice_does_not_duplicate_the_events(staged_events):
    dyd, _ = build_events_dyd(staged_events, "events.par")
    par = build_events_par(staged_events)

    dyd, dropped = build_events_dyd(staged_events, "events.par", dyd, EVENT_LIBS)
    par = build_events_par(staged_events, par, dropped)

    assert sorted(parse_dyd(dyd)) == ["EV_GEN", "EV_LINE"]
    assert len([c for c in parse_dyd_connections(dyd) if c["id1"]]) == 2
    assert sorted(parse_par(par)) == ["EV_GEN", "EV_LINE"]
