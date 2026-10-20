#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.curves_loader import curves_time_and_signals, load_curves_csv, read_crv_info


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
