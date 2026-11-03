#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.association_par import MANUAL, assemble_par, for_powsybl
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
