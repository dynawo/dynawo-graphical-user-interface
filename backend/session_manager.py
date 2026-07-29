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
import tempfile
import weakref

from backend.file_type_detector import detect_dynawo_type
from backend.models import FileInfo


class SessionDir:
    """Owns a temporary directory for the lifetime of one Streamlit session.

    weakref.finalize ensures the directory is deleted when this object is
    garbage-collected, which happens when the session ends.
    """

    def __init__(self) -> None:
        self.path = tempfile.mkdtemp(prefix="dynawo_session_")
        self._finalizer = weakref.finalize(self, shutil.rmtree, self.path, True)

    def cleanup(self) -> None:
        """Immediately delete the temp dir and disarm the GC finalizer."""
        self._finalizer()


class SessionManager:
    """Manages uploaded files within a session working directory."""

    def __init__(self, working_dir: str) -> None:
        self._working_dir = working_dir
        self._files: dict[str, FileInfo] = {}
        self._raw: dict[str, bytes] = {}

    @property
    def working_dir(self) -> str:
        return self._working_dir

    @property
    def files(self) -> dict[str, FileInfo]:
        return dict(self._files)

    def _resolve(self, name: str) -> str:
        """Resolve `name` (which may include subdirectories, e.g. from a dropped
        folder) to an absolute path, rejecting anything that would escape the
        working directory (e.g. "../../etc/passwd" or an absolute path).
        """
        base = os.path.normpath(self._working_dir)
        dest = os.path.normpath(os.path.join(base, name))
        if dest != base and not dest.startswith(base + os.sep):
            raise ValueError(f"Invalid file path: {name!r}")
        return dest

    def add_file(self, name: str, data: bytes) -> FileInfo:
        """Write a file to disk and register it. Returns the FileInfo."""
        dest = self._resolve(name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        info = FileInfo(name=name, size=len(data), ftype=detect_dynawo_type(data, name))
        self._files[name] = info
        self._raw[name] = data
        return info

    def replace_file(self, name: str, data: bytes) -> FileInfo:
        """Overwrite an existing file on disk and update its record."""
        return self.add_file(name, data)

    def remove_file(self, name: str) -> None:
        """Delete a file from disk and remove its record."""
        dest = self._resolve(name)
        if os.path.isfile(dest):
            os.remove(dest)
        self._files.pop(name, None)
        self._raw.pop(name, None)

    def get_raw(self, name: str) -> bytes | None:
        """Return the original uploaded bytes for a file, or None."""
        return self._raw.get(name)

    def get_path(self, name: str) -> str:
        """Return the absolute path of a file in the working directory."""
        return self._resolve(name)

    def register_existing_file(self, name: str) -> FileInfo:
        """Register a file already present in the working directory (e.g. copied by autoload).
        Reads bytes from disk to detect type and populate _raw; does not re-write the file.
        """
        path = self._resolve(name)
        with open(path, "rb") as fh:
            data = fh.read()
        info = FileInfo(name=name, size=len(data), ftype=detect_dynawo_type(data, name))
        self._files[name] = info
        self._raw[name] = data
        return info

    def has_file(self, name: str) -> bool:
        return name in self._files

    def files_by_type(self, ftype: str) -> list[str]:
        """Return names of all files with the given detected type."""
        return [name for name, info in self._files.items() if info.ftype == ftype]
