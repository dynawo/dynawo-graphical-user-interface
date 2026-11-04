#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import os

import pytest

from backend.association_generate import (
    adapt_dyd,
    adapt_jobs,
    build_model_mapping,
    dropped_assignments,
    read_dynawo_version,
    session_names,
    solver_type,
)
from backend.jobs_parser import read_network_iidm

NS = 'xmlns="http://www.rte-france.com/dynawo"'

# What PowSyBl writes, reduced to what the session adapts.
JOBS = f'''<?xml version="1.0" encoding="UTF-8"?>
<jobs {NS}><job name="powsybl">
  <solver lib="dynawo_SolverIDA" parFile="solvers.par" parId="IDA"/>
  <modeler compileDir="outputs/compilation">
    <network iidmFile="powsybl_dynawo.xiidm" parFile="network.par" parId="Network"/>
    <dynModels dydFile="powsybl_dynawo.dyd"/>
  </modeler>
  <simulation startTime="0" stopTime="10"/>
  <outputs directory="outputs"><logs><appender tag="" file="dynawo.log"/></logs></outputs>
</job></jobs>'''.encode()
REFERENCE = f'''<jobs {NS}><job name="ref"><outputs directory="results">
  <timeline exportMode="XML"/><curves inputFile="ref.crv" exportMode="CSV"/>
</outputs></job></jobs>'''.encode()
DYD = f'''<dynamicModelsArchitecture {NS}>
  <blackBoxModel id="L1" lib="LoadAlphaBeta" parFile="models.par" parId="Load" staticId="L1"/>
  <blackBoxModel id="TC_L1" lib="TapChangerAutomaton" parFile="models.par" parId="TC"/>
</dynamicModelsArchitecture>'''.encode()

NAMES = session_names(["powsybl_dynawo.jobs", "powsybl_dynawo.dyd", "models.par", "network.par", "solvers.par"], set())


def test_powsybl_files_get_session_names_free_in_the_session():
    assert NAMES["models.par"] == "dynamic_association_models.par"
    names = session_names(["powsybl_dynawo.dyd", "extra.par"], {"dynamic_association.dyd"})
    assert names == {"powsybl_dynawo.dyd": "dynamic_association_2.dyd", "extra.par": "dynamic_association_extra.par"}


def test_jobs_points_to_the_session_files_with_the_outputs_of_the_reference_job():
    jobs = adapt_jobs(JOBS, NAMES, "case.iidm", REFERENCE)

    text = jobs.decode()
    assert 'parFile="dynamic_association_solvers.par"' in text and 'dydFile="dynamic_association.dyd"' in text
    assert 'directory="results"' in text and "timeline" in text
    assert "curves" not in text                                               # the reference's .crv is not ours


def test_jobs_without_reference_keeps_its_outputs(tmp_path):
    path = tmp_path / "x.jobs"
    path.write_bytes(adapt_jobs(JOBS, NAMES, "case.iidm", None))

    assert read_network_iidm(str(path)) == "case.iidm"
    assert 'directory="outputs"' in path.read_text()


def test_dyd_points_to_the_session_par_files():
    assert adapt_dyd(DYD, NAMES).decode().count('parFile="dynamic_association_models.par"') == 2


def test_dropped_assignments_get_the_reason_from_the_report():
    assignments = [
        {"static_id": "L1", "model": "LoadAlphaBeta"},                         # written
        {"static_id": "L2", "model": "LoadAlphaBeta"},
        {"static_id": "G1", "model": "NoSuchModel"},
        {"static_id": "L1", "model": "TapChangerAutomationSystem", "automation": True, "dynamic_model_id": "TC_L1"},
        {"static_id": "L3", "model": "TapChangerAutomationSystem", "automation": True, "dynamic_model_id": "TC_L3"},
        {"static_id": "L4", "model": "TapChangerAutomationSystem", "automation": True, "dynamic_model_id": "TC_L4"},
    ]
    report = """
      + Model TC_L3 instantiation KO
         'L3' is not a load with two transformers
      Model NoSuchModel not found
      Equipment 'L2' is not connected: skipped
    """

    dropped = {a["static_id"]: a["reason"] for a in dropped_assignments(assignments, DYD, report)}

    assert dropped == {
        "L2": "Equipment 'L2' is not connected: skipped",
        "G1": "Model NoSuchModel not found",
        "L3": "Model TC_L3 instantiation KO: 'L3' is not a load with two transformers",
        "L4": "left out by PowSyBl (no reason in its report)",
    }


def test_model_mapping_takes_equipment_and_automation_systems():
    # PowSyBl checks the categories and columns: this fails on a malformed mapping.
    build_model_mapping([
        {"category": "Load", "static_id": "L1", "parameter_set_id": "Load", "model": "LoadAlphaBeta"},
        {"category": "TapChanger", "automation": True, "dynamic_model_id": "TC_L1", "parameter_set_id": "TC",
         "model": "TapChangerAutomationSystem", "fields": {"static_id": "L1", "side": "HIGH_VOLTAGE"}},
    ])


def test_solver_types_powsybl_knows():
    assert (solver_type("dynawo_SolverIDA"), solver_type("dynawo_SolverSIM"), solver_type("dynawo_SolverTRAP")) == \
        ("IDA", "SIM", None)


def _executable(path, script: str) -> str:
    path.write_text("#!/bin/sh\n" + script)
    os.chmod(path, 0o755)
    return str(path)


def test_dynawo_version_is_the_first_line_and_read_once(tmp_path):
    exe = _executable(tmp_path / "dynawo.sh", 'echo "1.7.1 (rev:abc)"; echo more; touch "$(dirname "$0")/ran"\n')

    assert read_dynawo_version(exe) == "1.7.1 (rev:abc)"
    os.remove(tmp_path / "ran")
    assert read_dynawo_version(exe) == "1.7.1 (rev:abc)"
    assert not (tmp_path / "ran").exists()                                    # cached


def test_dynawo_version_errors(tmp_path):
    with pytest.raises(RuntimeError, match="gave no version"):
        read_dynawo_version(_executable(tmp_path / "silent.sh", "exit 1\n"))
    with pytest.raises(RuntimeError, match="could not run"):
        read_dynawo_version(str(tmp_path / "missing.sh"))
