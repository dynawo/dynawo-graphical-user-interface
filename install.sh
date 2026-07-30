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
# install.sh — set up the Dynawo GUI for local development on Linux/macOS.
#
# Creates the Python virtual environment, installs the backend dependencies,
# and installs the frontend node_modules. Prerequisites (Python, Node, npm)
# are checked but never installed automatically — if something is missing the
# script prints exactly how to install it and exits.
#
# Usage:
#   ./install.sh          # install / update everything (idempotent)
#   ./install.sh --clean  # remove .venv and frontend/node_modules, then
#                         # reinstall (the tracked package-lock.json is kept)
#
# --clean does NOT remove downloaded Dynawo versions (~/.config/dynawo_ihm/).

set -euo pipefail

# Resolve the repo root from the script location so this works from any CWD.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV_DIR="$ROOT/.venv"
FRONTEND_DIR="$ROOT/frontend"

# Minimum supported versions (see README).
PY_MIN_MAJOR=3
PY_MIN_MINOR=10
NODE_MIN_MAJOR=18

# --- pretty output -----------------------------------------------------------

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m ⚠\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- argument parsing --------------------------------------------------------

CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        -h|--help)
            # Print the leading doc-comment block (skip shebang, stop at code).
            awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) fail "Unknown option: $arg (try --help)" ;;
    esac
done

# --- prerequisite checks -----------------------------------------------------

info "Checking prerequisites"

# Python >= 3.10
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not found. Install Python >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR}:
       Debian/Ubuntu:  sudo apt install python3 python3-venv python3-pip
       Fedora:         sudo dnf install python3 python3-pip
       macOS (brew):   brew install python@3.12"
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (${PY_MIN_MAJOR}, ${PY_MIN_MINOR}) else 1)"; then
    fail "Python ${PY_VER} is too old — need >= ${PY_MIN_MAJOR}.${PY_MIN_MINOR}."
fi
# The venv module ships separately on some distros.
if ! python3 -c "import venv" >/dev/null 2>&1; then
    fail "The python3 'venv' module is missing.
       Debian/Ubuntu:  sudo apt install python3-venv"
fi
ok "python3 ${PY_VER}"

# Node >= 18
if ! command -v node >/dev/null 2>&1; then
    fail "node not found. Install Node >= ${NODE_MIN_MAJOR} (LTS), e.g. via nvm:
       curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
       source ~/.bashrc && nvm install --lts"
fi
NODE_VER="$(node -v | sed 's/^v//')"
NODE_MAJOR="${NODE_VER%%.*}"
if [ "$NODE_MAJOR" -lt "$NODE_MIN_MAJOR" ]; then
    fail "Node ${NODE_VER} is too old — need >= ${NODE_MIN_MAJOR}. Upgrade with 'nvm install --lts'."
fi
ok "node ${NODE_VER}"

# npm
if ! command -v npm >/dev/null 2>&1; then
    fail "npm not found (usually ships with Node). Reinstall Node >= ${NODE_MIN_MAJOR}."
fi
ok "npm $(npm -v)"

# --- connectivity preflight --------------------------------------------------
# 'pip install' and 'npm install' below need to reach PyPI and the npm registry.
# Probe them first and stop early with a clear hint if unreachable, instead of
# letting a proxy/network problem surface as a cryptic timeout deep inside pip
# or npm.
#
# curl/wget honor the same http_proxy/https_proxy variables that pip and npm do,
# so if a proxy is correctly configured this probe succeeds too.
probe_url() {  # returns 0 if reachable
    local url="$1"
    # HEAD request only — never download the body. (pypi.org/simple is the full
    # package index, hundreds of MB; fetching it would blow the timeout.)
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --head --max-time 10 -o /dev/null "$url" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -q --spider -T 10 -t 1 "$url" 2>/dev/null
    else
        return 0  # no probe tool available — skip the check
    fi
}

info "Checking network connectivity"
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    warn "Neither curl nor wget found — skipping connectivity check."
else
    UNREACHABLE=()
    probe_url "https://pypi.org/simple/"          || UNREACHABLE+=("PyPI (pypi.org)")
    probe_url "https://registry.npmjs.org/"        || UNREACHABLE+=("npm registry (registry.npmjs.org)")
    if [ "${#UNREACHABLE[@]}" -gt 0 ]; then
        fail "Could not reach: ${UNREACHABLE[*]}
       Installing dependencies needs internet access to these registries.
       If you are behind a corporate proxy, set the proxy variables and re-run, e.g.:
         export https_proxy=http://proxy.example.com:3128
         export http_proxy=http://proxy.example.com:3128
       (These are also honored by pip and npm.)"
    fi
    ok "Registries reachable"
fi

# --- optional clean ----------------------------------------------------------

if [ "$CLEAN" -eq 1 ]; then
    info "Cleaning previous install"
    # Keep frontend/package-lock.json — it is tracked in git, so deleting it
    # would let npm re-resolve to different versions and dirty the working
    # tree. Removing node_modules is enough to force a clean reinstall against
    # the committed lockfile.
    rm -rf "$VENV_DIR" "$FRONTEND_DIR/node_modules"
    ok "Removed .venv and frontend/node_modules"
fi

# --- backend -----------------------------------------------------------------

info "Setting up the backend (Python)"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    ok "Created virtual environment at .venv"
else
    ok "Reusing existing .venv"
fi

# Use the venv's interpreter directly so we don't depend on 'activate'.
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install -r "$ROOT/requirements.txt"
ok "Backend dependencies installed"

# --- frontend ----------------------------------------------------------------

info "Setting up the frontend (Node)"
# NOTE: use 'npm install', NOT 'npm ci'. 'npm ci' skips optional native deps and
# breaks the rolldown/Vite dev server (see README "Clean install").
( cd "$FRONTEND_DIR" && npm install )
ok "Frontend dependencies installed"

# --- git hooks ---------------------------------------------------------------
# Install the commit-msg hook that prefixes commit messages with the ticket
# number from the branch name (see util/hooks/commit_hook.sh). Skipped when
# there is no .git — e.g. when the sources were downloaded as an archive.
#
# Never fatal: a missing hook must not break a development install.

install_commit_hook() {
    local git_dir hook shim
    git_dir="$(git -C "$ROOT" rev-parse --git-dir 2>/dev/null)" || return 1
    hook="$git_dir/hooks/commit-msg"

    # Resolve the script through the worktree root rather than baking in an
    # absolute path, so the hook survives the repo being moved or renamed.
    shim='#!/bin/bash
exec "$(git rev-parse --show-toplevel)/util/hooks/commit_hook.sh" "$1"'

    if [ ! -f "$hook" ] || [ "$shim" != "$(cat "$hook")" ]; then
        printf '%s\n' "$shim" > "$hook" || return 1
    fi
    [ -x "$hook" ] || chmod +x "$hook" || return 1
    chmod +x "$ROOT/util/hooks/commit_hook.sh" 2>/dev/null || true

    # git treats '#' as the comment character and would strip the '#<number> '
    # prefix from messages written in an editor. Move comments to '%'.
    local cc
    cc="$(git -C "$ROOT" config --get core.commentchar 2>/dev/null || true)"
    if [ -z "$cc" ] || [ "$cc" = "#" ]; then
        git -C "$ROOT" config core.commentchar % || return 1
    fi
}

info "Installing git hooks"
if ! command -v git >/dev/null 2>&1; then
    warn "git not found — skipping commit-msg hook installation."
elif ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    warn "Not a git repository — skipping commit-msg hook installation."
elif install_commit_hook; then
    ok "commit-msg hook installed (branches must be named '<number>_<name>')"
else
    warn "Could not install the commit-msg hook — continuing anyway."
fi

# --- done --------------------------------------------------------------------

echo
ok "Install complete. Start the app with:"
echo "     ./run.sh"
