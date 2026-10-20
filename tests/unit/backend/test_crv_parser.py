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


def test_reads_model_and_variable_of_each_curve(data_dir):
    curves = parse_crv((data_dir / "crv/two_curves.expected.crv").read_bytes())

    assert curves == [
        {"model": "NETWORK", "variable": "BUS1_Upu_value"},
        {"model": "GEN1", "variable": "generator_omegaPu"},
    ]


def test_incomplete_curves_are_left_out():
    content = b"""<curvesInput xmlns="http://www.rte-france.com/dynawo">
      <curve model="GEN1" variable="generator_omegaPu"/>
      <curve model="GEN1"/>
      <curve variable="generator_PGenPu"/>
      <curve model="" variable="generator_QGenPu"/>
    </curvesInput>"""

    assert parse_crv(content) == [{"model": "GEN1", "variable": "generator_omegaPu"}]


def test_invalid_xml_gives_no_curves():
    assert parse_crv(b"<curvesInput><curve") == []
