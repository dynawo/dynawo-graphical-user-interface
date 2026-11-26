#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Tests of backend/jobs_hints.py."""

import pytest

from backend.jobs_document import check
from backend.jobs_hints import load_hints, new_element
from backend.jobs_schema import load_jobs_schema

NO_HINTS = {"elements": {}, "attributes": {}}


@pytest.fixture
def schema(data_dir):
    return load_jobs_schema(str(data_dir / "xsd" / "jobs.xsd"))


def _tags(node: dict) -> list[str]:
    return [c["tag"] for c in node["children"]]


def test_without_hints_a_new_element_has_its_required_content_only(schema):
    job = new_element(schema.find("jobs/job"), NO_HINTS)
    assert job["attrs"] == {"name": ""}
    assert _tags(job) == ["solver", "modeler", "simulation"]
    modeler = job["children"][1]
    assert _tags(modeler) == ["dynModels", "precompiledModels", "modelicaModels"]
    assert modeler["children"][1]["attrs"] == {"useStandardModels": ""}


def test_a_hint_default_fills_an_attribute(schema):
    hints = {"elements": {}, "attributes": {"solver@lib": {"default": "dynawo_SolverIDA"}}}
    solver = new_element(schema.find("jobs/job/solver"), hints)
    assert solver["attrs"] == {"lib": "dynawo_SolverIDA", "parFile": "", "parId": ""}


def test_a_hint_default_adds_an_optional_attribute(schema):
    hints = {"elements": {}, "attributes": {"timeline@exportTime": {"default": "true"}}}
    timeline = new_element(schema.find("jobs/job/outputs/timeline"), hints)
    assert timeline["attrs"] == {"exportMode": "", "exportTime": "true"}


def test_a_hint_creates_an_optional_child(schema):
    hints = {"elements": {"outputs": {"create": True}}, "attributes": {}}
    job = new_element(schema.find("jobs/job"), hints)
    assert _tags(job) == ["solver", "modeler", "simulation", "outputs"]


def test_hints_for_what_the_schema_does_not_have_are_ignored(schema):
    hints = {"elements": {"linearization": {"create": True}},
             "attributes": {"solver@unknown": {"default": "x"}}}
    assert new_element(schema.find("jobs/job/solver"), hints)["attrs"] == {"lib": "", "parFile": "", "parId": ""}
    assert "linearization" not in _tags(new_element(schema.find("jobs/job/outputs"), hints))


def test_a_new_job_from_the_app_hints_is_valid_once_its_files_are_given(schema):
    job = new_element(schema.find("jobs/job"))
    job["children"][0]["attrs"].update(parFile="solver.par", parId="IDA")
    job["children"][1]["children"][0]["attrs"]["dydFile"] = "models.dyd"
    _, issues = check({"root": {"tag": "jobs", "attrs": {}, "children": [job]}}, schema)
    assert issues == []


def test_the_app_hints_file_is_well_formed():
    hints = load_hints()
    for key, hint in hints["attributes"].items():
        element, _, attribute = key.partition("@")
        assert element and attribute, key
        assert set(hint) <= {"label", "help", "unit", "default", "values"}, key
    for name, hint in hints["elements"].items():
        assert set(hint) <= {"label", "help", "create"}, name
