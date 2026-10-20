#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Fixtures of the API tests: an app talking to a session store of its own."""

import pytest
from fastapi.testclient import TestClient

import api.dependencies
from api.main import app
from api.session_store import SessionStore


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    """Keep the API away from the developer's own settings.

    Some routes save preferences to ~/.config/dynawo_ihm/config.json: a test
    must never overwrite the real one, nor depend on what it holds (the Dynawo
    executable it names, for one)."""
    monkeypatch.setattr("backend.user_config._CONFIG_PATH", str(tmp_path / "user_config.json"))
    monkeypatch.delenv("DYNAWO_DEFAULT_EXE", raising=False)


@pytest.fixture
def session_store(monkeypatch):
    """A fresh session store, so no session leaks from one test to the next.

    Each session owns a temporary directory: deleting the sessions at the end
    removes those directories too."""
    test_store = SessionStore()
    monkeypatch.setattr(api.dependencies, "store", test_store)
    yield test_store
    for session_id in list(test_store._sessions):
        test_store.delete(session_id)


@pytest.fixture
def client(session_store) -> TestClient:
    """An HTTP client for the app. It keeps its cookie, so all its requests
    share one session — like one browser tab. Not used as a context manager:
    that would run the app's shutdown hook, which stops the pypowsybl pool
    for every later test."""
    return TestClient(app)


@pytest.fixture
def upload(client, data_dir):
    """Upload files of tests/data into the client's session, under their base name."""
    def _upload(*relatives: str):
        files = [("files", ((data_dir / r).name, (data_dir / r).read_bytes())) for r in relatives]
        response = client.post("/api/files/upload", files=files)
        assert response.status_code == 200, response.text
        return response.json()
    return _upload
