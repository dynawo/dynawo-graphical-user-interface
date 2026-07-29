#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from dataclasses import dataclass, field


@dataclass
class SimulationConfig:
    start_time: float
    stop_time: float
    output_dir: str


@dataclass
class SolverInfo:
    lib: str
    par_file: str
    par_id: str


@dataclass
class RunRecord:
    run_id: int
    label: str
    jobs_file: str
    output_dir: str
    returncode: int | None
    output: str
    start_time: float
    stop_time: float
    exports_final_iidm: bool = False
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class ParameterChange:
    name: str
    old_value: str
    new_value: str


@dataclass
class ChangeLogEntry:
    timestamp: str
    par_file: str
    set_id: str
    changes: list[ParameterChange]
    dyn_id: str = ""


@dataclass
class DynModel:
    static_id: str
    lib: str
    dyn_id: str
    par_file: str
    par_id: str


@dataclass
class CurveChange:
    model: str
    variable: str
    action: str  # "removed" | "added"


@dataclass
class CrvChangeLogEntry:
    timestamp: str
    crv_file: str
    changes: list[CurveChange]


@dataclass
class FileInfo:
    name: str
    size: int
    ftype: str | None
