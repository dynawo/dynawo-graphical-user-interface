#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Labels and creation defaults of the jobs editor, and new elements built from them.

Hints are keyed by element name ("outputs") and "element@attribute"
("solver@lib"); hints for what the schema does not have are ignored.
"""

import functools
import json
import os

from backend.jobs_schema import ElementSpec

HINTS_PATH = os.path.join(os.path.dirname(__file__), "..", "resources", "jobs_editor_hints.json")


@functools.lru_cache(maxsize=1)
def load_hints() -> dict:
    """{"elements": {name: hint}, "attributes": {"element@attribute": hint}}."""
    with open(HINTS_PATH, encoding="utf-8") as fh:
        hints = json.load(fh)
    return {"elements": hints.get("elements", {}), "attributes": hints.get("attributes", {})}


def new_element(spec: ElementSpec, hints: dict | None = None) -> dict:
    """Node of a new element: its required content, plus what the hints give a default or create."""
    hints = hints if hints is not None else load_hints()
    attrs = {}
    for attr in spec.attributes:
        hint = hints["attributes"].get(f"{spec.name}@{attr.name}", {})
        if "default" in hint:
            attrs[attr.name] = hint["default"]
        elif attr.required:
            attrs[attr.name] = attr.default or ""
    children = []
    for child in spec.children:
        created = hints["elements"].get(child.name, {}).get("create", False)
        children += [new_element(child, hints) for _ in range(max(child.min, 1 if created else 0))]
    return {"tag": spec.name, "attrs": attrs, "children": children}
