#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from backend.jobs_parser import job_relative


def test_paths_of_a_job_at_the_root_are_unchanged():
    assert job_relative("IEEE57.jobs", "outputs_run1") == "outputs_run1"


def test_paths_of_a_job_in_a_folder_are_in_that_folder():
    # An upgraded job: Dynawo writes its outputs next to it.
    assert job_relative("IEEE57_dynawo2.0.0/IEEE57.jobs", "outputs_run1") == "IEEE57_dynawo2.0.0/outputs_run1"
    assert job_relative("IEEE57_dynawo2.0.0/IEEE57.jobs", "../IEEE57.iidm") == "IEEE57.iidm"
