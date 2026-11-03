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

from backend import association_rule_sets
from backend.association_rule_sets import delete_rule_set, exists, list_rule_sets, load_rule_set, save_rule_set, valid_name


@pytest.fixture(autouse=True)
def rule_sets_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(association_rule_sets, "RULE_SETS_DIR", str(tmp_path / "association_rules"))


@pytest.mark.parametrize(("name", "valid"), [
    ("IEEE14 base", True), ("case-1.v2", True), ("", False), (" leading", False), ("trailing.", False),
    ("../escape", False), ("a/b", False), ("x" * 81, False),
])
def test_names_are_plain_file_names(name, valid):
    assert valid_name(name) is valid


def test_rule_set_is_saved_listed_loaded_and_deleted():
    assert list_rule_sets() == [] and load_rule_set("base") is None

    save_rule_set("base", [{"model": "LoadAlphaBeta"}], {"stop_time": 20})
    save_rule_set("rules only", [{"model": "A"}, {"model": "B"}])

    assert exists("base")
    assert {(s["name"], s["count"]) for s in list_rule_sets()} == {("base", 1), ("rules only", 2)}
    assert load_rule_set("base") == {"rules": [{"model": "LoadAlphaBeta"}], "options": {"stop_time": 20}}
    assert load_rule_set("rules only")["options"] is None
    assert delete_rule_set("base") and not exists("base")
    assert not delete_rule_set("base")


def test_unreadable_files_are_skipped(tmp_path):
    save_rule_set("good", [])
    (tmp_path / "association_rules" / "broken.json").write_text("{")
    (tmp_path / "association_rules" / "notes.txt").write_text("")

    assert [s["name"] for s in list_rule_sets()] == ["good"]
    assert load_rule_set("broken") is None
