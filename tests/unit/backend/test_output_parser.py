#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

# tests/data/outputs is hand-written with the layout of a Dynawo output folder.
# It checks what the GUI extracts from such files, not that real Dynawo files
# look like this, which only a run of the real Dynawo can tell.

import pytest

from backend.output_parser import (
    find_constraints_xml,
    find_dynawo_log,
    find_lost_equipments_xml,
    find_timeline_xml,
    parse_constraints,
    parse_lost_equipments,
    parse_timeline,
)

INVALID_XML = b"<timeline><event"


@pytest.fixture
def outputs(data_dir):
    return data_dir / "outputs"


def test_timeline_events_with_numeric_times(outputs):
    events = parse_timeline((outputs / "timeLine/timeline.xml").read_bytes())

    assert events == [
        {"time": 1.0, "model_name": "EV_LINE", "message": "LINE : opening both sides"},
        {"time": 1.05, "model_name": "GEN1", "message": "PMIN : activation"},
    ]


def test_timeline_keeps_a_time_that_is_not_a_number():
    content = b"""<timeline xmlns="http://www.rte-france.com/dynawo">
      <event time="n/a" modelName="GEN1" message="m"/>
    </timeline>"""

    assert parse_timeline(content)[0]["time"] == "n/a"


def test_constraint_with_every_attribute(outputs):
    constraints = parse_constraints((outputs / "constraints/constraints.xml").read_bytes())

    assert constraints[0] == {
        "model_name": "L1-2-1", "description": "OverloadUp", "time": 2.5, "type": "Line",
        "kind": "OverloadUp", "limit": 500.0, "value": 612.3, "side": "1",
        "acceptable_duration": 600.0,
    }


def test_constraint_without_optional_attributes_omits_them(outputs):
    constraints = parse_constraints((outputs / "constraints/constraints.xml").read_bytes())

    assert constraints[1] == {"model_name": "BUS1", "description": "USupUmax", "time": 3.0}


def test_lost_equipments(outputs):
    assert parse_lost_equipments((outputs / "lostEquipments/lostEquipments.xml").read_bytes()) == [
        {"id": "L1-2", "type": "LINE"},
        {"id": "LOAD1", "type": "LOAD"},
    ]


@pytest.mark.parametrize("parse", [parse_timeline, parse_constraints, parse_lost_equipments])
def test_invalid_xml_gives_an_empty_list(parse):
    assert parse(INVALID_XML) == []


def test_output_files_are_found_in_their_subfolders(outputs):
    assert find_timeline_xml(str(outputs)) == str(outputs / "timeLine/timeline.xml")
    assert find_constraints_xml(str(outputs)) == str(outputs / "constraints/constraints.xml")
    assert find_lost_equipments_xml(str(outputs)) == str(outputs / "lostEquipments/lostEquipments.xml")
    assert find_dynawo_log(str(outputs)) == str(outputs / "logs/dynawo.log")


def test_log_with_a_name_set_in_the_jobs_file(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "my_case.log").write_text("log")

    assert find_dynawo_log(str(tmp_path), "my_case.log") == str(tmp_path / "logs/my_case.log")
    assert find_dynawo_log(str(tmp_path)) is None


@pytest.mark.parametrize("find", [find_timeline_xml, find_constraints_xml, find_lost_equipments_xml, find_dynawo_log])
def test_missing_output_file_is_none(find, tmp_path):
    assert find(str(tmp_path)) is None
