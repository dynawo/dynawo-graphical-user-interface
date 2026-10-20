#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import io
import zipfile

import pytest


def _names(client) -> list[str]:
    return [f["name"] for f in client.get("/api/files/").json()]


def _digest(client) -> str:
    return client.get("/api/files/state").json()["digest"]


def test_upload_detects_each_file_type(upload, client):
    uploaded = upload("par/macro_and_sets.par", "dyd/macro_and_sets.dyd", "crv/two_curves.expected.crv")

    assert {f["name"]: f["ftype"] for f in uploaded} == {
        "macro_and_sets.par": "par",
        "macro_and_sets.dyd": "dyd",
        "two_curves.expected.crv": "crv",
    }
    assert client.get("/api/files/").json() == uploaded


def test_uploading_a_name_already_there_keeps_the_first_file(upload, client):
    upload("par/macro_and_sets.par")

    response = client.post("/api/files/upload", files=[("files", ("macro_and_sets.par", b"<other/>"))])

    assert response.json() == []
    assert _names(client) == ["macro_and_sets.par"]


def test_file_of_a_dropped_folder_keeps_its_relative_path(client):
    response = client.post("/api/files/upload", files=[("files", ("case/sub/x.par", b"<x/>"))])

    assert response.status_code == 200
    assert _names(client) == ["case/sub/x.par"]


@pytest.mark.parametrize("name", ["../evil.par", "/etc/evil.par", "a//b.par", "./x.par", "a/../../x.par"])
def test_name_escaping_the_session_folder_is_refused(client, name):
    response = client.post("/api/files/upload", files=[("files", (name, b"<x/>"))])

    assert response.status_code == 400
    assert _names(client) == []


def test_delete(upload, client):
    upload("par/macro_and_sets.par", "dyd/macro_and_sets.dyd")

    assert client.delete("/api/files/macro_and_sets.par").json() == {"ok": True}
    assert _names(client) == ["macro_and_sets.dyd"]


def test_delete_unknown_file_is_404(client):
    assert client.delete("/api/files/nothing.par").status_code == 404


def test_replace(upload, client):
    upload("par/macro_and_sets.par")

    response = client.put("/api/files/macro_and_sets.par", files={"file": ("macro_and_sets.par", b"<x/>")})

    assert response.json()["size"] == 4
    assert client.get("/api/files/").json()[0]["size"] == 4


def test_replace_unknown_file_is_404(client):
    response = client.put("/api/files/nothing.par", files={"file": ("nothing.par", b"<x/>")})

    assert response.status_code == 404


def test_state_digest_moves_only_when_the_files_change(upload, client):
    empty = _digest(client)
    upload("par/macro_and_sets.par")
    one_file = _digest(client)
    assert one_file != empty
    assert _digest(client) == one_file                       # nothing changed

    client.put("/api/files/macro_and_sets.par", files={"file": ("macro_and_sets.par", b"<x/>")})
    assert _digest(client) != one_file

    client.delete("/api/files/macro_and_sets.par")
    assert _digest(client) == empty


def test_download_zips_the_session_files(upload, client, data_dir):
    upload("par/macro_and_sets.par", "dyd/macro_and_sets.dyd")

    response = client.get("/api/files/download")

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert sorted(archive.namelist()) == ["macro_and_sets.dyd", "macro_and_sets.par"]
    assert archive.read("macro_and_sets.par") == (data_dir / "par/macro_and_sets.par").read_bytes()


def test_uploaded_timeline_is_parsed(upload, client):
    upload("outputs/timeLine/timeline.xml")

    events = client.get("/api/files/timeline.xml/timeline").json()

    assert [e["model_name"] for e in events] == ["EV_LINE", "GEN1"]


def test_timeline_of_unknown_file_is_404(client):
    assert client.get("/api/files/nothing.xml/timeline").status_code == 404
