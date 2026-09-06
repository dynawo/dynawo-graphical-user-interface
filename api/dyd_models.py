#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from api.session_store import UserSession
from backend.dyd_parser import parse_dyd


def get_dyd_models(session: UserSession, dyd_names: list[str] | None = None) -> dict[str, dict]:
    """Return {dyn_id: {lib, dyn_id, static_id, parFile, parId}} for the whole session.

    Merged across *every* .dyd file of the session, not just the first one: a
    job may declare several <dynModels dydFile="..."/> elements, and DynaFlow
    routinely generates a pair (a base .dyd plus one per contingency). Model
    ids are unique across a job, so the merge is a plain union; should a file
    redefine one anyway, the first .dyd registering it wins.

    Pass `dyd_names` to restrict the merge to a given set of .dyd files — used
    when the caller works on one jobs file and must not see the models of a
    second, unrelated job sharing the session.
    """
    wanted = set(dyd_names) if dyd_names is not None else None
    models: dict[str, dict] = {}
    for name, meta in session.uploaded_files_info.items():
        if meta.get("ftype") != "dyd":
            continue
        if wanted is not None and name not in wanted:
            continue
        raw = session.session_manager.get_raw(name)
        if not raw:
            continue
        try:
            parsed = parse_dyd(raw)
        except Exception:
            continue
        for dyn_id, info in parsed.items():
            models.setdefault(dyn_id, info)
    return models
