#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

JOBS = "network_and_solver.jobs"


def test_solver_set_of_the_chosen_job_is_edited(upload, client):
    upload("jobs/network_and_solver.jobs", "par/network_and_solver.par")
    assert client.get("/api/solver/jobs").json() == [JOBS]

    solver = client.get("/api/solver/parameters", params={"jobs_file": JOBS}).json()
    assert (solver["jobs_file"], solver["lib"], solver["parFile"], solver["parId"]) == \
        (JOBS, "dynawo_SolverIDA", "network_and_solver.par", "IDA")
    assert {p["name"]: p["value"] for p in solver["pars"]} == {"order": "2", "absAccuracy": ""}

    changed = client.put("/api/solver/parameters", params={"jobs_file": JOBS}, json={"values": {"absAccuracy": "1e-6"}})
    assert changed.json() == {"changed": 1}
    pars = client.get("/api/solver/parameters", params={"jobs_file": JOBS}).json()["pars"]
    assert {p["name"]: p["value"] for p in pars}["absAccuracy"] == "1e-6"
    assert client.get("/api/solver/is-modified", params={"jobs_file": JOBS}).json() == {"modified": True}


def test_unknown_job_is_404(upload, client):
    upload("jobs/network_and_solver.jobs", "par/network_and_solver.par")

    assert client.get("/api/solver/parameters", params={"jobs_file": "nope.jobs"}).status_code == 404
