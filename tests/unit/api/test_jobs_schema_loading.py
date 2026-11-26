#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Tests of the jobs schema reload when the session's Dynawo changes."""

import pytest

from api.session_store import SessionStore

# A jobs.xsd with only <jobs>, to tell two Dynawo installs apart.
_EMPTY_JOBS_XSD = (
    '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"'
    ' targetNamespace="http://www.rte-france.com/dynawo" elementFormDefault="qualified">'
    '<xs:element name="jobs"><xs:complexType/></xs:element></xs:schema>'
)


@pytest.fixture
def dynawo_install(tmp_path, data_dir):
    """A minimal Dynawo distribution: dynawo.sh and, optionally, share/xsd/jobs.xsd."""
    def _install(name: str, xsd: str | None) -> str:
        root = tmp_path / name / "dynawo"
        (root / "share" / "xsd").mkdir(parents=True)
        (root / "dynawo.sh").write_text("#!/bin/sh\n")
        if xsd is not None:
            (root / "share" / "xsd" / "jobs.xsd").write_text(xsd)
        return str(root / "dynawo.sh")
    return _install


@pytest.fixture
def jobs_xsd(data_dir) -> str:
    return (data_dir / "xsd" / "jobs.xsd").read_text()


@pytest.fixture
def new_session():
    store = SessionStore()
    sessions = []

    def _new():
        sessions.append(store.create("test"))
        return sessions[-1]
    yield _new
    for s in sessions:
        store.delete(s.session_id)


def test_no_dynawo_means_no_schema_and_no_error(new_session):
    session = new_session()
    assert session.dynawo_executable is None
    assert (session.jobs_schema, session.jobs_schema_error) == (None, None)


def test_the_default_dynawo_schema_is_loaded_at_session_creation(new_session, dynawo_install, jobs_xsd, monkeypatch):
    monkeypatch.setenv("DYNAWO_DEFAULT_EXE", dynawo_install("a", jobs_xsd))
    session = new_session()
    assert session.jobs_schema.find("jobs/job") is not None


def test_changing_the_dynawo_reloads_the_schema(new_session, dynawo_install, jobs_xsd):
    session = new_session()
    session.dynawo_executable = dynawo_install("a", jobs_xsd)
    assert session.jobs_schema.find("jobs/job") is not None

    session.dynawo_executable = dynawo_install("b", _EMPTY_JOBS_XSD)
    assert session.jobs_schema.find("jobs/job") is None

    session.dynawo_executable = None
    assert (session.jobs_schema, session.jobs_schema_error) == (None, None)


def test_a_dynawo_without_xsd_is_still_selected(new_session, dynawo_install):
    session = new_session()
    exe = dynawo_install("a", None)
    session.dynawo_executable = exe
    assert session.dynawo_executable == exe
    assert session.jobs_schema is None
    assert "No jobs.xsd found" in session.jobs_schema_error


def test_setting_a_local_executable_through_the_api_loads_its_schema(client, session_store, dynawo_install, jobs_xsd):
    response = client.post("/api/dynawo/executable", json={"exe": dynawo_install("a", jobs_xsd)})
    assert response.status_code == 200, response.text
    (session,) = session_store._sessions.values()
    assert session.jobs_schema is not None
    assert session.jobs_schema_error is None

    client.post("/api/dynawo/executable", json={"exe": ""})
    assert session.jobs_schema is None
