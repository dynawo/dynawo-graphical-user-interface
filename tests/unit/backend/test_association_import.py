#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import pytest

from backend.association_import import _connections_of, _element_of, _par_file, read_dyd, reconstruct
from tests.unit.backend.association_network import CATALOGUE, network

DYD = "dyd/reconstruct.dyd"


@pytest.fixture
def rebuilt(data_dir) -> dict:
    return reconstruct(network(), str(data_dir / DYD), "reconstruct.dyd", CATALOGUE, ["loads.par"])


def _ids(rule: dict) -> list[str] | None:
    return rule["conditions"][0]["value"] if rule["conditions"] else None


def test_read_dyd_keeps_the_top_level_elements(data_dir):
    content = read_dyd(str(data_dir / DYD))

    assert content["models"]["L1"] == {"lib": "LoadAlphaBeta", "static_id": "L1", "par_file": "loads.par", "par_id": "Load"}
    assert content["composites"] == [("modelicaModel", "COMPOSITE")]
    assert content["macro_connectors"]["MC_OMS"][0] == ("currentLimitAutomaton_IMonitored", "@NAME@_iSide2")
    assert content["macro_connects"][0]["name2"] == "LN1"


def test_macro_connections_are_expanded_for_both_sides(data_dir):
    connections = _connections_of(read_dyd(str(data_dir / DYD)), {"OMS_LN1", "L1"})

    assert connections["OMS_LN1"] == [("currentLimitAutomaton_IMonitored", "NETWORK", "LN1_iSide2"),
                                      ("currentLimitAutomaton_order", "NETWORK", "LN1_state")]
    assert connections["L1"] == [("transformerT_tap", "TC_L1", "tapChanger_tap")]


def test_equipment_rules_cover_a_whole_type_without_condition_then_by_id(rebuilt):
    rules = [(r["element_type"], r["model"], _ids(r), r["parameter_set_id"]) for r in rebuilt["rules"]]

    assert rules[:4] == [
        ("GENERATOR", "GeneratorSynchronousFourWindings", ["G1"], "G1"),       # one element each: by id
        ("GENERATOR", "GeneratorSynchronousFourWindings", ["G2"], "G2"),
        ("LOAD", "LoadAlphaBeta", None, "Load"),                               # every load has a model
        ("LOAD", "LoadPQ", ["L3"], "LoadPQ"),                                  # the later rule overrides
    ]


def test_automation_systems_get_their_element_and_fields_from_their_connections(rebuilt):
    automation = {(r["model"], tuple(_ids(r) or ["every"]), tuple(sorted(r["fields"].items())))
                  for r in rebuilt["rules"][4:]}

    assert automation == {
        ("TapChangerAutomationSystem", ("L1",), (("side", "HIGH_VOLTAGE"),)),   # transformerT_ variables
        # Through the macroConnect; LN1 is the only line, so no condition.
        ("OverloadManagementSystem", ("every",), (("i_measurement_side", "TWO"),)),
    }
    assert rebuilt["report"]["automation_systems"] == 2


def test_rules_give_back_the_models_of_the_dyd(rebuilt):
    report = rebuilt["report"]

    assert (report["equipment"], report["differences"], report["difference_count"]) == (5, [], 0)
    assert report["unknown_models"] == []
    assert report["missing_par_files"] == ["gens.par"]


def test_what_rules_cannot_express_is_reported_with_the_reason(rebuilt):
    left_out = {entry["reason"].split(":")[0].split(" (")[0]: entry["examples"] for entry in rebuilt["report"]["left_out"]}

    assert left_out == {
        "frequency model": ["OMEGA_REF"],
        "event": ["FAULT", "DISCONNECT"],
        "static id not in the loaded network": ["NOT_IN_NETWORK"],
        "model on no network element": ["SIGNAL (SignalHandler)"],
        "composite model": ["COMPOSITE"],
        "automation system on several elements": ["TC_BOTH (TapChangerAutomaton: L2, L3)"],
        "automation system connected to no element it applies to": ["UV_ALONE (UnderVoltageAutomaton)"],
    }


def test_par_file_is_found_relative_to_the_dyd_else_by_name():
    assert _par_file("loads.par", "case/case.dyd", ["case/loads.par", "loads.par"]) == "case/loads.par"
    assert _par_file("../other/loads.par", "case/case.dyd", ["loads.par"]) == "loads.par"
    assert _par_file("gens.par", "case.dyd", ["a/gens.par", "b/gens.par"]) == "gens.par"     # ambiguous: as written
    assert _par_file(None, "case.dyd", ["loads.par"]) is None


def test_network_variable_names_the_longest_matching_element():
    assert _element_of("LN1_2_iSide1", {"LN1", "LN1_2"}) == ("LN1_2", "iSide1")
    assert _element_of("NOPE_state", {"LN1"}) is None
