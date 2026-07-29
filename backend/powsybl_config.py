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
import re

import yaml

# App-owned PowSyBl config dir — never the user's personal ~/.itools.
# Switching launcher versions inside this app must never touch a user's own
# PowSyBl config, and on a shared server the launcher choice is a single
# global setting for everyone, not a per-home-directory file.
_APP_CONFIG_ROOT = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm")
_ITOOLS_DIR = os.path.join(_APP_CONFIG_ROOT, ".itools")


def powsybl_config_dir() -> str:
    return _ITOOLS_DIR


def powsybl_config_path() -> str:
    return os.path.join(_ITOOLS_DIR, "config.yml")


def bootstrap_powsybl_config_dir() -> None:
    """Redirect PowSyBl to the app-owned .itools dir.

    Must run before the first pypowsybl import in the process: the GraalVM
    native image reads the powsybl.config.dirs system property at init time.
    Idempotent — safe to call multiple times.
    """
    os.makedirs(_ITOOLS_DIR, exist_ok=True)
    opt = f"-Dpowsybl.config.dirs={_ITOOLS_DIR}"
    existing = os.environ.get("GRAALVM_OPTIONS", "")
    if "powsybl.config.dirs" not in existing:
        os.environ["GRAALVM_OPTIONS"] = (existing + " " + opt).strip()


def _load_config() -> dict:
    path = powsybl_config_path()
    if not os.path.isfile(path):
        return {}
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def _save_config(data: dict) -> None:
    path = powsybl_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)


_STARTING_POINT_VOLTAGE_INIT = {"WARM": "DC_VALUES", "FLAT": "UNIFORM_VALUES"}
_VOLTAGE_INIT_TO_MODE = {v: k for k, v in _STARTING_POINT_VOLTAGE_INIT.items()}


def set_starting_point_mode(mode: str) -> None:
    """Write load-flow-default-parameters.voltageInitMode into config.yml.
    WARM → DC_VALUES, FLAT → UNIFORM_VALUES.
    """
    voltage_init = _STARTING_POINT_VOLTAGE_INIT.get(mode.upper(), "DC_VALUES")
    data = _load_config()
    data.setdefault("load-flow-default-parameters", {})["voltageInitMode"] = voltage_init
    _save_config(data)


def get_starting_point_mode() -> str:
    """Return 'WARM' or 'FLAT', defaulting to 'WARM' if unset."""
    data = _load_config()
    voltage_init = (data.get("load-flow-default-parameters") or {}).get("voltageInitMode")
    return _VOLTAGE_INIT_TO_MODE.get(voltage_init, "WARM")


def set_dynaflow_home(home_dir: str, debug: bool = False) -> None:
    """Write dynaflow.homeDir into the app-owned config.yml, preserving other sections."""
    data = _load_config()
    data.setdefault("dynaflow", {})
    data["dynaflow"]["homeDir"] = home_dir
    data["dynaflow"]["debug"] = debug
    _save_config(data)


def get_dynaflow_home() -> str | None:
    data = _load_config()
    return (data.get("dynaflow") or {}).get("homeDir")


def clear_dynaflow_home() -> None:
    data = _load_config()
    if "dynaflow" in data:
        del data["dynaflow"]
        _save_config(data)


# ── Per-worker config ────────────────────────────────────────────────────────
#
# Load-flow runs are dispatched to a pool of persistent worker processes.
# Without this, every worker would
# inherit the same shared computation-local.tmp-dir from the parent process's
# GRAALVM_OPTIONS, so two DynaFlow runs on two different workers at the same
# moment would still write into the same directory — exactly the attribution
# ambiguity the process-wide lock exists
# to avoid. Giving each worker its own private tmp-dir (set once, before that
# worker's first pypowsybl import) removes the need for that lock: a
# ProcessPoolExecutor worker only ever runs one task at a time, so within a
# single worker's private directory there is never more than one DynaFlow run
# in flight.

_worker_tmp_dir: str | None = None


def worker_tmp_dir() -> str | None:
    """The computation-local.tmp-dir private to *this* worker process, or
    None outside a worker (e.g. in the main API process)."""
    return _worker_tmp_dir


def bootstrap_worker_config_dir(worker_dir: str) -> str:
    """Per-worker analogue of bootstrap_powsybl_config_dir(). Must run before
    this worker process's first pypowsybl import — GraalVM reads
    powsybl.config.dirs at init time only. Returns the worker's private
    computation-local.tmp-dir.
    """
    global _worker_tmp_dir
    os.makedirs(worker_dir, exist_ok=True)
    tmp_dir = os.path.join(worker_dir, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    shared = _load_config()
    data = {
        "dynaflow": dict(shared.get("dynaflow") or {}),
        "computation-local": {"tmp-dir": tmp_dir},
    }
    if shared.get("load-flow-default-parameters"):
        data["load-flow-default-parameters"] = dict(shared["load-flow-default-parameters"])
    # Forced True regardless of the shared value: without this, PowSyBl
    # deletes a DynaFlow run's working directory synchronously inside
    # run_ac/run_dc, before our own per-run cleanup
    # (backend/loadflow_runner.run_loadflow_in_worker) ever gets a chance to
    # see it — even for an immediate, non-interactive delete right after.
    data["dynaflow"]["debug"] = True
    with open(os.path.join(worker_dir, "config.yml"), "w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=False)

    # Strip any -Dpowsybl.config.dirs=... already in GRAALVM_OPTIONS before
    # appending ours: this worker process inherits the *parent's* env at
    # spawn time (set by bootstrap_powsybl_config_dir() before the worker
    # pool was even created), so without stripping, this would silently end
    # up with two conflicting -Dpowsybl.config.dirs flags in the same string
    # — confirmed empirically: the worker's tmp-dir was never actually used
    # by PowSyBl when this bug was present (DynaFlow ran, but no working
    # directory ever appeared under it).
    existing = re.sub(r"-Dpowsybl\.config\.dirs=\S*", "", os.environ.get("GRAALVM_OPTIONS", "")).strip()
    opt = f"-Dpowsybl.config.dirs={worker_dir}"
    os.environ["GRAALVM_OPTIONS"] = (existing + " " + opt).strip()

    _worker_tmp_dir = tmp_dir
    return tmp_dir


def bootstrap_new_worker() -> None:
    """ProcessPoolExecutor initializer for the pypowsybl worker pool
    (api/routers/loadflow.py). Deliberately lives in *this* module, not
    alongside the pool itself: unpickling an initializer reference in a
    freshly-spawned worker process requires importing its defining module —
    if that were api/routers/loadflow.py, importing it would transitively
    `import pypowsybl.loadflow` (backend/loadflow_runner.py's top-level
    import) *before* this function's body ever ran, initialising the JVM
    with the wrong, inherited GRAALVM_OPTIONS and making the override below
    a no-op. Confirmed empirically: with the initializer in loadflow.py, the
    worker's private tmp-dir was set correctly in os.environ but never
    actually used by PowSyBl. This module never imports pypowsybl, by
    design, so importing it alone cannot trigger that JVM init.
    """
    import shutil
    import tempfile
    import uuid
    import atexit

    worker_dir = os.path.join(tempfile.gettempdir(), f"dynawo_ihm_worker_{uuid.uuid4().hex}")
    bootstrap_worker_config_dir(worker_dir)
    # Self-cleanup on this worker's own normal exit (e.g. pool.shutdown()).
    # Doesn't help on SIGKILL, same limitation already accepted for
    # _pypowsybl_pool.shutdown() itself in api/main.py.
    atexit.register(shutil.rmtree, worker_dir, ignore_errors=True)
