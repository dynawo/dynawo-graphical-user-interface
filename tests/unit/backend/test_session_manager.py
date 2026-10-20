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

from backend.session_manager import SessionManager


@pytest.fixture
def manager(tmp_path) -> SessionManager:
    (tmp_path / "session").mkdir()
    return SessionManager(str(tmp_path / "session"))


def test_file_is_written_in_the_working_dir(manager, tmp_path):
    manager.add_file("case/x.par", b"<x/>")

    assert (tmp_path / "session" / "case" / "x.par").read_bytes() == b"<x/>"
    assert manager.has_file("case/x.par")


@pytest.mark.parametrize("name", ["../x.par", "case/../../x.par", "/tmp/x.par"])
def test_path_escaping_the_working_dir_is_refused(manager, tmp_path, name):
    """Second line of defence, behind the API's own filename check: a name
    must never write outside the session's folder, whoever passes it."""
    with pytest.raises(ValueError):
        manager.add_file(name, b"<x/>")

    assert not (tmp_path / "x.par").exists()


def test_original_bytes_are_kept_after_an_edit_on_disk(manager):
    manager.add_file("a.par", b"<original/>")
    with open(manager.get_path("a.par"), "wb") as fh:
        fh.write(b"<edited/>")

    assert manager.get_raw("a.par") == b"<original/>"
