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

PAR = "macro_and_sets.par"


@pytest.fixture
def case(upload):
    """A session holding the .dyd and the .par its models use."""
    upload("dyd/macro_and_sets.dyd", "par/macro_and_sets.par")


def _value(client, model_id: str, name: str) -> str:
    pars = client.get(f"/api/parameters/model/{model_id}").json()["pars"]
    return next(p["value"] for p in pars if p["name"] == name)


def test_every_model_is_listed_even_without_static_id(case, client):
    models = {m["dyn_id"]: m for m in client.get("/api/parameters/models").json()}

    assert set(models) == {"GEN1", "GEN1_BIS", "GEN2", "LOAD1", "OMEGA_REF"}
    assert models["OMEGA_REF"]["static_id"] == ""


def test_model_parameters_show_who_else_an_edit_affects(case, client):
    gen1 = client.get("/api/parameters/model/GEN1").json()

    assert gen1["siblings"] == ["GEN1_BIS"]                  # same <set>
    assert gen1["macro_id"] == "MACRO_GEN"
    assert gen1["macro_siblings"] == ["GEN2"]                # same macro only
    pars = {p["name"]: p for p in gen1["pars"]}
    assert pars["generator_SNom"]["value"] == "2000"
    assert pars["generator_H"]["shared"] is True


def test_model_whose_set_is_missing_has_no_parameters(case, client):
    omega = client.get("/api/parameters/model/OMEGA_REF").json()

    assert omega["pars"] == [] and omega["refs"] == []


def test_unknown_model_is_404(case, client):
    assert client.get("/api/parameters/model/NOPE").status_code == 404
    assert client.put("/api/parameters/model/NOPE", json={"values": {}}).status_code == 404


def test_edit_is_written_and_logged(case, client):
    response = client.put("/api/parameters/model/GEN1", json={"values": {"generator_SNom": "2500"}})

    assert response.json() == {"changed": 1}
    assert _value(client, "GEN1", "generator_SNom") == "2500"
    assert _value(client, "GEN1_BIS", "generator_SNom") == "2500"  # same <set>
    log = client.get("/api/parameters/changelog").json()
    assert [(e["dyn_id"], e["changes"]) for e in log] == [
        ("GEN1", [{"name": "generator_SNom", "old_value": "2000", "new_value": "2500"}]),
    ]
    assert client.get("/api/parameters/modified-files").json() == [PAR]


def test_edit_to_the_same_value_is_not_logged(case, client):
    response = client.put("/api/parameters/model/GEN1", json={"values": {"generator_SNom": "2000"}})

    assert response.json() == {"changed": 0}
    assert client.get("/api/parameters/changelog").json() == []


def test_revert_puts_the_old_value_back(case, client):
    client.put("/api/parameters/model/GEN1", json={"values": {"generator_SNom": "2500"}})
    timestamp = client.get("/api/parameters/changelog").json()[0]["timestamp"]

    response = client.post(f"/api/parameters/changelog/GEN1/revert/{timestamp}")

    assert response.json() == {"ok": True, "warned": False}
    assert _value(client, "GEN1", "generator_SNom") == "2000"
    assert client.get("/api/parameters/changelog").json() == []


def test_revert_unknown_entry_is_404(case, client):
    assert client.post("/api/parameters/changelog/GEN1/revert/2000-01-01T00:00:00").status_code == 404


def test_restore_gives_back_the_uploaded_file(case, client, data_dir):
    client.put("/api/parameters/model/GEN2", json={"values": {"generator_H": "6"}})

    assert client.post(f"/api/parameters/restore/{PAR}").json() == {"ok": True}

    assert _value(client, "GEN2", "generator_H") == "5.4"
    assert client.get("/api/parameters/modified-files").json() == []
    assert client.get("/api/parameters/changelog").json() == []
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/files/download").content))
    assert archive.read(PAR) == (data_dir / "par" / PAR).read_bytes()  # byte for byte
