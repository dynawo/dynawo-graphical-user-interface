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

_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "config.json")


def load_user_config() -> dict:
    """Load persisted user preferences from disk."""
    try:
        with open(_CONFIG_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_user_config(config: dict) -> None:
    """Persist user preferences to disk."""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w") as fh:
        json.dump(config, fh, indent=2)


def get_default_executable() -> str:
    """Return the Dynawo executable path from env var or saved config, or empty string."""
    return os.environ.get("DYNAWO_DEFAULT_EXE") or load_user_config().get("dynawo_executable", "")


def get_local_executables() -> list[str]:
    """Return manually-set local Dynawo executable paths saved in config, most recent first."""
    return load_user_config().get("local_executables", [])


def add_local_executable(exe: str) -> None:
    """Persist a manually-set local Dynawo executable path and mark it as last-used."""
    cfg = load_user_config()
    paths = [p for p in cfg.get("local_executables", []) if p != exe]
    paths.insert(0, exe)
    cfg["local_executables"] = paths
    cfg["dynawo_executable"] = exe
    save_user_config(cfg)


def remove_local_executable(exe: str) -> None:
    """Remove a manually-set local Dynawo executable path from the saved config."""
    cfg = load_user_config()
    cfg["local_executables"] = [p for p in cfg.get("local_executables", []) if p != exe]
    save_user_config(cfg)


def clear_default_executable() -> None:
    """Clear the last-used executable pointer without touching saved local executables."""
    cfg = load_user_config()
    cfg["dynawo_executable"] = ""
    save_user_config(cfg)
