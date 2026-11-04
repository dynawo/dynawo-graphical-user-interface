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
import shutil
import sys

import pytest

from backend import compatibility
from backend.compatibility import (
    FULL_UPGRADE_ORIGIN,
    check_job,
    find_update_tool,
    latest_script,
    migration_changes,
    relink,
    run_update,
    scan_par_sets,
    tool_versions,
    update_tool_place,
    version_tuple,
)
from tests.fake_dynawo import make_fake_dynawo


@pytest.fixture(autouse=True)
def clear_desc_cache():
    compatibility._desc_cache.clear()


@pytest.fixture
def case(tmp_path, data_dir) -> str:
    """A session directory holding the job of tests/data/compatibility."""
    session = tmp_path / "session"
    shutil.copytree(data_dir / "compatibility", session)
    return str(session)


def _issues(result: dict) -> dict[tuple[str, str], list[str]]:
    return {(i["level"], i["message"]): i["examples"] for i in result["issues"]}


def test_job_is_checked_against_the_ddb_of_the_dynawo(case, tmp_path):
    exe = make_fake_dynawo(tmp_path / "dynawo")

    result = check_job(case, "case.jobs", exe)

    assert _issues(result) == {
        ("error", "dynamic models file 'missing.dyd' not found"): [],
        ("error", "library 'NoSuchLib' not in this Dynawo's ddb"): ["X"],
        ("error", "variable 'load_QPu' not declared by 'LoadAlphaBeta'"): ["L1"],     # through the macroConnector
        ("error", "parameter set 'Missing' not in 'case.par'"): [],
        ("warning", "parameter 'load_P0Pu' not declared by 'LoadAlphaBeta'"): ["set Load"],
        ("warning", "parameter 'load_gamma' not declared by 'LoadAlphaBeta'"): ["set Load"],  # from its macro
        ("info", "1 Modelica model(s) compiled at run time: not checked"): [],
    }
    assert (result["models"], result["libraries"], result["parameter_sets"]) == (4, 3, 3)


def test_missing_solver_library_is_an_error(case, tmp_path):
    exe = make_fake_dynawo(tmp_path / "dynawo")
    os.remove(tmp_path / "dynawo" / "lib" / "dynawo_SolverIDA.so")

    assert ("error", "solver library 'dynawo_SolverIDA' not found in this Dynawo") in _issues(check_job(case, "case.jobs", exe))


def test_par_sets_are_read_with_their_macros(case):
    sets = scan_par_sets(os.path.join(case, "case.par"), {"Load", "IDA"})

    assert sets == {"Load": {"load_alpha", "load_P0Pu", "load_gamma"}, "IDA": {"order"}}


def test_versions_compare_as_numbers():
    assert version_tuple("1.10.0") > version_tuple("1.9.2")
    assert version_tuple("2.0.0 (rev:master-6e26b2b)")[:3] == (2, 0, 0)


def test_only_the_dynawo_own_update_tool_is_used(tmp_path, monkeypatch):
    old = make_fake_dynawo(tmp_path / "old", scripts=("1.7.0.8", "1.7.0.9"))
    home = make_fake_dynawo(tmp_path / "home", scripts=("1.8.0.16", "2.0.0.2"))
    monkeypatch.setenv("DYNAWO_HOME", str(tmp_path / "home"))

    tool = find_update_tool(old)

    assert tool == update_tool_place(old) == str(tmp_path / "old" / "sbin" / "updateXML" / "update.py")
    assert latest_script(tool) == "1.7.0.9"
    assert tool_versions(find_update_tool(home)) == ["1.8.0", "2.0.0"]
    assert find_update_tool(str(tmp_path / "nowhere" / "dynawo.sh")) is None


def test_no_update_tool_found(tmp_path):
    assert find_update_tool(str(tmp_path / "nowhere" / "dynawo.sh")) is None


def test_update_tool_output_and_tickets_are_collected(case, tmp_path):
    tool = find_update_tool(make_fake_dynawo(tmp_path / "dynawo"))

    result = run_update(tool, sys.executable, os.path.join(case, "case.jobs"), FULL_UPGRADE_ORIGIN, "2.0.0",
                        str(tmp_path / "out"))

    assert sorted(result["files"]) == ["case.dyd", "case.jobs", "case.par"]
    assert result["tickets"] == ["4223"]


@pytest.mark.skipif(os.name == "nt", reason="the Python without imp is a shell script")
def test_update_tool_using_imp_runs_on_a_python_without_it(case, tmp_path):
    """Dynawo 1.7's update tool imports imp, which Python 3.12 removed."""
    tool = find_update_tool(make_fake_dynawo(tmp_path / "dynawo"))
    (tmp_path / "step.py").write_text("STEP = 1\n")
    with open(tool) as fh:
        body = fh.read()
    with open(tool, "w") as fh:
        fh.write(f"import imp\nassert imp.load_source('step', {str(tmp_path / 'step.py')!r}).STEP == 1\n" + body)
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "imp.py").write_text("raise ModuleNotFoundError(\"No module named 'imp'\")\n")
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\nPYTHONPATH="${{PYTHONPATH:+$PYTHONPATH:}}{blocker}" exec {sys.executable} "$@"\n')
    os.chmod(python, 0o755)

    result = run_update(tool, str(python), os.path.join(case, "case.jobs"), FULL_UPGRADE_ORIGIN, "2.0.0",
                        str(tmp_path / "out"))

    assert result["tickets"] == ["4223"]


def test_update_tool_failure_is_raised(case, tmp_path):
    tool = find_update_tool(make_fake_dynawo(tmp_path / "dynawo"))

    with pytest.raises(RuntimeError):
        run_update(tool, sys.executable, os.path.join(case, "missing.jobs"), FULL_UPGRADE_ORIGIN, "2.0.0",
                   str(tmp_path / "out"))


def test_migration_changes_name_the_files_an_upgrade_would_change(case, tmp_path):
    tool = find_update_tool(make_fake_dynawo(tmp_path / "dynawo"))

    changes = migration_changes(tool, sys.executable, case, "case.jobs", FULL_UPGRADE_ORIGIN, "2.0.0")

    assert changes == {"changed": ["case.dyd"], "tickets": ["4223"]}           # the others are unchanged
    assert not any(name.startswith("applied") for name in os.listdir(case))   # on a copy


def test_upgraded_file_points_to_the_files_not_upgraded_one_folder_up(case):
    content = relink(os.path.join(case, "case.jobs"), {"case.jobs", "case.dyd", "case.par"}).decode()

    assert 'iidmFile="../case.iidm"' in content                               # not upgraded
    assert 'dydFile="../missing.dyd"' in content
    assert 'dydFile="case.dyd"' in content and 'parFile="case.par"' in content
