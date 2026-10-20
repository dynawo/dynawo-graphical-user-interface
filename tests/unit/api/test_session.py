#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from fastapi.testclient import TestClient

from api.main import app


def test_health(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_first_request_opens_a_session(client, session_store):
    client.get("/api/files/")

    session_id = client.cookies.get("session_id")
    assert session_id
    assert session_store.get(session_id) is not None


def test_requests_of_one_browser_share_their_session(client, upload):
    upload("par/macro_and_sets.par")
    first = client.cookies.get("session_id")

    names = [f["name"] for f in client.get("/api/files/").json()]

    assert names == ["macro_and_sets.par"]
    assert client.cookies.get("session_id") == first


def test_two_users_do_not_see_each_others_files(client, upload, session_store):
    upload("par/macro_and_sets.par")
    other_user = TestClient(app)

    assert other_user.get("/api/files/").json() == []
    assert other_user.cookies.get("session_id") != client.cookies.get("session_id")


def test_unknown_session_cookie_gets_a_new_session(session_store):
    browser = TestClient(app, cookies={"session_id": "expired-or-forged"})

    new_id = browser.get("/api/files/").cookies.get("session_id")

    assert new_id not in (None, "expired-or-forged")
    assert session_store.get(new_id) is not None
