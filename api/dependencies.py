#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

from fastapi import Cookie, Response

from api.session_store import UserSession, store


def get_session(response: Response, session_id: str = Cookie(default=None)) -> UserSession:
    if session_id:
        session = store.get(session_id)
        if session:
            return session
    # Auto-create an anonymous session and set the cookie
    session = store.create(user="anonymous")
    response.set_cookie("session_id", session.session_id, httponly=True, samesite="lax")
    return session
