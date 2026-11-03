#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import shutil

import pytest

from backend import compatibility
from tests.fake_dynawo import make_fake_dynawo

JOB = {"jobs_file": "case.jobs"}


@pytest.fixture
def dynawo(tmp_path, monkeypatch) -> str:
    """The fake Dynawo 2.0.0, its update tool the only one found."""
    monkeypatch.delenv("DYNAWO_HOME", raising=False)
    monkeypatch.setattr(compatibility, "APP_UPDATE_TOOL", str(tmp_path / "app" / "updateXML" / "update.py"))
    compatibility._desc_cache.clear()
    return make_fake_dynawo(tmp_path / "dynawo")


@pytest.fixture
def case(upload, client, dynawo):
    upload("compatibility/case.jobs", "compatibility/case.dyd", "compatibility/case.par")
    assert client.post("/api/dynawo/executable", json={"exe": dynawo}).status_code == 200


def test_context_names_the_version_and_the_update_tool(case, client, tmp_path):
    context = client.get("/api/compatibility/context", params=JOB).json()

    assert context == {"dynawo_version": "2.0.0", "update_tool": True, "update_tool_missing": None,
                       "update_tool_path": str(tmp_path / "dynawo" / "sbin" / "updateXML"),
                       "update_tool_latest": "2.0.0.1"}


def test_check_gives_the_issues_and_what_an_upgrade_would_change(case, client):
    result = client.post("/api/compatibility/check", json=JOB).json()

    assert result["dynawo_version"] == "2.0.0" and result["versions"] == "checked"
    assert result["migration"] == {"changed": ["case.dyd"], "tickets": ["4223"]}
    assert any(i["message"] == "library 'NoSuchLib' not in this Dynawo's ddb" for i in result["issues"])


def test_upgrade_writes_a_folder_beside_the_originals(case, client):
    result = client.post("/api/compatibility/upgrade", json=JOB).json()

    assert result["folder"] == "case_dynawo2.0.0" and result["jobs_file"] == "case_dynawo2.0.0/case.jobs"
    assert result["files"] == ["case_dynawo2.0.0/case.dyd", "case_dynawo2.0.0/case.jobs", "case_dynawo2.0.0/case.par"]
    assert result["tickets"] == ["4223"]
    names = {f["name"] for f in client.get("/api/files/").json()}
    assert {"case.jobs", "case_dynawo2.0.0/case.jobs"} <= names               # the originals are kept

    again = client.post("/api/compatibility/upgrade", json=JOB).json()       # replaces the folder
    assert again["files"] == result["files"]


def test_without_update_tool_the_check_says_so_and_upgrade_is_refused(case, client, tmp_path):
    shutil.rmtree(tmp_path / "dynawo" / "sbin")

    context = client.get("/api/compatibility/context", params=JOB).json()
    assert context["update_tool"] is False
    assert context["update_tool_missing"].startswith("Dynawo's update tool (updateXML/update.py) was not found in:")
    assert client.post("/api/compatibility/check", json=JOB).json()["versions"] == "no_tool"
    assert client.post("/api/compatibility/upgrade", json=JOB).status_code == 400


def test_without_dynawo_or_job_the_request_is_refused(upload, client):
    upload("compatibility/case.jobs")

    assert client.post("/api/compatibility/check", json=JOB).status_code == 400
    assert client.post("/api/compatibility/check", json={"jobs_file": "case.dyd"}).status_code in (400, 404)
