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
import re
import zipfile

import pandas as pd
import pytest

from backend.association_rules import element_table
from backend.association_script import build_export
from backend.par_parser import parse_par

NS = 'xmlns="http://www.rte-france.com/dynawo"'
DYNAWO = "/opt/dynawo/dynawo.sh"


class FakeNetwork:
    """The part of a pypowsybl network the export reads: the loads table."""

    def get_loads(self, all_attributes=False):
        return pd.DataFrame({"id": ["L1", "L2", "L3"], "p0": [5.0, 20.0, 30.0]}).set_index("id")


def _par(*sets: str) -> bytes:
    return f'<?xml version="1.0" encoding="UTF-8"?><parametersSet {NS}>{"".join(sets)}</parametersSet>'.encode()


# Session files of a recorded generation: the user's .par files and the
# generated ones holding the sets filled in the GUI.
FILES = {
    "net.iidm":        b"<network/>",
    "loads.par":       _par('<set id="Big"><par type="DOUBLE" name="load_alpha" value="2"/></set>'),
    "solver.par":      _par('<set id="IDAOrder2"><par type="INT" name="order" value="2"/></set>'),
    "gen_models.par":  _par('<set id="Big"/>', '<set id="MyLoad"><par type="DOUBLE" name="load_alpha" value="1"/></set>'),
    "gen_network.par": _par('<set id="Network"><par type="DOUBLE" name="load_Tp" value="90"/></set>'),
}

GENERATION = {
    "rules": [
        {"element_type": "LOAD", "conditions": [], "model": "LoadAlphaBeta",
         "par_source": "manual", "par_file": "", "parameter_set_id": "MyLoad"},
        {"element_type": "LOAD", "conditions": [{"attribute": "p0", "operator": ">", "value": "10"}],
         "model": "LoadAlphaBeta", "par_source": "file", "par_file": "loads.par", "parameter_set_id": "Big"},
    ],
    "set_ids": ["MyLoad", "Big"],
    "model_category": {"LoadAlphaBeta": "Load"},
    "iidm": "net.iidm",
    "models_par": "gen_models.par",
    "network_par": "gen_network.par", "network_id": "Network", "network_source": None,
    "solver_type": "IDA", "solver_par": "gen_solvers.par", "solver_id": "IDAOrder2", "solver_source": "solver.par",
    "start": 0.0, "stop": 10.0,
    "dynawo_version": "1.7.1 (rev:v1.7-1250b93)",
    "additional_models": None,
}


@pytest.fixture
def folder() -> dict[str, str]:
    """The exported zip: {name in the folder: text}."""
    archive = zipfile.ZipFile(io.BytesIO(build_export(GENERATION, FakeNetwork(), FILES.__getitem__, DYNAWO, "1.16.1")))
    return {name.split("/", 1)[1]: archive.read(name).decode() for name in archive.namelist()}


def test_folder_holds_the_script_the_user_files_and_the_sets_filled_in_the_gui(folder):
    assert set(folder) == {"dynamic_association.py", "README.md", "net.iidm", "loads.par", "solver.par",
                           "manual_sets.par"}
    assert folder["loads.par"] == FILES["loads.par"].decode()                      # the original, not a copy
    manual = parse_par(folder["manual_sets.par"].encode())
    assert set(manual) == {"MyLoad", "Network"}                                   # no network file: filled in the GUI


def test_script_compiles_and_runs_the_dynawo_of_the_gui_by_default(folder):
    script = folder["dynamic_association.py"]
    compile(script, "dynamic_association.py", "exec")

    assert f"DYNAWO = {DYNAWO!r}" in script
    # The fake Dynawo answers PowSyBl with the version of the GUI's generation.
    assert "WRITE_VERSION = '1.7.1 (rev:v1.7-1250b93)'" in script
    assert "config.yml" not in folder                                             # written by the script


def test_script_selects_the_elements_of_each_rule_in_order(folder):
    script = folder["dynamic_association.py"]
    source = re.search(r"^def select_models.*?return owner, automation$", script, re.S | re.M).group(0)
    namespace = {"element_table": element_table}
    exec(source, namespace)

    owner, automation = namespace["select_models"](FakeNetwork())

    assert {static_id: spec for (_, static_id), (_, _, spec) in owner.items()} == {
        "L1": ("manual_sets.par", "MyLoad"),
        "L2": ("loads.par", "Big"),                                               # the later rule overrides
        "L3": ("loads.par", "Big"),
    }
    assert automation == {}


def test_readme_gives_the_defaults_and_the_upgrade(folder):
    readme = folder["README.md"]

    assert "Dynawo 1.7.1," in readme                                              # short version
    assert DYNAWO in readme
    assert "`--network-par FILE` and `--network-par-id SET`" in readme
    assert "default: `manual_sets.par`,\n  set `Network`" in readme
    assert "`--update-tool`" in readme and "`--write-only`" in readme
