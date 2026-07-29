#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class SimulationResult:
    returncode: int
    output: str


class SimulationRunner:
    """Runs a Dynawo simulation in a background thread.

    Usage:
        runner = SimulationRunner()
        runner.start(cmd, cwd, on_line=..., on_done=...)
        while runner.is_running():
            time.sleep(0.1)
        result = runner.result
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._result: SimulationResult | None = None
        self._lock = threading.Lock()
        self._new_lines: list[str] = []
        self._lines_lock = threading.Lock()

    def start(
        self,
        cmd: list[str],
        cwd: str,
        on_line: Callable[[str], None] | None = None,
        on_done: Callable[[SimulationResult], None] | None = None,
        env: dict | None = None,
    ) -> None:
        """Launch the simulation in a background thread. Non-blocking."""
        if self.is_running():
            raise RuntimeError("A simulation is already running.")
        self._result = None
        with self._lines_lock:
            self._new_lines.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(cmd, cwd, on_line, on_done, env),
            daemon=True,
        )
        self._thread.start()

    def _run(
        self,
        cmd: list[str],
        cwd: str,
        on_line: Callable[[str], None] | None,
        on_done: Callable[[SimulationResult], None] | None,
        env: dict | None,
    ) -> None:
        output_lines: list[str] = []
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            with self._lock:
                self._proc = proc

            for line in proc.stdout:
                output_lines.append(line)
                with self._lines_lock:
                    self._new_lines.append(line)
                if on_line:
                    on_line(line)
            proc.wait()
            result = SimulationResult(
                returncode=proc.returncode,
                output="".join(output_lines),
            )
        except Exception as exc:
            result = SimulationResult(returncode=-1, output=str(exc))
        finally:
            with self._lock:
                self._proc = None
            self._result = result
            if on_done:
                on_done(result)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def flush_new_lines(self) -> list[str]:
        """Drain and return lines produced since the last flush."""
        with self._lines_lock:
            lines = self._new_lines.copy()
            self._new_lines.clear()
            return lines

    def cancel(self) -> None:
        """Terminate the running process if one exists."""
        with self._lock:
            if self._proc is not None:
                self._proc.terminate()

    @property
    def result(self) -> SimulationResult | None:
        """The result of the last completed run, or None if still running."""
        return self._result
