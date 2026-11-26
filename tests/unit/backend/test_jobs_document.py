#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Tests of backend/jobs_document.py."""

import copy
import shutil

import pytest

from backend.jobs_document import (
    JobsConflictError,
    JobsDocumentError,
    SessionContext,
    check,
    file_token,
    parse,
    read,
    render,
    write,
)
from backend.jobs_schema import load_jobs_schema
from tests.conftest import canonical_xml


@pytest.fixture
def schema(data_dir):
    return load_jobs_schema(str(data_dir / "xsd" / "jobs.xsd"))


@pytest.fixture
def valid(data_dir) -> dict:
    return read(str(data_dir / "jobs" / "valid.jobs"))[0]


@pytest.fixture
def session(tmp_path, data_dir):
    """A session folder holding valid.jobs and the files it references."""
    shutil.copy(data_dir / "jobs" / "valid.jobs", tmp_path / "case.jobs")
    shutil.copy(data_dir / "par" / "network_and_solver.par", tmp_path / "network_and_solver.par")
    (tmp_path / "network.iidm").write_text("<network/>")
    (tmp_path / "events.dyd").write_text("<dynamicModelsArchitecture/>")
    types = {"case.jobs": "jobs", "network_and_solver.par": "par", "network.iidm": "iidm", "events.dyd": "dyd"}
    return SessionContext(working_dir=str(tmp_path), jobs_file="case.jobs", file_types=types)


def _job(doc: dict) -> dict:
    return doc["root"]["children"][0]


def _child(node: dict, tag: str) -> dict:
    return next(c for c in node["children"] if c.get("tag") == tag)


def _tags(node: dict) -> list[str]:
    return [c.get("tag", "#comment") for c in node["children"]]


# ── Reading and rendering ────────────────────────────────────────────────────

def test_parse_gives_a_tree_of_the_file(valid):
    assert valid["nsmap"] == {"dyn": "http://www.rte-france.com/dynawo"}
    assert valid["prolog"] == [{"comment": " A job valid against tests/data/xsd/jobs.xsd, with comments. "}]
    assert valid["root"]["tag"] == "jobs"
    assert _child(_job(valid), "solver")["attrs"] == {
        "lib": "dynawo_SolverIDA", "parFile": "network_and_solver.par", "parId": "IDA"}
    assert _tags(_child(_job(valid), "modeler")) == [
        "network", "#comment", "dynModels", "precompiledModels", "modelicaModels"]


def test_render_gives_back_the_file(valid, schema, data_dir):
    original = (data_dir / "jobs" / "valid.jobs").read_text()
    rendered = render(valid, schema)
    assert canonical_xml(rendered) == canonical_xml(original)
    assert "<!-- the events -->" in rendered
    assert rendered.splitlines()[1] == "<!-- A job valid against tests/data/xsd/jobs.xsd, with comments. -->"


def test_the_namespace_prefix_is_kept(schema):
    default_ns = '<jobs xmlns="http://www.rte-france.com/dynawo"><job name="a"/></jobs>'
    assert "<jobs xmlns=" in render(parse(default_ns), schema)
    prefixed = '<dyn:jobs xmlns:dyn="http://www.rte-france.com/dynawo"><dyn:job name="a"/></dyn:jobs>'
    assert "<dyn:job " in render(parse(prefixed), schema)


def test_unknown_elements_and_attributes_are_kept(schema):
    doc = parse('<jobs xmlns="http://www.rte-france.com/dynawo"><job name="a" extra="1"><future x="y"/></job></jobs>')
    rendered = render(doc, schema)
    assert 'extra="1"' in rendered
    assert '<future x="y"/>' in rendered


def test_xml_that_does_not_parse_gives_its_line(schema):
    with pytest.raises(JobsDocumentError) as exc:
        parse('<jobs xmlns="http://www.rte-france.com/dynawo">\n<job name="a">\n</jobs>')
    assert exc.value.line == 3


def test_an_invalid_tree_is_refused(schema):
    with pytest.raises(JobsDocumentError):
        render({"root": {"tag": "bad tag", "attrs": {}, "children": []}}, schema)


# ── Ordering ─────────────────────────────────────────────────────────────────

def test_sequence_children_are_put_in_schema_order(valid, schema):
    modeler = _child(_job(valid), "modeler")
    modeler["children"].reverse()  # modelica, precompiled, dynModels, comment, network
    rendered = parse(render(valid, schema))
    assert _tags(_child(_job(rendered), "modeler")) == [
        "#comment", "network", "dynModels", "precompiledModels", "modelicaModels"]


def test_a_comment_moves_with_the_element_after_it(valid, schema):
    modeler = _child(_job(valid), "modeler")
    network = modeler["children"].pop(0)
    modeler["children"].append(network)  # comment, dynModels, precompiled, modelica, network
    rendered = parse(render(valid, schema))
    assert _tags(_child(_job(rendered), "modeler")) == [
        "network", "#comment", "dynModels", "precompiledModels", "modelicaModels"]


def test_the_children_of_a_job_keep_their_order(valid, schema):
    _job(valid)["children"].reverse()
    rendered = parse(render(valid, schema))
    assert _tags(_job(rendered)) == ["outputs", "simulation", "modeler", "solver"]


# ── Schema errors ────────────────────────────────────────────────────────────

def test_a_valid_document_has_no_issue(valid, schema, session):
    assert check(valid, schema, session)[1] == []


def test_a_missing_element_is_reported_where_it_was_expected(valid, schema):
    modeler = _child(_job(valid), "modeler")
    modeler["children"] = [c for c in modeler["children"] if c.get("tag") != "precompiledModels"]
    _, issues = check(valid, schema)
    assert len(issues) == 1
    assert issues[0]["level"] == "error"
    assert issues[0]["path"] == [0, 1, 3]  # modelicaModels, found in its place
    assert "precompiledModels" in issues[0]["message"]
    assert "{http" not in issues[0]["message"]


def test_a_wrong_value_is_reported_on_its_element(valid, schema):
    outputs = _child(_job(valid), "outputs")
    _child(outputs, "timeline")["attrs"]["exportMode"] = "PDF"
    normalised, issues = check(valid, schema)
    assert [(i["path"], i["attribute"]) for i in issues] == [([0, 3, 0], "exportMode")]
    assert _child(_job(normalised), "outputs") is normalised["root"]["children"][0]["children"][3]


def test_a_repeated_dyd_file_is_reported(valid, schema):
    modeler = _child(_job(valid), "modeler")
    modeler["children"].insert(3, copy.deepcopy(_child(modeler, "dynModels")))
    _, issues = check(valid, schema)
    assert len(issues) == 1
    assert "Duplicate" in issues[0]["message"]


# ── File warnings ────────────────────────────────────────────────────────────

def _warnings(doc, schema, context) -> list[tuple]:
    return [(i["path"], i.get("attribute"), i["message"]) for i in check(doc, schema, context)[1]
            if i["level"] == "warning"]


def test_a_file_not_in_the_session_is_reported(valid, schema, session):
    _child(_child(_job(valid), "modeler"), "network")["attrs"]["iidmFile"] = "other.iidm"
    assert _warnings(valid, schema, session) == [([0, 1, 0], "iidmFile", "other.iidm is not in the session")]


def test_a_file_of_the_wrong_type_is_reported(valid, schema, session):
    _child(_job(valid), "solver")["attrs"]["parFile"] = "events.dyd"
    assert _warnings(valid, schema, session) == [([0, 0], "parFile", "events.dyd is not a .par file")]


@pytest.mark.parametrize("ref", ["../outside.dyd", "/abs/path.dyd"])
def test_a_file_outside_the_session_is_reported(valid, schema, session, ref):
    _child(_child(_job(valid), "modeler"), "dynModels")["attrs"]["dydFile"] = ref
    assert _warnings(valid, schema, session) == [([0, 1, 2], "dydFile", f"{ref} is outside the session")]


def test_an_empty_file_reference_is_reported(valid, schema, session):
    _child(_child(_job(valid), "modeler"), "dynModels")["attrs"]["dydFile"] = ""
    assert _warnings(valid, schema, session) == [([0, 1, 2], "dydFile", "No file given")]


def test_a_par_id_missing_from_its_file_is_reported(valid, schema, session):
    _child(_job(valid), "solver")["attrs"]["parId"] = "SIM"
    assert _warnings(valid, schema, session) == [([0, 0], "parId", "Set SIM not found in network_and_solver.par")]


def test_references_are_relative_to_the_jobs_folder(valid, schema, session):
    # The jobs file moves to sub/, the referenced files stay at the root.
    _child(_child(_job(valid), "modeler"), "dynModels")["attrs"]["dydFile"] = "../events.dyd"
    _child(_child(_job(valid), "modeler"), "network")["attrs"]["iidmFile"] = "network.iidm"
    session.jobs_file = "sub/case.jobs"
    warnings = _warnings(valid, schema, session)
    assert ([0, 1, 2], "dydFile", "../events.dyd is not in the session") not in warnings
    assert ([0, 1, 0], "iidmFile", "network.iidm is not in the session") in warnings


def test_a_start_time_after_the_stop_time_is_reported(valid, schema, session):
    _child(_job(valid), "simulation")["attrs"]["startTime"] = "20"
    assert _warnings(valid, schema, session) == [([0, 2], "stopTime", "startTime must be lower than stopTime")]


def test_two_jobs_with_the_same_name_are_reported(valid, schema, session):
    valid["root"]["children"].append(copy.deepcopy(_job(valid)))
    assert _warnings(valid, schema, session) == [([1], "name", "Another job is named case")]


# ── Writing ──────────────────────────────────────────────────────────────────

def test_write_saves_what_render_shows(valid, schema, session):
    path = f"{session.working_dir}/case.jobs"
    _, token = read(path)
    _child(_job(valid), "simulation")["attrs"]["stopTime"] = "30"
    doc, new_token, issues = write(path, valid, schema, token, session)
    content = open(path, "rb").read()
    assert content.decode() == render(valid, schema)
    assert new_token == file_token(content)
    assert issues == []
    assert read(path)[0] == doc


def test_a_document_with_errors_is_saved_all_the_same(valid, schema, session):
    path = f"{session.working_dir}/case.jobs"
    _, token = read(path)
    _child(_job(valid), "outputs")["children"][0]["attrs"]["exportMode"] = "PDF"
    _, _, issues = write(path, valid, schema, token, session)
    assert issues and issues[0]["level"] == "error"
    assert 'exportMode="PDF"' in open(path).read()


def test_a_file_changed_since_it_was_read_is_not_overwritten(valid, schema, session):
    path = f"{session.working_dir}/case.jobs"
    _, token = read(path)
    with open(path, "a") as fh:
        fh.write("<!-- changed by another page -->")
    before = open(path).read()
    with pytest.raises(JobsConflictError, match="changed since it was read"):
        write(path, valid, schema, token, session)
    assert open(path).read() == before


def test_a_new_file_is_created_and_never_overwrites_one(valid, schema, session):
    path = f"{session.working_dir}/new/case2.jobs"
    write(path, valid, schema, None, session)
    assert read(path)[0]["root"]["tag"] == "jobs"
    with pytest.raises(JobsConflictError, match="already exists"):
        write(path, valid, schema, None, session)
