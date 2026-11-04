#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import pytest

from api.routers.dynamic_association import _manual_parameters
from tests.fake_dynawo import make_fake_dynawo
from tests.unit.backend.association_network import network

API = "/api/dynamic-association"

CATALOGUE = [
    {"name": "Load", "models": [{"name": "LoadAlphaBeta"}, {"name": "LoadPQ"}]},
    {"name": "TapChanger", "models": [{"name": "TapChangerAutomationSystem"}]},
]


def _desc(ddb, lib: str, *parameters: str) -> None:
    body = "".join(f'<parameter name="{p}" valueType="DOUBLE"/>' for p in parameters)
    (ddb / f"{lib}.desc.xml").write_text(
        f'<model xmlns="http://www.rte-france.com/dynawo"><elements><parameters>{body}</parameters></elements></model>')


def _rule(model: str, source: str, set_id: str) -> dict:
    return {"model": model, "par_source": source, "par_file": "user.par" if source == "file" else "",
            "parameter_set_id": set_id}


def test_manual_sets_get_the_parameters_of_their_models(tmp_path):
    ddb = tmp_path / "ddb"
    ddb.mkdir()
    _desc(ddb, "LoadAlphaBeta", "load_alpha", "load_beta")
    _desc(ddb, "LoadPQ", "load_P0Pu", "load_alpha")
    _desc(ddb, "TapChangerAutomaton", "tapChanger_UTarget")    # library of TapChangerAutomationSystem
    rules = [
        _rule("LoadAlphaBeta", "manual", "Loads"),
        _rule("LoadPQ", "manual", "Loads"),                     # same set: the union, each name once
        _rule("TapChangerAutomationSystem", "manual", "TC"),
        _rule("LoadPQ", "file", "FromFile"),                    # a set of a file: not created
    ]

    parameters = _manual_parameters(str(tmp_path / "dynawo.sh"), rules, CATALOGUE)

    assert parameters == {
        "Loads": [("load_alpha", "DOUBLE"), ("load_beta", "DOUBLE"), ("load_P0Pu", "DOUBLE")],
        "TC": [("tapChanger_UTarget", "DOUBLE")],
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@pytest.fixture
def session(client, session_store):
    """The client's session with the test network loaded (see association_network)."""
    client.get("/api/files/")
    session = next(iter(session_store._sessions.values()))
    session.network, session.network_name = network(), "net.iidm"
    info = session.session_manager.add_file("net.iidm", b'<iidm:network xmlns:iidm="http://www.powsybl.org/schema/iidm/1_4"/>')
    session.uploaded_files_info["net.iidm"] = {"size": info.size, "ftype": "iidm"}
    return session


@pytest.fixture
def dynawo(client, tmp_path) -> str:
    """The fake Dynawo 2.0.0 selected: its ddb has LoadAlphaBeta only (of the models used here)."""
    exe = make_fake_dynawo(tmp_path / "dynawo")
    assert client.post("/api/dynawo/executable", json={"exe": exe}).status_code == 200
    return exe


def _load_rule(model: str, *conditions: tuple, **extra) -> dict:
    return {"element_type": "LOAD", "model": model, "par_source": "manual", "parameter_set_id": "S",
            "conditions": [{"attribute": a, "operator": o, "value": v} for a, o, v in conditions], **extra}


def test_element_types_attributes_and_values(session, client):
    assert {"id": "LOAD", "label": "Loads"} in client.get(f"{API}/element-types").json()["element_types"]

    loads = client.get(f"{API}/element-types/LOAD/attributes").json()
    assert (loads["count"], loads["not_connected"]) == (3, {"L2": "not connected"})
    assert client.get(f"{API}/element-types/LOAD/values", params={"attribute": "id", "search": "3"}).json() == \
        {"values": ["L3"], "total": 1}
    assert client.get(f"{API}/element-types/TURBINE/attributes").status_code == 404
    assert client.get(f"{API}/element-types/LOAD/values", params={"attribute": "nope"}).status_code == 404


def test_without_network_the_routes_say_so(client):
    client.get("/api/files/")

    response = client.post(f"{API}/resolve", json={"rules": [_load_rule("LoadAlphaBeta")]})
    assert (response.status_code, response.json()["detail"]) == (404, "No network loaded")


def test_models_are_marked_available_in_the_selected_dynawo(session, client, dynawo):
    response = client.get(f"{API}/models", params={"element_type": "LOAD"}).json()
    available = {m["name"]: m["available"] for m in response["models"]}

    assert response["dynawo_version"] == "2.0.0"
    assert (available["LoadAlphaBeta"], available["LoadPQ"]) == (True, False)


def test_rules_and_options_are_kept_with_the_session(session, client):
    rules = [_load_rule("LoadAlphaBeta", ("p0", ">", 10))]
    client.put(f"{API}/rules", json={"rules": rules})
    client.put(f"{API}/options", json={"stop_time": 20})

    assert client.get(f"{API}/rules").json()["rules"][0]["conditions"] == [{"attribute": "p0", "operator": ">", "value": 10}]
    context = client.get(f"{API}/context").json()
    assert (context["network_file"], context["dynawo"], context["script_available"]) == ("net.iidm", None, False)
    assert context["options"]["stop_time"] == 20


def test_parameter_sources_list_the_sets_of_the_user_files(session, upload, client):
    upload("par/macro_and_sets.par")

    files = client.get(f"{API}/parameter-sources").json()["files"]

    assert files == [{"name": "macro_and_sets.par", "sets": ["GEN1", "GEN2", "LOAD1"]}]


def test_resolve_counts_the_elements_per_model_and_warns_of_unavailable_ones(session, client, dynawo):
    rules = [_load_rule("LoadAlphaBeta"), _load_rule("LoadPQ", ("p0", ">", "10"))]

    resolution = client.post(f"{API}/resolve", json={"rules": rules}).json()

    assert [(m["model"], m["count"]) for m in resolution["models"]] == [("LoadPQ", 2), ("LoadAlphaBeta", 1)]
    assert "model 'LoadPQ' is not available for Dynawo 2.0.0" in resolution["rules"][1]["warnings"]
    assert "assignments" not in resolution


def test_rule_with_a_set_of_a_missing_file_is_an_error(session, client):
    rule = _load_rule("LoadAlphaBeta", par_source="file", par_file="nope.par", parameter_set_id="S")

    report = client.post(f"{API}/resolve", json={"rules": [rule]}).json()["rules"][0]

    assert report["errors"] == ["parameters file 'nope.par' is not in the session"]


def test_rules_are_rebuilt_from_a_dyd_of_the_session(session, upload, client):
    upload("dyd/reconstruct.dyd", "par/macro_and_sets.par")

    rebuilt = client.post(f"{API}/import", json={"dyd_file": "reconstruct.dyd"}).json()

    assert rebuilt["report"]["equipment"] == 5 and rebuilt["report"]["difference_count"] == 0
    assert client.post(f"{API}/import", json={"dyd_file": "macro_and_sets.par"}).status_code == 400


def test_generation_needs_a_dynawo_and_the_script_a_generation(session, client):
    response = client.post(f"{API}/generate", json={"rules": [_load_rule("LoadAlphaBeta")]})

    assert response.status_code == 400 and response.json()["detail"].startswith("No Dynawo selected")
    assert client.get(f"{API}/script").status_code == 404


def test_added_models_need_a_dynawo_and_a_supported_category(session, client):
    assert client.get(f"{API}/custom-models").json() == {"dynawo_version": None, "models": []}
    assert client.get(f"{API}/custom-models/candidates", params={"category": "Line"}).status_code == 400
    assert client.delete(f"{API}/custom-models/MyLoad", params={"shared": False}).status_code == 400


def test_rule_sets_are_saved_replaced_only_on_request_and_deleted(client):
    rules = {"rules": [_load_rule("LoadAlphaBeta")], "options": {"stop_time": 20}}

    assert [s["name"] for s in client.put(f"{API}/rule-sets/base", json=rules).json()["rule_sets"]] == ["base"]
    assert client.put(f"{API}/rule-sets/base", json=rules).status_code == 409
    assert client.put(f"{API}/rule-sets/base", params={"overwrite": True}, json={"rules": []}).status_code == 200
    assert client.get(f"{API}/rule-sets/base").json() == {"name": "base", "rules": [], "options": None}
    assert client.get(f"{API}/rule-sets").json()["rule_sets"][0]["count"] == 0
    assert client.delete(f"{API}/rule-sets/base").json() == {"rule_sets": []}
    assert client.delete(f"{API}/rule-sets/base").status_code == 404
    assert client.get(f"{API}/rule-sets/..%2Fescape").status_code in (400, 404)
    assert client.put(f"{API}/rule-sets/bad.", json=rules).status_code == 400
