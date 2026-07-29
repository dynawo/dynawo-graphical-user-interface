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
import socket
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass


@dataclass
class DownloadState:
    fraction: float = 0.0
    text: str = "Starting…"
    done: bool = False
    error: str | None = None


class DownloadManager:
    """Downloads and extracts a ZIP in a background thread.

    Progress is readable from any thread via the `state` property.
    The thread survives Streamlit page navigation because it is a daemon
    thread independent of the script runner lifecycle.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = DownloadState()

    def start(self, url: str, dest_dir: str) -> None:
        if self.is_running():
            return
        with self._lock:
            self._state = DownloadState()
        self._thread = threading.Thread(
            target=self._run, args=(url, dest_dir), daemon=True
        )
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> DownloadState:
        with self._lock:
            s = self._state
            return DownloadState(
                fraction=s.fraction, text=s.text, done=s.done, error=s.error
            )

    def _update(self, fraction: float, text: str) -> None:
        with self._lock:
            self._state.fraction = fraction
            self._state.text = text

    def _run(self, url: str, dest_dir: str) -> None:
        try:
            self._download(url, dest_dir)
            with self._lock:
                self._state.done = True
                self._state.fraction = 1.0
                self._state.text = "Done"
        except Exception as exc:
            with self._lock:
                self._state.done = True
                self._state.error = f"{type(exc).__name__}: {exc}"

    def _download(self, url: str, dest_dir: str) -> None:
        # Download to a temp file so dest_dir stays empty until extraction,
        # keeping _is_downloaded() correct while the download is in progress.
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            zip_path = tmp.name

        try:
            _PROXY_HINT = (
                "Connection timed out. If your network requires an HTTP proxy, "
                "set the https_proxy environment variable before starting the app "
                "(e.g. export https_proxy=http://proxy:3128) and restart."
            )
            req = urllib.request.Request(url, headers={"User-Agent": "dynawo-ihm/1.0"})
            try:
                resp_cm = urllib.request.urlopen(req, timeout=15)
            except (socket.timeout, urllib.error.URLError) as exc:
                cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                if isinstance(cause, socket.timeout) or "timed out" in str(cause).lower():
                    raise RuntimeError(_PROXY_HINT) from exc
                raise
            with resp_cm as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536
                with open(zip_path, "wb") as fh:
                    while True:
                        data = resp.read(chunk_size)
                        if not data:
                            break
                        fh.write(data)
                        downloaded += len(data)
                        if total:
                            mb_done = downloaded / 1024 / 1024
                            mb_total = total / 1024 / 1024
                            self._update(
                                min(downloaded / total, 1.0),
                                f"Downloading… {mb_done:.1f} / {mb_total:.1f} MB",
                            )

            self._update(1.0, "Extracting…")
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.infolist():
                    dest_path = os.path.join(dest_dir, member.filename)
                    unix_mode = (member.external_attr >> 16) & 0o170000

                    if unix_mode == 0o120000:
                        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                        link_target = zf.read(member).decode()
                        if os.path.lexists(dest_path):
                            os.remove(dest_path)
                        os.symlink(link_target, dest_path)
                    else:
                        zf.extract(member, dest_dir)
                        if os.path.isfile(dest_path) and (member.external_attr >> 16) & 0o111:
                            os.chmod(dest_path, os.stat(dest_path).st_mode | 0o111)
        finally:
            if os.path.exists(zip_path):
                os.remove(zip_path)
