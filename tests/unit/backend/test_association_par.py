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

from backend.association_par import (
    FILE,
    MANUAL,
    assemble_par,
    check_rule_parameters,
    file_set_ids,
    for_powsybl,
    set_ids,
    spec_key,
)
from backend.desc_parser import get_lib_parameters_to_set
from backend.par_parser import parse_par

PARAMETERS = {"MySet": [("load_alpha", "DOUBLE"), ("load_isControllable", "BOOL")]}
SPEC = (MANUAL, "", "MySet")


def test_new_manual_set_holds_the_parameters_to_fill():
    content, ids, to_fill = assemble_par([SPEC], {}, None, None, PARAMETERS)

    pars = parse_par(content)["MySet"]["pars"]
    assert [(p["name"], p["type"], p["value"]) for p in pars] == [
        ("load_alpha", "DOUBLE", ""), ("load_isControllable", "BOOL", ""),
    ]
    assert ids[SPEC] == "MySet" and to_fill == ["MySet"]


def test_kept_manual_set_is_not_refilled_and_is_done_once_filled():
    previous, _, _ = assemble_par([SPEC], {}, None, None, PARAMETERS)
    filled = previous.replace(b'value=""', b'value="1"')

    content, _, to_fill = assemble_par([SPEC], {}, None, filled, {"MySet": [("load_beta", "DOUBLE")]})

    assert [p["name"] for p in parse_par(content)["MySet"]["pars"]] == ["load_alpha", "load_isControllable"]
    assert to_fill == []


def test_powsybl_copy_leaves_out_parameters_without_value():
    content, _, _ = assemble_par([SPEC], {}, None, None, PARAMETERS)

    pars = parse_par(for_powsybl(content, {"MySet": ["load_alpha"]}))["MySet"]["pars"]

    assert [(p["name"], p["value"]) for p in pars] == [("load_alpha", "1")]   # placeholder only


def test_parameters_to_set_skip_read_only_and_defaulted(tmp_path):
    (tmp_path / "ddb").mkdir()
    (tmp_path / "ddb" / "MyLib.desc.xml").write_text(
        '<model xmlns="http://www.rte-france.com/dynawo"><elements><parameters>'
        '<parameter name="a" valueType="DOUBLE" readOnly="false"/>'
        '<parameter name="b" valueType="INT"/>'
        '<parameter name="c" valueType="DOUBLE" readOnly="false" defaultValue="1"/>'
        '<parameter name="d" valueType="DOUBLE" readOnly="true"/>'
        '</parameters></elements></model>')

    assert get_lib_parameters_to_set(str(tmp_path / "dynawo.sh"), "MyLib") == [("a", "DOUBLE"), ("b", "INT")]


# ── Sets of files and the PowSyBl copy ────────────────────────────────────────

NS = 'xmlns="http://www.rte-france.com/dynawo"'


def _file(*elements: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?><parametersSet {NS}>{"".join(elements)}</parametersSet>'.encode()


GENS = _file('<macroParameterSet id="M"><par type="DOUBLE" name="generator_H" value="5"/></macroParameterSet>',
             '<set id="Gen"><macroParSet id="M"/><par type="DOUBLE" name="generator_SNom" value="100"/></set>',
             '<set id="Unused"/>')
LOADS = _file('<macroParameterSet id="M"><par type="DOUBLE" name="load_alpha" value="2"/></macroParameterSet>',
              '<set id="Gen"><macroParSet id="M"/></set>')                     # same ids as in gens.par


def test_sets_used_are_copied_once_and_clashing_ids_renamed():
    specs = [(FILE, "gens.par", "Gen"), (FILE, "loads.par", "Gen"), (FILE, "gens.par", "Gen")]

    content, ids, to_fill = assemble_par(specs, {"gens.par": GENS, "loads.par": LOADS}, None)

    assert ids == {(FILE, "gens.par", "Gen"): "Gen", (FILE, "loads.par", "Gen"): "Gen_2"}
    sets = parse_par(content)
    assert set(sets) == {"Gen", "Gen_2"} and to_fill == []                    # "Unused" left out
    assert (sets["Gen"]["macro_id"], sets["Gen_2"]["macro_id"]) == ("M", "M_2")  # each its own macro
    assert {p["name"] for p in sets["Gen_2"]["pars"]} == {"load_alpha"}


def test_set_of_a_file_keeps_its_id_against_a_manual_one():
    specs = [(MANUAL, "", "Gen"), (FILE, "gens.par", "Gen")]

    _, ids, to_fill = assemble_par(specs, {"gens.par": GENS}, None)

    assert ids == {(FILE, "gens.par", "Gen"): "Gen", (MANUAL, "", "Gen"): "Gen_2"}
    assert to_fill == ["Gen_2"]


def test_powsybl_copy_expands_macros_and_drops_what_powsybl_cannot_read():
    content = _file(
        '<macroParameterSet id="M"><par type="DOUBLE" name="a" value="1"/><par type="DOUBLE" name="b" value="1"/>'
        '<reference type="DOUBLE" name="r" origData="IIDM" origName="p_pu"/></macroParameterSet>',
        '<set id="S"><macroParSet id="M"/><par type="DOUBLE" name="b" value="2"/>'
        '<refPar id="Other" name="c"/><parTable type="DOUBLE" name="t"/></set>')

    sets = parse_par(for_powsybl(content))

    assert sets["S"]["macro_id"] is None
    assert {p["name"]: p["value"] for p in sets["S"]["pars"]} == {"a": "1", "b": "2"}   # the set's own value wins
    assert [r["name"] for r in sets["S"]["refs"]] == ["r"]


@pytest.mark.parametrize(("rule", "error"), [
    ({"par_source": "elsewhere"}, "unknown parameter source 'elsewhere'"),
    ({"par_source": "file", "par_file": ""}, "no parameters file chosen"),
    ({"par_source": "file", "par_file": "nope.par", "parameter_set_id": "Gen"}, "parameters file 'nope.par' is not in the session"),
    ({"par_source": "file", "par_file": "gens.par", "parameter_set_id": ""}, "no parameter set chosen"),
    ({"par_source": "file", "par_file": "gens.par", "parameter_set_id": "Nope"}, "parameter set 'Nope' is not in 'gens.par'"),
    ({"par_source": "generic", "model": "LoadPQ"}, "the generic parameters file has no set for model 'LoadPQ'"),
    ({"par_source": "manual", "parameter_set_id": "", "model": ""}, "no parameter set name"),
])
def test_rule_parameter_errors(rule, error):
    assert check_rule_parameters(rule, {"gens.par": {"Gen"}}, {"LoadAlphaBeta"}) == [error]


def test_rule_parameters_without_error():
    par_sets = {"gens.par": {"Gen"}}

    assert check_rule_parameters({"par_source": "file", "par_file": "gens.par", "parameter_set_id": "Gen"}, par_sets, None) == []
    assert check_rule_parameters({"par_source": "generic", "model": "LoadPQ"}, par_sets, None) == \
        ["the generic parameters file is not available yet"]
    # A manual set defaults to the model's name.
    assert spec_key({"par_source": "manual", "model": "LoadPQ"}) == (MANUAL, "", "LoadPQ")
    assert check_rule_parameters({"par_source": "manual", "model": "LoadPQ"}, par_sets, None) == []


def test_set_ids_of_a_file_follow_its_changes(tmp_path):
    path = tmp_path / "gens.par"
    path.write_bytes(GENS)
    assert file_set_ids(str(path)) == ["Gen", "Unused"]

    path.write_bytes(LOADS + b"\n")                                            # another size: read again
    assert file_set_ids(str(path)) == ["Gen"]
    assert file_set_ids(str(tmp_path / "missing.par")) is None
    assert set_ids(b"not xml") == []
