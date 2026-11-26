#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Tests of the /api/jobs endpoints."""

import shutil

import pytest


@pytest.fixture
def dynawo(client, tmp_path, data_dir):
    """Select a minimal Dynawo whose jobs.xsd is the test schema."""
    root = tmp_path / "dynawo"
    (root / "share" / "xsd").mkdir(parents=True)
    (root / "dynawo.sh").write_text("#!/bin/sh\n")
    shutil.copy(data_dir / "xsd" / "jobs.xsd", root / "share" / "xsd" / "jobs.xsd")
    response = client.post("/api/dynawo/executable", json={"exe": str(root / "dynawo.sh")})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def case(upload, dynawo):
    upload("jobs/valid.jobs", "par/network_and_solver.par")


def _get(client, name="valid.jobs") -> dict:
    response = client.get(f"/api/jobs/file/{name}")
    assert response.status_code == 200, response.text
    return response.json()


def _simulation(document: dict) -> dict:
    return next(c for c in document["root"]["children"][0]["children"] if c.get("tag") == "simulation")


# ── Dynawo selection ─────────────────────────────────────────────────────────

def test_selecting_a_dynawo_reports_its_jobs_xsd(dynawo):
    assert dynawo["jobs_xsd_error"] is None


def test_selecting_a_dynawo_without_xsd_reports_why(client, tmp_path):
    (tmp_path / "dynawo.sh").write_text("#!/bin/sh\n")
    body = client.post("/api/dynawo/executable", json={"exe": str(tmp_path / "dynawo.sh")}).json()
    assert "No jobs.xsd found" in body["jobs_xsd_error"]


def test_without_dynawo_the_endpoints_refuse(client):
    response = client.get("/api/jobs/schema")
    assert response.status_code == 400
    assert "No Dynawo selected" in response.json()["detail"]


def test_without_xsd_the_endpoints_give_the_reason(client, tmp_path):
    (tmp_path / "dynawo.sh").write_text("#!/bin/sh\n")
    client.post("/api/dynawo/executable", json={"exe": str(tmp_path / "dynawo.sh")})
    response = client.get("/api/jobs/schema")
    assert response.status_code == 400
    assert "No jobs.xsd found" in response.json()["detail"]


# ── Schema and reading ───────────────────────────────────────────────────────

def test_schema(client, dynawo):
    body = client.get("/api/jobs/schema").json()
    assert body["root"]["name"] == "jobs"
    assert body["xsd"].endswith("share/xsd/jobs.xsd")
    assert {"element": "dynModels", "attribute": "dydFile", "ftype": "dyd"} in body["file_refs"]


def test_get_a_jobs_file(client, case):
    body = _get(client)
    assert body["document"]["root"]["tag"] == "jobs"
    assert body["token"]


def test_a_file_that_is_not_a_jobs_file_is_refused(client, case):
    assert client.get("/api/jobs/file/network_and_solver.par").status_code == 404
    assert client.get("/api/jobs/file/missing.jobs").status_code == 404


# ── Check, render, parse ─────────────────────────────────────────────────────

def test_check_gives_errors_and_warnings(client, case):
    document = _get(client)["document"]
    _simulation(document)["attrs"]["startTime"] = "abc"
    body = client.post("/api/jobs/check", json={"document": document, "jobs_file": "valid.jobs"}).json()
    levels = {(i["level"], i.get("attribute")) for i in body["issues"]}
    assert ("error", None) in levels  # startTime is not a float
    assert ("warning", "iidmFile") in levels  # network.iidm was not uploaded


def test_render_and_parse_go_both_ways(client, case):
    document = _get(client)["document"]
    text = client.post("/api/jobs/render", json={"document": document}).json()["text"]
    assert text.startswith("<?xml")
    body = client.post("/api/jobs/parse", json={"text": text}).json()
    assert body["document"] == document


def test_parse_gives_the_line_of_a_syntax_error(client, dynawo):
    response = client.post("/api/jobs/parse", json={"text": "<jobs>\n<job>\n</jobs>"})
    assert response.status_code == 422
    assert response.json()["detail"]["line"] == 3


# ── Saving ───────────────────────────────────────────────────────────────────

def test_save_writes_the_file_and_gives_a_new_token(client, case):
    body = _get(client)
    _simulation(body["document"])["attrs"]["stopTime"] = "30"
    saved = client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body})
    assert saved.status_code == 200, saved.text
    assert saved.json()["token"] != body["token"]
    assert _simulation(_get(client)["document"])["attrs"]["stopTime"] == "30"


def test_save_after_another_change_is_refused(client, case, session_store):
    body = _get(client)
    (session,) = session_store._sessions.values()
    with open(session.session_manager.get_path("valid.jobs"), "a") as fh:
        fh.write("<!-- changed -->")
    response = client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body})
    assert response.status_code == 409


def test_save_refuses_a_root_other_than_jobs(client, case):
    body = _get(client)
    body["document"]["root"]["tag"] = "job"
    response = client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body})
    assert response.status_code == 422


def test_save_refreshes_the_session_record(client, case, session_store):
    body = _get(client)
    _simulation(body["document"])["attrs"]["stopTime"] = "30"
    client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body})
    (session,) = session_store._sessions.values()
    assert b'stopTime="30"' in session.session_manager.get_raw("valid.jobs")


# ── Creating ─────────────────────────────────────────────────────────────────

def test_create_a_jobs_file(client, case):
    document = _get(client)["document"]
    response = client.post("/api/jobs/file", json={"jobs_file": "sub/new.jobs", "document": document})
    assert response.status_code == 200, response.text
    files = {f["name"]: f["ftype"] for f in client.get("/api/files/").json()}
    assert files["sub/new.jobs"] == "jobs"


@pytest.mark.parametrize("name, status", [
    ("valid.jobs", 409),
    ("new.xml", 400),
    ("../outside.jobs", 400),
])
def test_create_refuses_a_bad_name(client, case, name, status):
    document = _get(client)["document"]
    assert client.post("/api/jobs/file", json={"jobs_file": name, "document": document}).status_code == status
