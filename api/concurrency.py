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
import threading
from contextlib import contextmanager

from fastapi import HTTPException

# Bounds worst-case contention for pypowsybl-heavy in-process work (NAD/SLD/diff
# rendering, load-flow runs) — mirrors _global_sim_semaphore in
# api/routers/simulation.py. Non-blocking: saturation fails fast with a 503
# instead of queuing, since none of this gives real isolation/parallelism on
# its own.
_MAX_CONCURRENT_PYPOWSYBL = int(os.environ.get("MAX_CONCURRENT_PYPOWSYBL", "4"))
_pypowsybl_semaphore = threading.Semaphore(_MAX_CONCURRENT_PYPOWSYBL)


@contextmanager
def pypowsybl_slot():
    if not _pypowsybl_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=503,
            detail=f"Server is at capacity ({_MAX_CONCURRENT_PYPOWSYBL} simultaneous pypowsybl operations). Try again shortly.",
        )
    try:
        yield
    finally:
        _pypowsybl_semaphore.release()
