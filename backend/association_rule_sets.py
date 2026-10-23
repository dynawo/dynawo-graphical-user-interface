#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import datetime
import json
import os
import re

# Named sets of dynamic-association rules, kept in the app's config directory
# so that they can be applied again in any session.

RULE_SETS_DIR = os.path.join(os.path.expanduser("~"), ".config", "dynawo_ihm", "association_rules")

# Version of the stored format, for reading older files later.
FORMAT = 1

_NAME = re.compile(r"^[\w][\w .-]{0,79}$")


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name)) and not name.endswith((" ", "."))


def _path(name: str) -> str:
    return os.path.join(RULE_SETS_DIR, f"{name}.json")


def list_rule_sets() -> list[dict]:
    """Saved sets: [{name, saved, count}], most recent first."""
    sets = []
    try:
        files = os.listdir(RULE_SETS_DIR)
    except OSError:
        return []
    for file in files:
        if not file.endswith(".json"):
            continue
        try:
            with open(os.path.join(RULE_SETS_DIR, file), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        sets.append({"name": file[:-5], "saved": data.get("saved", ""), "count": len(data.get("rules", []))})
    return sorted(sets, key=lambda s: s["saved"], reverse=True)


def exists(name: str) -> bool:
    return os.path.isfile(_path(name))


def load_rule_set(name: str) -> dict | None:
    """{rules, options}; options (generation settings) is None when not saved."""
    try:
        with open(_path(name), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return {"rules": data.get("rules", []), "options": data.get("options")}


def save_rule_set(name: str, rules: list[dict], options: dict | None = None) -> None:
    os.makedirs(RULE_SETS_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"format": FORMAT, "saved": datetime.datetime.now().isoformat(timespec="seconds"),
                   "rules": rules, "options": options}, fh, indent=2)
    os.replace(tmp, _path(name))


def delete_rule_set(name: str) -> bool:
    try:
        os.remove(_path(name))
        return True
    except OSError:
        return False
