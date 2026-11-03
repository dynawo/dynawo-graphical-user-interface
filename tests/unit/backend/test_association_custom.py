#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend import association_custom
from backend.association_custom import (
    _model_variables,
    additional_models_file,
    candidates,
    ddb_libraries,
    declares,
    load_shared,
    next_patch,
    numeric_version,
    save_shared,
    with_custom_models,
)
from tests.fake_dynawo import make_fake_dynawo
from tests.unit.backend.association_network import CATALOGUE

# What PowSyBl writes for a probed load: a macro connection to the network and
# static references, through a macroStaticReference and directly.
PROBE_DYD = b'''<dyn:dynamicModelsArchitecture xmlns:dyn="http://www.rte-france.com/dynawo">
  <dyn:blackBoxModel id="L1" lib="LoadAlphaBeta" parFile="m.par" parId="P" staticId="L1">
    <dyn:macroStaticRef id="MSR_Load"/>
    <dyn:staticRef var="load_QRefPu" staticVar="q"/>
  </dyn:blackBoxModel>
  <dyn:blackBoxModel id="G1" lib="GeneratorPQ" parFile="m.par" parId="P" staticId="G1"/>
  <dyn:macroConnector id="MC_Load">
    <dyn:connect var1="load_terminal" var2="@STATIC_ID@@NODE@_ACPIN"/>
  </dyn:macroConnector>
  <dyn:macroStaticReference id="MSR_Load"><dyn:staticRef var="load_PPu" staticVar="p"/></dyn:macroStaticReference>
  <dyn:macroConnect connector="MC_Load" id1="L1" id2="NETWORK"/>
  <dyn:macroConnect connector="MC_Load" id1="G1" id2="NETWORK"/>
</dyn:dynamicModelsArchitecture>'''


def test_versions_for_powsybl():
    assert numeric_version("1.7.1 (rev:v1.7-1250b93)") == "1.7.1"
    assert numeric_version("master") == "master"
    assert next_patch("1.7.9") == "1.7.10"


def test_variables_of_a_probed_model_are_those_powsybl_uses():
    assert _model_variables(PROBE_DYD, "L1") == {"load_terminal", "load_PPu", "load_QRefPu"}
    assert _model_variables(PROBE_DYD, "NOPE") == set()


def test_ddb_libraries_are_those_described(tmp_path):
    assert ddb_libraries(make_fake_dynawo(tmp_path / "dynawo")) == ["DYNModelOmegaRef", "LoadAlphaBeta"]


def test_variables_count_as_declared_when_dynawo_expands_them():
    assert declares(["load_terminal_V_re", "load_PPu_value"], ["load_terminal", "load_PPu"])
    assert not declares(["load_terminal"], ["load_PPu"])


def test_candidates_fit_the_category_and_no_larger_one():
    interfaces = {
        "SimplifiedGenerator": {"variables": ["generator_terminal"], "parameters": []},
        "SignalNGenerator":    {"variables": ["generator_terminal", "generator_N"], "parameters": []},
        "Broken":              {"error": "no element to probe"},
    }
    libraries = {
        "GeneratorPV":        (["generator_terminal_V_re"], []),
        "GeneratorPVSignalN": (["generator_terminal_V_re", "generator_N_value"], []),
        "LoadAlphaBeta":      (["load_terminal"], []),
    }

    assert candidates("SimplifiedGenerator", interfaces, libraries) == ["GeneratorPV"]
    assert candidates("SignalNGenerator", interfaces, libraries) == ["GeneratorPVSignalN"]
    assert candidates("Broken", interfaces, libraries) == []


def test_parameters_a_category_needs_must_be_declared():
    interfaces = {"Load": {"variables": ["load_terminal"], "parameters": ["load_alpha"]}}

    assert candidates("Load", interfaces, {"A": (["load_terminal"], ["load_alpha"]), "B": (["load_terminal"], [])}) == ["A"]


def test_added_models_join_their_category():
    models = [{"lib": "MyLoad", "category": "Load", "doc": "mine", "shared": True},
              {"lib": "AnotherLoad", "category": "Load"}]

    catalogue = {c["name"]: c for c in with_custom_models(CATALOGUE, models)}

    assert [(m["name"], m.get("custom")) for m in catalogue["Load"]["models"]] == [
        ("LoadAlphaBeta", None), ("LoadPQ", None), ("AnotherLoad", "live"), ("MyLoad", "shared")]
    assert catalogue["Line"] == next(c for c in CATALOGUE if c["name"] == "Line")


def test_additional_models_file_limits_each_model_to_its_dynawo_version():
    content = additional_models_file([{"lib": "MyLoad", "internal": "baseLoads", "dynawo_version": "1.7.1"}])

    assert content == {"baseLoads": {"libs": [{"lib": "MyLoad", "doc": "", "minVersion": "1.7.1", "maxVersion": "1.7.2",
                                               "endCause": "Added for Dynawo 1.7.1 only"}]}}


def test_shared_models_are_saved_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(association_custom, "SHARED_PATH", str(tmp_path / "config" / "models.json"))
    assert load_shared() == []

    save_shared([{"lib": "MyLoad", "category": "Load"}])

    assert load_shared() == [{"lib": "MyLoad", "category": "Load"}]
