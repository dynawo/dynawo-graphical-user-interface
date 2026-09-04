#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import json
import os
import threading
from typing import Callable

_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "config.json")

# Guards the read-modify-write cycle in update_user_config. FastAPI runs the
# sync endpoints that write here in a threadpool, so requests really do overlap
# — the two Load Flow panels saving their parameters at once, or one of them
# saving while a Dynawo version is being selected. Each writer rewrites the
# whole file, so without this lock whichever finishes last silently drops the
# other's key, and settings disappear for no visible reason.
#
# Re-entrant because update_user_config calls save_user_config while holding it.
#
# A thread lock is enough only because the API runs as a single process (see
# run.sh / docker-entrypoint.sh — uvicorn without --workers). If that ever
# changes this needs a file lock instead.
_LOCK = threading.RLock()


def load_user_config() -> dict:
    """Load persisted user preferences from disk."""
    try:
        with open(_CONFIG_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_user_config(config: dict) -> None:
    """Overwrite the persisted user preferences.

    Callers that want to change one key must go through update_user_config
    instead — this replaces the whole file.
    """
    with _LOCK:
        os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
        # Write to a sibling then rename: os.replace is atomic, so an
        # interrupted write can't leave a truncated config.json behind —
        # load_user_config would read that as {} and every saved preference
        # would be gone at once.
        tmp_path = f"{_CONFIG_PATH}.tmp"
        with open(tmp_path, "w") as fh:
            json.dump(config, fh, indent=2)
        os.replace(tmp_path, _CONFIG_PATH)


def update_user_config(mutate: Callable[[dict], None]) -> None:
    """Apply `mutate` to the saved config as one atomic read-modify-write.

    Every partial update must use this rather than load / modify / save by
    hand: holding the lock across the whole cycle is what stops concurrent
    requests from overwriting each other's keys.
    """
    with _LOCK:
        cfg = load_user_config()
        mutate(cfg)
        save_user_config(cfg)


def get_default_executable() -> str:
    """Return the Dynawo executable path from env var or saved config, or empty string."""
    return os.environ.get("DYNAWO_DEFAULT_EXE") or load_user_config().get("dynawo_executable", "")


def get_local_executables() -> list[str]:
    """Return manually-set local Dynawo executable paths saved in config, most recent first."""
    return load_user_config().get("local_executables", [])


def add_local_executable(exe: str) -> None:
    """Persist a manually-set local Dynawo executable path and mark it as last-used."""
    def mutate(cfg: dict) -> None:
        paths = [p for p in cfg.get("local_executables", []) if p != exe]
        paths.insert(0, exe)
        cfg["local_executables"] = paths
        cfg["dynawo_executable"] = exe
    update_user_config(mutate)


def remove_local_executable(exe: str) -> None:
    """Remove a manually-set local Dynawo executable path from the saved config."""
    def mutate(cfg: dict) -> None:
        cfg["local_executables"] = [p for p in cfg.get("local_executables", []) if p != exe]
    update_user_config(mutate)


def clear_default_executable() -> None:
    """Clear the last-used executable pointer without touching saved local executables."""
    update_user_config(lambda cfg: cfg.__setitem__("dynawo_executable", ""))


def get_loadflow_parameters(provider: str) -> dict:
    """Return the saved load-flow panel settings for a provider, or {} if none."""
    return load_user_config().get("loadflow_parameters", {}).get(provider, {})


def save_loadflow_parameters(provider: str, params: dict) -> None:
    """Persist the load-flow panel settings chosen for a provider."""
    def mutate(cfg: dict) -> None:
        cfg.setdefault("loadflow_parameters", {})[provider] = params
    update_user_config(mutate)


def clear_loadflow_parameters(provider: str) -> None:
    """Forget the saved load-flow panel settings for a provider, so the next
    load falls back to the provider's own defaults."""
    def mutate(cfg: dict) -> None:
        cfg.setdefault("loadflow_parameters", {}).pop(provider, None)
    update_user_config(mutate)
