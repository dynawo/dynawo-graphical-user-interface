#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Tests of backend/jobs_schema.py."""

import json
import os
import shutil

import pytest
from lxml import etree

from backend.jobs_schema import (
    JobsSchemaError,
    jobs_xsd_path,
    load_jobs_schema,
    schema_for_executable,
)


@pytest.fixture
def schema(data_dir):
    return load_jobs_schema(str(data_dir / "xsd" / "jobs.xsd"))


def _xsd(tmp_path, body: str) -> str:
    """A small XSD in Dynawo's namespace, written to a file."""
    path = tmp_path / "jobs.xsd"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
        ' xmlns:dyn="http://www.rte-france.com/dynawo"'
        ' targetNamespace="http://www.rte-france.com/dynawo" elementFormDefault="qualified">'
        f"{body}</xs:schema>")
    return str(path)


# ── Structure ────────────────────────────────────────────────────────────────

def test_the_root_is_jobs_holding_any_number_of_jobs(schema):
    assert schema.root.name == "jobs"
    job = schema.find("jobs/job")
    assert (job.min, job.max) == (1, None)
    assert job.attribute("name").required


def test_xs_all_children_are_unordered_with_their_own_occurrence(schema):
    job = schema.find("jobs/job")
    assert job.ordered is False
    assert [(c.name, c.min, c.max) for c in job.children] == [
        ("solver", 1, 1), ("modeler", 1, 1), ("simulation", 1, 1), ("outputs", 0, 1),
    ]


def test_sequence_children_are_ordered(schema):
    modeler = schema.find("jobs/job/modeler")
    assert modeler.ordered is True
    assert [(c.name, c.min, c.max) for c in modeler.children] == [
        ("network", 0, 1), ("dynModels", 1, None), ("precompiledModels", 1, 1), ("modelicaModels", 1, 1),
    ]


def test_a_type_used_in_two_places_is_read_in_both(schema):
    for parent in ("precompiledModels", "modelicaModels"):
        directory = schema.find(f"jobs/job/modeler/{parent}/directory")
        assert (directory.min, directory.max) == (0, None)
        assert [a.name for a in directory.attributes] == ["path", "recursive"]


def test_unique_constraints_are_read(schema):
    unique = schema.find("jobs/job/modeler").unique
    assert [(u.element, u.attribute) for u in unique] == [("dynModels", "dydFile")]


def test_an_empty_element_is_kept(schema):
    lost = schema.find("jobs/job/outputs/lostEquipments")
    assert (lost.attributes, lost.children) == ((), ())


def test_find_returns_none_for_an_unknown_path(schema):
    assert schema.find("jobs/job/nothing") is None
    assert schema.find("job") is None


# ── Attributes ───────────────────────────────────────────────────────────────

def test_attribute_types(schema):
    simulation = schema.find("jobs/job/simulation")
    assert simulation.attribute("startTime").to_dict() == {"name": "startTime", "type": "number", "required": True}
    assert simulation.attribute("criteriaStep").to_dict() == {"name": "criteriaStep", "type": "integer", "required": False}
    assert schema.find("jobs/job/modeler/precompiledModels").attribute("useStandardModels").type == "boolean"
    assert schema.find("jobs/job/solver").attribute("lib").type == "string"


def test_an_enumeration_gives_its_values(schema):
    export_mode = schema.find("jobs/job/outputs/timeline").attribute("exportMode")
    assert (export_mode.type, export_mode.values, export_mode.required) == ("enum", ("TXT", "CSV", "XML"), True)


def test_a_default_value_is_kept(schema):
    export_mode = schema.find("jobs/job/outputs/curves").attribute("exportMode")
    assert (export_mode.default, export_mode.required) == ("CSV", False)


# ── Serialisation and validation ─────────────────────────────────────────────

def test_the_schema_serialises_to_json(schema):
    data = json.loads(json.dumps(schema.to_dict()))
    job = data["root"]["children"][0]
    assert job["ordered"] is False
    assert job["children"][1]["unique"] == [{"element": "dynModels", "attribute": "dydFile"}]


def test_the_validator_checks_a_jobs_file(schema, data_dir):
    # This job lacks <precompiledModels>, required by the schema.
    doc = etree.parse(str(data_dir / "jobs" / "network_and_solver.jobs"))
    assert not schema.validator.validate(doc)
    assert "precompiledModels" in str(schema.validator.error_log)


# ── Unsupported XSD ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("body, construct", [
    ('<xs:element name="jobs"><xs:complexType><xs:choice>'
     '<xs:element name="a" type="dyn:E"/></xs:choice></xs:complexType></xs:element>'
     '<xs:complexType name="E"/>', "xs:choice"),
    ('<xs:element name="jobs"><xs:complexType><xs:attribute name="t">'
     '<xs:simpleType><xs:restriction base="xs:string"><xs:pattern value="[a-z]+"/>'
     '</xs:restriction></xs:simpleType></xs:attribute></xs:complexType></xs:element>', "xs:pattern"),
    ('<xs:element name="jobs"><xs:complexType>'
     '<xs:attribute name="d" type="xs:date"/></xs:complexType></xs:element>', "xs:date"),
    ('<xs:element name="jobs"><xs:complexType><xs:sequence maxOccurs="unbounded">'
     '<xs:element name="a" type="dyn:E"/></xs:sequence></xs:complexType></xs:element>'
     '<xs:complexType name="E"/>', "repeated content group"),
    ('<xs:element name="jobs" type="xs:string"/>', "no complex type"),
    ('<xs:element name="jobs" type="dyn:T"/><xs:complexType name="T"><xs:sequence>'
     '<xs:element name="jobs" type="dyn:T" minOccurs="0"/></xs:sequence></xs:complexType>', "recursive"),
])
def test_unsupported_xsd_is_refused(tmp_path, body, construct):
    with pytest.raises(JobsSchemaError, match=construct):
        load_jobs_schema(_xsd(tmp_path, body))


def test_a_missing_xsd_names_the_path_looked_at(tmp_path):
    with pytest.raises(JobsSchemaError, match="No jobs.xsd found at .*nowhere"):
        load_jobs_schema(str(tmp_path / "nowhere" / "jobs.xsd"))


def test_an_xsd_that_is_not_xml_is_refused(tmp_path):
    path = tmp_path / "jobs.xsd"
    path.write_text("<xs:schema")
    with pytest.raises(JobsSchemaError, match="not a readable XML file"):
        load_jobs_schema(str(path))


# ── From the Dynawo executable ───────────────────────────────────────────────

@pytest.fixture
def dynawo_install(tmp_path, data_dir):
    """A minimal Dynawo distribution: dynawo.sh and, optionally, share/xsd/jobs.xsd."""
    def _install(with_xsd: bool = True) -> str:
        root = tmp_path / "dynawo"
        (root / "share" / "xsd").mkdir(parents=True, exist_ok=True)
        (root / "dynawo.sh").write_text("#!/bin/sh\n")
        if with_xsd:
            shutil.copy(data_dir / "xsd" / "jobs.xsd", root / "share" / "xsd" / "jobs.xsd")
        return str(root / "dynawo.sh")
    return _install


def test_the_xsd_is_in_share_xsd_next_to_dynawo_sh():
    assert jobs_xsd_path("/opt/dynawo/dynawo.sh") == "/opt/dynawo/share/xsd/jobs.xsd"


def test_schema_for_executable_loads_its_xsd(dynawo_install):
    schema, error = schema_for_executable(dynawo_install())
    assert error is None
    assert schema.find("jobs/job/modeler") is not None


def test_schema_for_executable_reports_a_missing_xsd_without_raising(dynawo_install):
    schema, error = schema_for_executable(dynawo_install(with_xsd=False))
    assert schema is None
    assert "No jobs.xsd found" in error


def test_no_executable_is_not_an_error():
    assert schema_for_executable(None) == (None, None)
    assert schema_for_executable("") == (None, None)


def test_a_replaced_xsd_is_read_again(dynawo_install):
    exe = dynawo_install()
    first, _ = schema_for_executable(exe)
    assert first.find("jobs/job/outputs") is not None

    target = jobs_xsd_path(exe)
    with open(target) as fh:
        content = fh.read()
    with open(target, "w") as fh:
        fh.write(content.replace('<xs:element name="outputs" type="dyn:Outputs" minOccurs="0"/>', ""))
    # Newer mtime, same path: must not come from the cache.
    st = os.stat(target)
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    second, _ = schema_for_executable(exe)
    assert second.find("jobs/job/outputs") is None
