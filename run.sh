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
# run.sh — start the Dynawo GUI for local development on Linux/macOS.
#
# Launches the FastAPI backend (uvicorn, port 8000) and the React/Vite dev
# server (port 5173) together, and shuts both down cleanly on Ctrl+C. The Vite
# dev server proxies /api/* to the backend, so open the app at :5173.
#
# Run ./install.sh first. Environment variables from the README (SESSION_TTL,
# MAX_CONCURRENT_SIMS, DYNAWO_DEFAULT_EXE, ...) are passed through to the
# backend, e.g.:
#   SESSION_TTL=7200 ./run.sh
#
# Usage:
#   ./run.sh

set -euo pipefail

# Resolve the repo root from the script location so this works from any CWD.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV_DIR="$ROOT/.venv"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=5173

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m ⚠\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    # Print the leading doc-comment block (skip shebang, stop at code).
    awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
    exit 0
fi

# --- guards ------------------------------------------------------------------

if [ ! -x "$VENV_DIR/bin/uvicorn" ]; then
    fail "Backend not installed (no .venv). Run ./install.sh first."
fi
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    fail "Frontend not installed (no frontend/node_modules). Run ./install.sh first."
fi

# --- process management ------------------------------------------------------

PIDS=()

cleanup() {
    # Avoid re-entrancy while we tear down.
    trap - INT TERM EXIT
    info "Shutting down..."
    for pid in "${PIDS[@]}"; do
        # Kill the whole process group so uvicorn's --reload workers and
        # vite's child processes go down too.
        kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- launch ------------------------------------------------------------------

info "Starting backend  → http://localhost:${BACKEND_PORT}  (Swagger at /docs)"
# setsid gives the backend its own process group so cleanup can kill reload
# workers as a group; fall back to a plain launch if setsid is unavailable.
if command -v setsid >/dev/null 2>&1; then
    setsid "$VENV_DIR/bin/uvicorn" api.main:app --reload --port "$BACKEND_PORT" &
else
    "$VENV_DIR/bin/uvicorn" api.main:app --reload --port "$BACKEND_PORT" &
fi
PIDS+=($!)

info "Starting frontend → http://localhost:${FRONTEND_PORT}  (open this one)"
if command -v setsid >/dev/null 2>&1; then
    ( cd "$FRONTEND_DIR" && exec setsid npm run dev ) &
else
    ( cd "$FRONTEND_DIR" && exec npm run dev ) &
fi
PIDS+=($!)

# --- announce when ready -----------------------------------------------------
# Wait until the frontend actually accepts connections, then print a clear
# "open this" banner. Without this the URL scrolls off screen behind uvicorn
# and Vite startup logs. Uses bash's /dev/tcp so no extra tools are needed;
# gives up after ~60s in case startup fails.
echo
info "Servers starting — waiting for the frontend to come up..."
READY=0
for ((i = 0; i < 60; i++)); do
    if (exec 3<>"/dev/tcp/127.0.0.1/${FRONTEND_PORT}") 2>/dev/null; then
        exec 3>&- 2>/dev/null || true
        READY=1
        break
    fi
    sleep 1
done

echo
if [ "$READY" -eq 1 ]; then
    printf '\033[1;32m════════════════════════════════════════════════════════\033[0m\n'
    printf '\033[1;32m  ✓  Dynawo GUI is ready\033[0m\n\n'
    printf '     Open  \033[1;36mhttp://localhost:%s\033[0m  in your browser\n\n' "${FRONTEND_PORT}"
    printf '     Swagger API docs:  http://localhost:%s/docs\n' "${BACKEND_PORT}"
    printf '     Press Ctrl+C here to stop both servers\n'
    printf '\033[1;32m════════════════════════════════════════════════════════\033[0m\n'
else
    warn "Frontend did not respond on port ${FRONTEND_PORT} within 60s — check the logs above."
    warn "If it is still starting, open http://localhost:${FRONTEND_PORT} in your browser manually."
fi
echo

# Wait for any child to exit; if one dies, cleanup() (via EXIT trap) stops the other.
wait -n 2>/dev/null || wait
