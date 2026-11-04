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

from backend.models import RunRecord

FOLDER = "case_dynawo2.0.0"     # where an upgrade puts its job


@pytest.fixture
def run_in_folder(client, session_store, data_dir):
    """A finished run of a job in a folder of the session, with the outputs
    Dynawo writes next to the job: curves and log."""
    client.get("/api/files/")
    session = next(iter(session_store._sessions.values()))
    manager = session.session_manager
    jobs = (data_dir / "jobs/network_and_solver.jobs").read_text().replace(
        '<outputs directory="outputs"/>',
        '<outputs directory="outputs"><logs><appender tag="" file="dynawo.log"/></logs></outputs>')
    info = manager.add_file(f"{FOLDER}/case.jobs", jobs.encode())
    session.uploaded_files_info[info.name] = {"size": info.size, "ftype": info.ftype}
    outputs = os.path.join(manager.working_dir, FOLDER, "outputs_run1")
    os.makedirs(os.path.join(outputs, "curves"))
    os.makedirs(os.path.join(outputs, "logs"))
    with open(os.path.join(outputs, "curves", "curves.csv"), "w") as fh:
        fh.write("time;GEN1_generator_omegaPu;\n0;1;\n1;0.99;\n")
    with open(os.path.join(outputs, "logs", "dynawo.log"), "w") as fh:
        fh.write("simulation done\n")
    session.sim_runs.append(RunRecord(run_id=1, label="Run 1", jobs_file=f"{FOLDER}/case.jobs",
                                      output_dir="outputs_run1", returncode=0, output="ok",
                                      start_time=0, stop_time=1))
    return outputs


def test_outputs_of_a_job_in_a_folder_are_found_next_to_it(run_in_folder, client):
    run = client.get("/api/simulation/runs").json()[0]
    assert (run["has_log"], run["has_timeline"]) == (True, False)

    assert client.get("/api/simulation/1/curves/data").status_code == 200
    assert client.get("/api/simulation/1/log").json() == {"text": "simulation done\n"}


def test_deleting_the_run_removes_its_outputs_next_to_the_job(run_in_folder, client):
    assert client.delete("/api/simulation/1").json() == {"ok": True}

    assert not os.path.exists(run_in_folder)
