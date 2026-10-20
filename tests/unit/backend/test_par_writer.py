#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.par_parser import parse_par
from backend.par_writer import relocate_table_files, write_par_values

FIXTURE = "par/macro_and_sets.par"


def _value(par_path, set_id: str, name: str) -> str | None:
    pars = parse_par(par_path.read_bytes())[set_id]["pars"]
    return next((p["value"] for p in pars if p["name"] == name), None)


def test_round_trip_without_updates_keeps_the_content(copy_data):
    par = copy_data(FIXTURE)
    before = parse_par(par.read_bytes())

    write_par_values(str(par), "GEN1", {})

    assert parse_par(par.read_bytes()) == before


def test_own_par_is_updated_in_place(copy_data):
    par = copy_data(FIXTURE)

    write_par_values(str(par), "GEN1", {"generator_SNom": "2500"})

    assert _value(par, "GEN1", "generator_SNom") == "2500"
    assert _value(par, "GEN2", "generator_SNom") == "1150"  # GEN1's own value, not the macro's


def test_inherited_par_is_updated_in_the_macro_for_every_set(copy_data):
    par = copy_data(FIXTURE)

    write_par_values(str(par), "GEN2", {"generator_H": "6"})

    assert _value(par, "GEN2", "generator_H") == "6"
    assert _value(par, "GEN1", "generator_H") == "6"        # deliberate shared edit


def test_new_par_is_added_when_its_type_is_known(copy_data):
    par = copy_data(FIXTURE)

    write_par_values(str(par), "LOAD1", {"load_beta": "1.5", "load_gamma": "3"}, types={"load_beta": "DOUBLE"})

    pars = {p["name"]: p for p in parse_par(par.read_bytes())["LOAD1"]["pars"]}
    assert pars["load_beta"]["type"] == "DOUBLE"
    assert pars["load_beta"]["value"] == "1.5"
    assert "load_gamma" not in pars                          # no type: skipped, not guessed


def test_unknown_set_leaves_the_file_untouched(copy_data):
    par = copy_data(FIXTURE)
    before = par.read_bytes()

    write_par_values(str(par), "NO_SUCH_SET", {"generator_H": "6"})

    assert par.read_bytes() == before


def test_output_matches_expected_file(copy_data, assert_same_xml):
    par = copy_data(FIXTURE)

    write_par_values(str(par), "GEN1", {"generator_SNom": "2500"})
    write_par_values(str(par), "GEN2", {"generator_H": "6"})
    write_par_values(str(par), "LOAD1", {"load_beta": "1.5"}, types={"load_beta": "DOUBLE"})

    assert_same_xml(par.read_bytes(), "par/macro_and_sets.expected.par")


def test_table_files_under_the_run_dir_are_relocated(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "diagrams").mkdir(parents=True)
    table = run_dir / "diagrams" / "HVDC1_Diagram.txt"
    table.write_text("table content")
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("not ours")
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    par = session_dir / "case.par"
    par.write_text(f"""<parametersSet xmlns="http://www.rte-france.com/dynawo">
      <set id="HVDC1">
        <par type="STRING" name="hvdc_QInj1MinTableFile" value="{table}"/>
        <par type="STRING" name="hvdc_QInj1MaxTableFile" value="{table}"/>
        <par type="STRING" name="other_file" value="{outside}"/>
        <par type="DOUBLE" name="hvdc_PMaxPu" value="10"/>
      </set>
    </parametersSet>""")

    copied = relocate_table_files(str(par), str(run_dir))

    assert copied == ["Tables/HVDC1_Diagram.txt"]           # copied once, even if referenced twice
    assert (session_dir / "Tables" / "HVDC1_Diagram.txt").read_text() == "table content"
    assert _value(par, "HVDC1", "hvdc_QInj1MinTableFile") == "Tables/HVDC1_Diagram.txt"
    assert _value(par, "HVDC1", "hvdc_QInj1MaxTableFile") == "Tables/HVDC1_Diagram.txt"
    assert _value(par, "HVDC1", "other_file") == str(outside)  # outside the run dir: kept as is


def test_par_without_table_files_is_not_rewritten(copy_data, tmp_path):
    par = copy_data(FIXTURE)
    before = par.read_bytes()

    assert relocate_table_files(str(par), str(tmp_path / "run")) == []
    assert par.read_bytes() == before
