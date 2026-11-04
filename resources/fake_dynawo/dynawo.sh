#!/bin/sh
#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#
# Fake Dynawo of the dynamic association: PowSyBl's dynawo.homeDir points to a
# copy of this script, so that Simulation.run writes the input files without
# simulating.
#   dynawo.sh version -> content of the "version" file next to this script
#   dynawo.sh jobs ... -> nothing

if [ "$1" = "version" ]; then
    cat "$(dirname "$0")/version"
fi
exit 0
