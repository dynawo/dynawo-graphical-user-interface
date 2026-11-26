#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.curves_loader import (
    curves_time_and_signals,
    load_curves,
    load_curves_csv,
    load_curves_hdf5,
    load_curves_xml,
    read_crv_info,
)


def test_crv_info_names_each_column_after_model_and_variable(data_dir):
    assert read_crv_info(str(data_dir / "crv/two_curves.expected.crv")) == {
        "NETWORK_BUS1_Upu_value": ("NETWORK", "BUS1_Upu_value"),
        "GEN1_generator_omegaPu": ("GEN1", "generator_omegaPu"),
    }


def test_crv_info_columns_are_the_ones_of_the_csv(data_dir):
    """The .crv the GUI writes and the CSV Dynawo returns must name columns alike,
    or the plots cannot tell which model and variable a column is."""
    info = read_crv_info(str(data_dir / "crv/two_curves.expected.crv"))
    _, signals = curves_time_and_signals(load_curves_csv(str(data_dir / "outputs")))

    assert set(info) == set(signals)


def test_missing_crv_gives_no_info(tmp_path):
    assert read_crv_info(str(tmp_path / "none.crv")) == {}


def test_curves_csv_time_and_signals(data_dir):
    df = load_curves_csv(str(data_dir / "outputs"))

    time, signals = curves_time_and_signals(df)
    assert time.tolist() == [0.0, 1.0, 2.0]
    assert signals == ["NETWORK_BUS1_Upu_value", "GEN1_generator_omegaPu"]
    assert df["GEN1_generator_omegaPu"].tolist() == [1.0, 0.999, 1.001]


def test_trailing_separator_is_not_a_curve(data_dir):
    """Dynawo ends each line of curves.csv with ';' (checked on the IEEE14
    DisconnectLine reference outputs): pandas read it as an empty "Unnamed: N"
    column, which the plot page offered as a curve."""
    assert (data_dir / "outputs/curves/curves.csv").read_text().splitlines()[0].endswith(";")

    _, signals = curves_time_and_signals(load_curves_csv(str(data_dir / "outputs")))

    assert not [s for s in signals if s.startswith("Unnamed")]


def test_line_without_trailing_separator_is_read_alike(tmp_path):
    (tmp_path / "curves").mkdir()
    (tmp_path / "curves" / "curves.csv").write_text("time;GEN1_generator_omegaPu\n0;1.0\n1;0.99\n")

    _, signals = curves_time_and_signals(load_curves_csv(str(tmp_path)))

    assert signals == ["GEN1_generator_omegaPu"]


def test_missing_csv_is_none(tmp_path):
    assert load_curves_csv(str(tmp_path)) is None


def test_empty_csv_is_none(tmp_path):
    (tmp_path / "curves").mkdir()
    (tmp_path / "curves" / "curves.csv").write_text("")

    assert load_curves_csv(str(tmp_path)) is None


def _write_h5(output_dir, time, names, data):
    """A curves.h5 laid out as Dynawo writes it: time, curve_names, data (time x curves)."""
    import h5py

    (output_dir / "curves").mkdir(parents=True, exist_ok=True)
    with h5py.File(output_dir / "curves" / "curves.h5", "w") as fh:
        fh["time"] = time
        fh.create_dataset("curve_names", data=names, dtype=h5py.string_dtype("ascii"))
        fh["data"] = data


def test_curves_hdf5_gives_the_same_table_as_the_csv(tmp_path):
    _write_h5(tmp_path, [0.0, 1.0, 2.0], ["NETWORK_BUS1_Upu_value", "GEN1_generator_omegaPu"],
              [[1.02, 1.0], [0.98, 0.999], [1.01, 1.001]])
    df = load_curves_hdf5(str(tmp_path))
    time, signals = curves_time_and_signals(df)
    assert list(time) == [0.0, 1.0, 2.0]
    assert signals == ["NETWORK_BUS1_Upu_value", "GEN1_generator_omegaPu"]
    assert list(df["GEN1_generator_omegaPu"]) == [1.0, 0.999, 1.001]


def test_curves_hdf5_stored_curve_by_curve_is_read_too(tmp_path):
    _write_h5(tmp_path, [0.0, 1.0, 2.0], ["A_x", "B_y"], [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    assert list(load_curves_hdf5(str(tmp_path))["B_y"]) == [4.0, 5.0, 6.0]


def test_curves_hdf5_with_inconsistent_sizes_is_not_read(tmp_path):
    _write_h5(tmp_path, [0.0, 1.0], ["A_x", "B_y"], [[1.0, 2.0, 3.0]])
    assert load_curves_hdf5(str(tmp_path)) is None


def test_load_curves_reads_csv_or_hdf5(tmp_path, data_dir):
    assert load_curves(str(tmp_path)) is None
    _write_h5(tmp_path, [0.0], ["A_x"], [[1.0]])
    assert list(load_curves(str(tmp_path)).columns) == ["time", "A_x"]
    assert list(load_curves(str(data_dir / "outputs")).columns)[0] == "time"


def _write_xml(output_dir, curves: str) -> None:
    """A curves.xml laid out as Dynawo writes it: curve elements holding their points."""
    (output_dir / "curves").mkdir(parents=True, exist_ok=True)
    (output_dir / "curves" / "curves.xml").write_text(
        '<?xml version="1.0" encoding="ISO-8859-1" standalone="no"?>\n'
        f'<curvesOutput xmlns="http://www.rte-france.com/dynawo">{curves}</curvesOutput>',
        encoding="iso-8859-1")


def test_curves_xml_gives_the_same_table_as_the_csv(tmp_path):
    _write_xml(tmp_path,
               '<curve model="NETWORK" variable="BUS1_Upu_value">'
               '<point time="0.0" value="1.02"/><point time="1.0" value="0.98"/></curve>'
               '<curve model="GEN1" variable="generator_omegaPu">'
               '<point time="0.0" value="1.0"/><point time="1.0" value="0.999"/></curve>')
    time, signals = curves_time_and_signals(load_curves_xml(str(tmp_path)))
    assert list(time) == [0.0, 1.0]
    assert signals == ["NETWORK_BUS1_Upu_value", "GEN1_generator_omegaPu"]


def test_curves_xml_keeps_repeated_times(tmp_path):
    # Dynawo writes several points at the time of an event.
    _write_xml(tmp_path, '<curve model="A" variable="x"><point time="0" value="1"/>'
                         '<point time="1" value="2"/><point time="1" value="3"/></curve>')
    df = load_curves_xml(str(tmp_path))
    assert list(df["time"]) == [0.0, 1.0, 1.0]
    assert list(df["A_x"]) == [1.0, 2.0, 3.0]


def test_curves_xml_on_different_time_grids_are_joined_on_time(tmp_path):
    _write_xml(tmp_path, '<curve model="A" variable="x"><point time="0" value="1"/><point time="2" value="3"/></curve>'
                         '<curve model="B" variable="y"><point time="1" value="5"/></curve>')
    df = load_curves_xml(str(tmp_path))
    assert list(df["time"]) == [0.0, 1.0, 2.0]
    assert df["B_y"].isna().tolist() == [True, False, True]


def test_curves_xml_that_does_not_parse_is_not_read(tmp_path):
    _write_xml(tmp_path, '<curve model="A" variable="x"><point time="0" value="1"/>')
    assert load_curves_xml(str(tmp_path)) is None


def test_load_curves_reads_xml_too(tmp_path):
    _write_xml(tmp_path, '<curve model="A" variable="x"><point time="0" value="1"/></curve>')
    assert list(load_curves(str(tmp_path)).columns) == ["time", "A_x"]
