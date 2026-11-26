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


def test_schema_includes_the_hints(client, dynawo):
    hints = client.get("/api/jobs/schema").json()["hints"]
    assert hints["attributes"]["solver@lib"]["default"] == "dynawo_SolverIDA"


def test_new_element(client, dynawo):
    node = client.get("/api/jobs/new", params={"path": "jobs/job/outputs/timeline"}).json()["node"]
    assert node["tag"] == "timeline"
    assert client.get("/api/jobs/new", params={"path": "jobs/nothing"}).status_code == 404


def test_a_new_jobs_file_can_be_created_from_a_new_root(client, dynawo):
    root = client.get("/api/jobs/new", params={"path": "jobs"}).json()["node"]
    response = client.post("/api/jobs/file", json={"jobs_file": "new.jobs", "document": {"root": root}})
    assert response.status_code == 200, response.text
    assert _get(client, "new.jobs")["document"]["root"]["children"][0]["attrs"]["name"] == "job"


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
    assert ("error", "startTime") in levels  # not a float
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


def test_save_refreshes_the_size_and_keeps_the_original(client, case, session_store):
    body = _get(client)
    assert body["modified"] is False
    _simulation(body["document"])["attrs"]["stopTime"] = "30000"
    saved = client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body}).json()
    assert saved["modified"] is True
    (session,) = session_store._sessions.values()
    path = session.session_manager.get_path("valid.jobs")
    assert session.uploaded_files_info["valid.jobs"]["size"] == len(open(path, "rb").read())
    assert b'stopTime="10"' in session.session_manager.get_raw("valid.jobs")


def test_restore_gives_back_the_original(client, case):
    body = _get(client)
    original = _simulation(body["document"])["attrs"]["stopTime"]
    for stop in ("30", "40"):  # saved twice: restore goes back past both
        body = _get(client)
        _simulation(body["document"])["attrs"]["stopTime"] = stop
        client.put("/api/jobs/file", json={"jobs_file": "valid.jobs", **body})
    assert client.post("/api/jobs/restore/valid.jobs").status_code == 200
    restored = _get(client)
    assert _simulation(restored["document"])["attrs"]["stopTime"] == original
    assert restored["modified"] is False


def test_a_created_file_restores_to_its_created_content(client, case):
    document = _get(client)["document"]
    client.post("/api/jobs/file", json={"jobs_file": "new.jobs", "document": document})
    body = _get(client, "new.jobs")
    assert body["modified"] is False
    _simulation(body["document"])["attrs"]["stopTime"] = "99"
    client.put("/api/jobs/file", json={"jobs_file": "new.jobs", **body})
    client.post("/api/jobs/restore/new.jobs")
    assert _simulation(_get(client, "new.jobs")["document"])["attrs"]["stopTime"] == "10"


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


def test_par_sets(client, case):
    assert client.get("/api/jobs/par-sets/network_and_solver.par").json() == ["Network", "IDA"]
    assert client.get("/api/jobs/par-sets/valid.jobs").status_code == 404
