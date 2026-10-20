#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Fixtures shared by the whole test suite."""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture
def data_dir() -> Path:
    """tests/data — the small hand-written input and expected files."""
    return DATA_DIR


@pytest.fixture
def copy_data(tmp_path):
    """Copy a file of tests/data into the test's temporary directory.

    For the writers that edit a file in place: the committed fixture must never
    be modified by a test run."""
    def _copy(relative: str) -> Path:
        target = tmp_path / Path(relative).name
        shutil.copy(DATA_DIR / relative, target)
        return target
    return _copy


def canonical_xml(content: bytes | str) -> str:
    """XML in canonical form (C14N 2.0), whitespace between elements stripped.

    Comparing two documents this way ignores what does not change their meaning
    — attribute order, indentation, the XML declaration — so an expected-output
    test only fails on a real difference. Namespace prefixes are kept: Dynawo
    reads either, but a change of prefix is still a change of the file the
    writer produces.

    One tag per line, so a failing comparison shows as a readable line diff."""
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return re.sub(r">\s*<", ">\n<", ET.canonicalize(xml_data=content, strip_text=True))


@pytest.fixture
def assert_same_xml():
    """Assert that an XML output matches the expected file of tests/data."""
    def _assert(actual: bytes | str, expected_relative: str) -> None:
        expected = (DATA_DIR / expected_relative).read_bytes()
        assert canonical_xml(actual) == canonical_xml(expected)
    return _assert
