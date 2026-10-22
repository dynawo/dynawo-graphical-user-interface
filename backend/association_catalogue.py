#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

import re
import threading

import pypowsybl.dynamic as dyn

# Model catalogue of the running pypowsybl build, read at runtime: builds
# differ in the models and categories they offer.

# Equipment categories map a model onto a network element (one dataframe
# indexed by static_id); automation systems use their own dynamic_model_id.
EQUIPMENT = "equipment"
AUTOMATION_SYSTEM = "automation_system"

_DATAFRAME_NAME = re.compile(r'^\[dataframe "([^"]*)"\]\s*')
_COLUMN = re.compile(r"^(\w+)\s*\((\w+)\)$")

_cache: list[dict] | None = None
_cache_lock = threading.Lock()


def parse_attributes(attribute: str) -> list[dict]:
    """Parse a category's `attribute` text into [{name, index, columns}].

    `name` is None for a single dataframe; `index` is its first column.
    """
    dataframes = []
    for part in attribute.split(" / "):
        part = part.strip()
        if not part:
            continue
        name = None
        m = _DATAFRAME_NAME.match(part)
        if m:
            name = m.group(1)
            part = part[m.end():]
        part = re.sub(r"^index\s*:\s*", "", part)
        columns = []
        for col in part.split(","):
            cm = _COLUMN.match(col.strip())
            if cm:
                columns.append({"name": cm.group(1), "type": cm.group(2)})
        dataframes.append({
            "name":    name,
            "index":   columns[0]["name"] if columns else None,
            "columns": columns,
        })
    return dataframes


def category_kind(dataframes: list[dict]) -> str:
    """EQUIPMENT for a single dataframe indexed by static_id, else AUTOMATION_SYSTEM."""
    if len(dataframes) == 1 and dataframes[0]["index"] == "static_id":
        return EQUIPMENT
    return AUTOMATION_SYSTEM


def build_catalogue(categories, models) -> list[dict]:
    """Merge pypowsybl's category and model dataframes, one entry per category:
    {name, description, kind, dataframes, models: [{name, description}]}.
    """
    entries: dict[str, dict] = {}
    for name, row in categories.iterrows():
        dataframes = parse_attributes(str(row.get("attribute", "") or ""))
        entries[name] = {
            "name":        name,
            "description": str(row.get("description", "") or ""),
            "kind":        category_kind(dataframes),
            "dataframes":  dataframes,
            "models":      [],
        }
    for model_name, row in models.iterrows():
        category = str(row.get("category", "") or "")
        entry = entries.get(category)
        if entry is None:
            entry = entries[category] = {
                "name": category, "description": "", "kind": category_kind([]),
                "dataframes": [], "models": [],
            }
        entry["models"].append({"name": model_name, "description": str(row.get("description", "") or "")})
    for entry in entries.values():
        entry["models"].sort(key=lambda m: m["name"])
    return sorted(entries.values(), key=lambda e: e["name"])


def get_catalogue() -> list[dict]:
    """Catalogue of the running pypowsybl build, read once. Needs no Dynawo."""
    global _cache
    with _cache_lock:
        if _cache is None:
            mapping = dyn.ModelMapping()
            _cache = build_catalogue(
                mapping.get_categories_information(),
                mapping.get_supported_models_information(),
            )
        return _cache
