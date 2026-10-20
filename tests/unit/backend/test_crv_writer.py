#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.crv_parser import parse_crv
from backend.crv_writer import build_crv_bytes, write_crv

CURVES = [
    {"model": "NETWORK", "variable": "BUS1_Upu_value"},
    {"model": "GEN1", "variable": "generator_omegaPu"},
]


def test_round_trip_keeps_curves_and_order():
    assert parse_crv(build_crv_bytes(CURVES)) == CURVES


def test_output_matches_expected_file(assert_same_xml):
    assert_same_xml(build_crv_bytes(CURVES), "crv/two_curves.expected.crv")


def test_written_file_is_the_same_as_the_built_bytes(tmp_path):
    crv = tmp_path / "case.crv"

    write_crv(str(crv), CURVES)

    assert crv.read_bytes() == build_crv_bytes(CURVES)


def test_no_curves_gives_an_empty_valid_file():
    assert parse_crv(build_crv_bytes([])) == []
