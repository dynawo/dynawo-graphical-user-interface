#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import pandas as pd
import pytest

from backend.association_rules import (
    BOOLEAN,
    NUMBER,
    OPERATORS,
    TEXT,
    attribute_values,
    automation_fields,
    automation_id,
    check_condition,
    coerce_value,
    condition_code,
    condition_mask,
    connection_notes,
    describe_attributes,
    element_table,
    get_automation_system,
    get_element_type,
    model_library,
    models_for_element_type,
    network_models_requirement,
    required_categories,
    requirement_text,
    resolve,
)
from tests.unit.backend.association_network import CATALOGUE, network, rule


def _loads() -> pd.DataFrame:
    return element_table(network(), get_element_type("LOAD"))


def _by_element(resolution: dict) -> dict[str, str]:
    """{element id: model} of the equipment assignments."""
    return {a["static_id"]: a["model"] for a in resolution["assignments"] if not a.get("automation")}


# ── Element tables and attributes ─────────────────────────────────────────────

def test_element_table_joins_the_voltage_level_attributes():
    df = _loads().set_index("id")

    assert df.loc["L1", ["nominal_v", "substation_id", "country"]].tolist() == [63.0, "S1", "FR"]
    lines = element_table(network(), get_element_type("LINE")).set_index("id")
    assert lines.loc["LN1", ["nominal_v1", "nominal_v2", "country2"]].tolist() == [63.0, 225.0, "BE"]


def test_describe_attributes_gives_kinds_operators_and_listed_values():
    attributes = {a["name"]: a for a in describe_attributes(_loads())}

    assert attributes["p0"]["kind"] == NUMBER and ">" in attributes["p0"]["operators"]
    assert attributes["connected"]["kind"] == BOOLEAN and "<" not in attributes["connected"]["operators"]
    assert attributes["country"] == {"name": "country", "kind": TEXT, "operators": ["=", "!=", "in", "matches"],
                                     "values": ["BE", "FR"]}


def test_attribute_values_are_searched_without_case():
    assert attribute_values(_loads(), "id", "l", 2) == (["L1", "L2"], 3)
    assert attribute_values(_loads(), "country", "b", 10) == (["BE"], 1)


def test_connection_notes_name_the_open_side():
    lines = element_table(network(), get_element_type("LINE"))

    assert connection_notes(_loads()) == {"L2": "not connected"}
    assert connection_notes(lines) == {"LN1": "not connected on side 2"}


# ── Conditions ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("kind", "operator", "raw", "expected"), [
    (NUMBER, ">", "10", 10.0),
    (BOOLEAN, "=", "True", True),
    (TEXT, "in", "FR, BE,", ["FR", "BE"]),
    (NUMBER, "in", [1, "2"], [1.0, 2.0]),
    (TEXT, "matches", "^L[12]$", "^L[12]$"),
])
def test_values_are_coerced_to_the_attribute_kind(kind, operator, raw, expected):
    assert coerce_value(kind, operator, raw) == expected


@pytest.mark.parametrize(("condition", "error"), [
    ({"attribute": "nope", "operator": "=", "value": "1"}, "unknown attribute 'nope'"),
    ({"attribute": "p0", "operator": "~", "value": "1"}, "unknown operator '~'"),
    ({"attribute": "country", "operator": ">", "value": "A"}, "operator '>' does not apply to text attribute 'country'"),
    ({"attribute": "p0", "operator": "=", "value": "big"}, "invalid value for 'p0'"),
    ({"attribute": "connected", "operator": "=", "value": "yes"}, "invalid value for 'connected'"),
    ({"attribute": "id", "operator": "matches", "value": "("}, "invalid value for 'id'"),
    ({"attribute": "id", "operator": "in", "value": " , "}, "invalid value for 'id': the list of values is empty"),
])
def test_invalid_conditions_are_reported(condition, error):
    value, reason = check_condition(_loads(), condition)

    assert value is None and reason.startswith(error)


@pytest.mark.parametrize(("attribute", "operator", "raw"), [
    ("p0", "=", "20"), ("p0", "!=", "20"), ("p0", "<", "20"), ("p0", "<=", "20"), ("p0", ">", "20"),
    ("p0", ">=", "20"), ("country", "in", "BE"), ("id", "matches", "^L[13]"), ("connected", "=", "false"),
])
def test_script_code_selects_what_the_gui_selects(attribute, operator, raw):
    # The exported script writes conditions as code: both forms must stay identical.
    df = _loads()
    condition = {"attribute": attribute, "operator": operator, "value": raw}
    value, error = check_condition(df, condition)
    assert error is None

    from_code = eval(condition_code(condition, value), {"df": df})

    assert from_code.fillna(False).astype(bool).tolist() == condition_mask(df, condition, value).tolist()


def test_every_operator_has_a_script_form():
    assert all(len(entry) == 3 and "{s}" in entry[2] for entry in OPERATORS.values())


# ── Models ────────────────────────────────────────────────────────────────────

def test_models_of_an_element_type_put_the_fitting_ones_first_then_its_automation_systems():
    models = models_for_element_type(get_element_type("LOAD"), CATALOGUE)
    names = [m["name"] for m in models]

    assert names[:4] == ["LoadAlphaBeta", "LoadPQ", "LoadOneTransformer", "LoadTwoTransformers"]
    assert all(m["suggested"] for m in models[:4]) and not models[4]["suggested"]
    tap_changer = models[-1]
    assert (tap_changer["name"], tap_changer["automation"], tap_changer["lib"], tap_changer["id_prefix"]) == \
        ("TapChangerAutomationSystem", True, "TapChangerAutomaton", "TC")
    assert "PhaseShifterI" not in names                                       # transformers only


def test_model_library_is_the_model_unless_renamed():
    assert model_library("UnderVoltage", "UnderVoltageAutomationSystem") == "UnderVoltageAutomaton"
    assert model_library("LoadAlphaBeta", "Load") == "LoadAlphaBeta"
    assert model_library("LoadAlphaBeta", None) == "LoadAlphaBeta"


def test_requirements_are_said_in_words():
    assert requirement_text(get_automation_system("TapChanger")) == (
        "needs on its element a LoadOneTransformer model for side NONE, "
        "a LoadTwoTransformers model for side HIGH_VOLTAGE or LOW_VOLTAGE")
    assert requirement_text(get_automation_system("UnderVoltageAutomationSystem")).startswith(
        "needs a SynchronousGenerator, SynchronizedGenerator")
    assert requirement_text(get_automation_system("PhaseShifterI")) is None
    assert network_models_requirement().startswith("a Line, Transformer, Bus or InfiniteBus model makes PowSyBl")


def test_automation_fields_default_to_the_first_value_and_reject_others():
    tap_changer = get_automation_system("TapChanger")

    assert automation_fields(tap_changer, None) == ({"side": "NONE"}, [])
    values, errors = automation_fields(tap_changer, {"side": "MIDDLE"})
    assert errors == ["invalid transformer 'MIDDLE' (one of NONE, HIGH_VOLTAGE, LOW_VOLTAGE)"]


def test_automation_id_holds_the_fields_marked_in_id_unless_default():
    tap_changer, oms = get_automation_system("TapChanger"), get_automation_system("OverloadManagementSystem")

    assert automation_id(tap_changer, "L1", {"side": "NONE"}) == "TC_L1"
    assert automation_id(tap_changer, "L1", {"side": "HIGH_VOLTAGE"}) == "TC_L1_HIGH_VOLTAGE"
    assert automation_id(oms, "LN1", {"i_measurement_side": "TWO"}) == "OMS_LN1"      # not in the id
    assert required_categories(tap_changer, {"side": "LOW_VOLTAGE"}) == ["LoadTwoTransformers"]


# ── Resolution ────────────────────────────────────────────────────────────────

def test_later_rules_override_earlier_ones():
    rules = [rule("LOAD", "LoadAlphaBeta"), rule("LOAD", "LoadPQ", ("p0", ">", "10"))]

    resolution = resolve(network(), rules, CATALOGUE)

    assert _by_element(resolution) == {"L1": "LoadAlphaBeta", "L2": "LoadPQ", "L3": "LoadPQ"}
    assert [(r["matched"], r["applied"]) for r in resolution["rules"]] == [(3, 1), (2, 2)]
    loads = next(t for t in resolution["element_types"] if t["id"] == "LOAD")
    assert (loads["total"], loads["unmatched_count"]) == (3, 0)


@pytest.mark.parametrize(("conditions", "expected"), [
    ([("id", "=", "L2")], {"L2"}),
    ([("id", "in", "L1, L3, NOPE")], {"L1", "L3"}),
    ([("id", "in", "L1, L2, L3"), ("p0", ">", "10")], {"L2", "L3"}),        # looked up, then tested
    ([("nominal_v", ">=", "200"), ("country", "=", "BE")], {"L2", "L3"}),
])
def test_conditions_select_the_elements(conditions, expected):
    resolution = resolve(network(), [rule("LOAD", "LoadAlphaBeta", *conditions)], CATALOGUE)

    assert set(_by_element(resolution)) == expected


def test_rules_with_errors_are_skipped():
    rules = [
        rule("TURBINE", "LoadAlphaBeta"),
        rule("LOAD", "NoSuchModel"),
        rule("LOAD", "LoadAlphaBeta", ("p0", ">", "big")),
        rule("LOAD", "LoadPQ", set_id="Missing"),
    ]

    resolution = resolve(network(), rules, CATALOGUE, check_parameters=lambda r: ["no set"] if r["parameter_set_id"] == "Missing" else [])

    assert [r["errors"][0] for r in resolution["rules"]] == [
        "unknown element type 'TURBINE'", "unknown model 'NoSuchModel'", "invalid value for 'p0': could not convert string to float: 'big'", "no set"]
    assert resolution["assignments"] == []


def test_model_of_another_element_type_and_unconnected_elements_are_warned():
    resolution = resolve(network(), [rule("GENERATOR", "LoadAlphaBeta"), rule("LOAD", "LoadPQ")], CATALOGUE)

    assert resolution["rules"][0]["warnings"] == [
        "model 'LoadAlphaBeta' (Load) is not made for generators: PowSyBl will probably leave these elements out"]
    assert resolution["rules"][1]["warnings"] == ["1 element(s) not connected: L2"]


def test_automation_system_goes_alongside_the_equipment_model():
    rules = [rule("LOAD", "LoadTwoTransformers"),
             rule("LOAD", "TapChangerAutomationSystem", ("id", "=", "L1"), fields={"side": "HIGH_VOLTAGE"})]

    resolution = resolve(network(), rules, CATALOGUE)

    automation = [a for a in resolution["assignments"] if a.get("automation")]
    assert automation == [{"element_type": "LOAD", "static_id": "L1", "model": "TapChangerAutomationSystem",
                           "category": "TapChanger", "rule": 1, "automation": True,
                           "dynamic_model_id": "TC_L1_HIGH_VOLTAGE", "fields": {"static_id": "L1", "side": "HIGH_VOLTAGE"}}]
    assert _by_element(resolution)["L1"] == "LoadTwoTransformers"              # not overridden
    assert resolution["rules"][1]["warnings"] == []


def test_automation_system_without_the_model_it_connects_to_is_warned():
    rules = [rule("LOAD", "LoadAlphaBeta"), rule("LOAD", "TapChangerAutomationSystem", ("id", "in", "L1, L3"))]

    warnings = resolve(network(), rules, CATALOGUE)["rules"][1]["warnings"]

    assert warnings == ["2 element(s) without the model this automation system connects to "
                        "(LoadOneTransformer): PowSyBl will leave it out: L1, L3"]


def test_automation_system_without_the_part_it_acts_on_is_warned():
    rules = [rule("TWO_WINDINGS_TRANSFORMER", "TransformerFixedRatio"),
             rule("TWO_WINDINGS_TRANSFORMER", "PhaseShifterI")]

    warnings = resolve(network(), rules, CATALOGUE)["rules"][1]["warnings"]

    assert "1 element(s) without a phase tap changer: PowSyBl will leave them out: T2" in warnings


def test_automation_system_on_another_element_type_or_clashing_id_is_an_error():
    clash = network()
    clash._tables["get_generators"].loc["TC_L1"] = ["HYDRO", "VL1", True]       # a generator named like it
    rules = [rule("GENERATOR", "TapChangerAutomationSystem"),
             rule("GENERATOR", "GeneratorSynchronousFourWindings"),
             rule("LOAD", "TapChangerAutomationSystem", ("id", "=", "L1"))]

    reports = resolve(clash, rules, CATALOGUE)["rules"]

    assert reports[0]["errors"] == ["automation system 'TapChangerAutomationSystem' applies to loads only"]
    assert reports[2]["errors"] == ["automation system id 'TC_L1' is also the id of an element with a model"]
    assert reports[2]["applied"] == 0


def test_network_element_model_asks_for_models_on_the_whole_network():
    warnings = resolve(network(), [rule("LINE", "Line")], CATALOGUE)["rules"][0]["warnings"]

    assert warnings[-1].endswith("without one: 2 two-winding transformers (T1, T2); 1 buses (bus-breaker view) (B1). "
                                 "PowSyBl will refuse to generate")
