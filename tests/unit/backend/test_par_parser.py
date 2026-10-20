#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.par_parser import macro_usage, parse_par


def _by_name(entries: list[dict]) -> dict[str, dict]:
    return {e["name"]: e for e in entries}


def test_plain_set_reads_pars_and_references(data_dir):
    sets = parse_par((data_dir / "par/macro_and_sets.par").read_bytes())

    load = sets["LOAD1"]
    assert load["macro_id"] is None
    assert load["pars"] == [
        {"name": "load_alpha", "type": "DOUBLE", "value": "2", "shared": False},
        {"name": "load_isControllable", "type": "BOOL", "value": "false", "shared": False},
    ]
    assert load["refs"] == [
        {"name": "load_P0Pu", "origData": "IIDM", "origName": "p_pu", "shared": False},
    ]


def test_set_inherits_macro_entries_as_shared(data_dir):
    sets = parse_par((data_dir / "par/macro_and_sets.par").read_bytes())

    gen2 = sets["GEN2"]
    assert gen2["macro_id"] == "MACRO_GEN"
    pars = _by_name(gen2["pars"])
    assert pars["generator_H"] == {"name": "generator_H", "type": "DOUBLE", "value": "5.4", "shared": True}
    assert pars["generator_SNom"]["value"] == "1150"
    assert _by_name(gen2["refs"])["generator_P0Pu"]["shared"] is True


def test_own_par_overrides_the_macro(data_dir):
    pars = _by_name(parse_par((data_dir / "par/macro_and_sets.par").read_bytes())["GEN1"]["pars"])

    assert pars["generator_SNom"] == {"name": "generator_SNom", "type": "DOUBLE", "value": "2000", "shared": False}
    assert pars["generator_H"]["shared"] is True            # still inherited
    assert pars["generator_ExcitationPu"]["shared"] is False


def test_unknown_macro_contributes_nothing():
    content = b"""<parametersSet xmlns="http://www.rte-france.com/dynawo">
      <set id="S"><macroParSet id="MISSING"/><par type="INT" name="a" value="1"/></set>
    </parametersSet>"""

    s = parse_par(content)["S"]
    assert s["macro_id"] == "MISSING"
    assert s["pars"] == [{"name": "a", "type": "INT", "value": "1", "shared": False}]


def test_macro_usage_lists_every_set_of_a_macro(data_dir):
    assert macro_usage((data_dir / "par/macro_and_sets.par").read_bytes()) == {"MACRO_GEN": ["GEN1", "GEN2"]}
