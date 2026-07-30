#!/usr/bin/env bash
#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#
# stop.sh — stop the Dynawo GUI containers started by start.sh.
#
# By default this stops and removes the containers but keeps the data volume,
# so simulation results and per-user configuration survive until next time.
#
# Usage:
#   ./docker/stop.sh           # stop the containers, keep the data
#   ./docker/stop.sh --purge   # also delete the data volume (asks first)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -h|--help)
            awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) fail "Unknown option: $arg (try --help)" ;;
    esac
done

if ! docker info >/dev/null 2>&1; then
    fail "The Docker daemon is not responding — nothing to stop.
       Docker Desktop: start it and wait until the whale icon stops animating.
       Linux service:  sudo systemctl start docker"
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "Docker Compose not found — it ships with Docker Desktop and Rancher
       Desktop, or install the plugin: https://docs.docker.com/compose/install/"
fi

if [ "$PURGE" -eq 1 ]; then
    # Deleting the volume throws away downloaded simulation data and saved
    # settings, so make it an explicit choice rather than a flag typo.
    printf '\033[1;33m ⚠\033[0m This deletes the data volume: simulation results and saved\n'
    printf '   settings are lost. The application itself is unaffected.\n'
    printf '   Type "yes" to continue: '
    read -r answer
    if [ "$answer" != "yes" ]; then
        info "Cancelled — nothing was removed."
        exit 0
    fi
    info "Stopping containers and deleting the data volume"
    "${COMPOSE[@]}" down -v
    ok "Stopped, data volume deleted."
else
    info "Stopping containers"
    "${COMPOSE[@]}" down
    ok "Stopped. Data is preserved — start again with ./docker/start.sh"
fi
