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
# start.sh — build if needed and start the Dynawo GUI in Docker.
#
# The container image carries Python, Node, Dynawo and DynaFlow Launcher, so
# nothing has to be installed on the host beyond Docker itself. This is the
# containerised counterpart of install.sh + run.sh, which set up and start the
# application natively instead.
#
# Prerequisites are checked but never installed automatically — if something is
# missing the script prints exactly how to fix it and exits.
#
# Usage:
#   ./docker/start.sh            # build if needed, start, open the browser
#   ./docker/start.sh --rebuild  # force a rebuild even if images exist
#   ./docker/start.sh --no-open  # do not launch a browser
#
# Settings come from the environment or a .env file at the repository root:
#   GUI_PORT             host port to serve on            (default 8080)
#   SESSION_TTL          backend session lifetime         (default 3600)
#   MAX_CONCURRENT_SIMS  parallel simulation limit        (default 4)
#   HTTP_PROXY/HTTPS_PROXY/NO_PROXY   inherited for builds and for the app
#
# Stop it again with ./docker/stop.sh

set -euo pipefail

# The repository root is the parent of this script's directory, so the script
# works from any current directory (including a double-clicked launcher).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

MIN_FREE_GB=8

# --- pretty output -----------------------------------------------------------

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m ⚠\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31m ✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- argument parsing --------------------------------------------------------

REBUILD=0
OPEN_BROWSER=1
for arg in "$@"; do
    case "$arg" in
        --rebuild) REBUILD=1 ;;
        --no-open) OPEN_BROWSER=0 ;;
        -h|--help)
            # Print the leading doc-comment block (skip shebang, stop at code).
            awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *) fail "Unknown option: $arg (try --help)" ;;
    esac
done

# --- prerequisites -----------------------------------------------------------

info "Checking prerequisites"

if ! command -v docker >/dev/null 2>&1; then
    fail "docker not found. Install one of:
       Docker Engine   https://docs.docker.com/engine/install/
       Docker Desktop  https://www.docker.com/products/docker-desktop/
       Rancher Desktop https://rancherdesktop.io/
     Docker Desktop needs a paid subscription in larger organisations;
     Rancher Desktop and Podman Desktop do not."
fi

# 'docker --version' answers from the client alone, so it succeeds even when
# the daemon is down. Only 'docker info' proves the engine is actually up.
if ! docker info >/dev/null 2>&1; then
    fail "The Docker daemon is not responding.
       Docker Desktop: start it and wait until the whale icon stops animating.
       Linux service:  sudo systemctl start docker
       Permissions:    if this says 'permission denied', add yourself to the
                       docker group (sudo usermod -aG docker \$USER) and log
                       out and back in."
fi
ok "docker $(docker version --format '{{.Client.Version}}' 2>/dev/null || echo '')"

# Compose v2 is a docker subcommand; v1 is a separate binary. Support both.
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    fail "Docker Compose not found.
       It ships with Docker Desktop and Rancher Desktop. On a plain Docker
       Engine install, add the plugin:
         sudo apt install docker-compose-plugin      # Debian/Ubuntu
       or see https://docs.docker.com/compose/install/"
fi
ok "compose available (${COMPOSE[*]})"

# Images and build cache land in the daemon's data directory, not in this
# repository, so check the free space where they will actually go.
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
if [ -d "$DOCKER_ROOT" ]; then
    FREE_GB="$(df -Pk "$DOCKER_ROOT" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}')"
    if [ -n "${FREE_GB:-}" ] && [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
        warn "Only ${FREE_GB} GB free in ${DOCKER_ROOT} — the images need about ${MIN_FREE_GB} GB."
        warn "Reclaim space with: docker system prune -a"
        warn "(never add --volumes: that deletes the downloaded simulation data)"
    else
        ok "${FREE_GB:-?} GB free in ${DOCKER_ROOT}"
    fi
fi

# --- do we need to build? ----------------------------------------------------

# Once the images exist, starting needs no network at all: Dynawo and DynaFlow
# Launcher are baked in, so the app downloads nothing. Only probe connectivity
# when a build is actually going to run.
NEEDS_BUILD=1
if [ "$REBUILD" -eq 0 ] && "${COMPOSE[@]}" images -q 2>/dev/null | grep -q .; then
    NEEDS_BUILD=0
fi

# --- connectivity preflight (only before a build) ----------------------------
# The build fetches Python wheels, npm packages and the simulation engines.
# curl/wget honour the same http_proxy/https_proxy variables that pip, npm and
# Docker's build arguments do, so a passing probe means the build will reach
# them too. Probe first and stop with a clear hint, instead of letting a
# proxy problem surface as a cryptic timeout deep inside the build.

probe_url() {  # returns 0 if reachable
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --head --max-time 10 -o /dev/null "$url" 2>/dev/null
    elif command -v wget >/dev/null 2>&1; then
        wget -q --spider -T 10 -t 1 "$url" 2>/dev/null
    else
        return 0  # no probe tool available — skip the check
    fi
}

if [ "$NEEDS_BUILD" -eq 1 ]; then
    info "Checking network connectivity (needed to build the images)"
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        warn "Neither curl nor wget found — skipping the connectivity check."
    else
        UNREACHABLE=()
        probe_url "https://pypi.org/simple/"     || UNREACHABLE+=("PyPI (pypi.org)")
        probe_url "https://registry.npmjs.org/"  || UNREACHABLE+=("npm registry (registry.npmjs.org)")
        probe_url "https://github.com/"          || UNREACHABLE+=("GitHub (github.com)")
        if [ "${#UNREACHABLE[@]}" -gt 0 ]; then
            fail "Could not reach: ${UNREACHABLE[*]}
       Building the images needs access to these. If you are behind a
       corporate proxy, set the proxy variables and re-run, e.g.:
         export https_proxy=http://proxy.example.com:3128
         export http_proxy=http://proxy.example.com:3128
       These are passed through to the build and to the application."
        fi
        ok "Registries reachable"
    fi
fi

# --- settings ----------------------------------------------------------------

# Mirror how Compose resolves GUI_PORT: the environment wins over .env, and
# 8080 is the fallback declared in docker-compose.yml.
PORT="${GUI_PORT:-}"
if [ -z "$PORT" ] && [ -f "$ROOT/.env" ]; then
    PORT="$(sed -n 's/^[[:space:]]*GUI_PORT[[:space:]]*=[[:space:]]*\([^[:space:]#]*\).*/\1/p' "$ROOT/.env" | tail -1)"
fi
PORT="${PORT:-8080}"
URL="http://localhost:${PORT}"

# --- build and start ---------------------------------------------------------

if [ "$NEEDS_BUILD" -eq 1 ]; then
    info "Building the images — first time takes 10-25 minutes"
else
    info "Starting (images already built; pass --rebuild to force a rebuild)"
fi

if ! "${COMPOSE[@]}" up -d --build; then
    echo >&2
    fail "Startup failed. Inspect the output above, or:
         ${COMPOSE[*]} logs backend
         ${COMPOSE[*]} logs web

       If it failed while pulling a base image, note that the Docker daemon
       has its own proxy configuration, separate from this shell — setting
       HTTP_PROXY here does not affect it:
         Docker Desktop : Settings -> Resources -> Proxies
         Linux service  : /etc/systemd/system/docker.service.d/proxy.conf
                          then sudo systemctl daemon-reload
                               sudo systemctl restart docker"
fi

# --- wait until it answers ---------------------------------------------------
# The backend runs a healthcheck and Compose v2 holds the web container back
# until it passes, but v1 ignores that — so poll the published port either way
# rather than reporting success before the app can serve a request.

info "Waiting for the application to come up..."
READY=0
for _ in $(seq 1 180); do
    if command -v curl >/dev/null 2>&1; then
        if curl -fsS --max-time 2 -o /dev/null "$URL" 2>/dev/null; then READY=1; break; fi
    else
        # No curl: fall back to a plain TCP connect via bash's /dev/tcp.
        if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
            exec 3>&- 2>/dev/null || true
            READY=1; break
        fi
    fi
    sleep 1
done

echo
if [ "$READY" -eq 1 ]; then
    printf '\033[1;32m════════════════════════════════════════════════════════\033[0m\n'
    printf '\033[1;32m  ✓  Dynawo GUI is running\033[0m\n\n'
    printf '     Open  \033[1;36m%s\033[0m  in your browser\n\n' "$URL"
    printf '     Stop it with       ./docker/stop.sh\n'
    printf '     Follow the logs    %s logs -f\n' "${COMPOSE[*]}"
    printf '\033[1;32m════════════════════════════════════════════════════════\033[0m\n'
else
    warn "The application did not answer on port ${PORT} within 3 minutes."
    warn "The containers may still be starting. Check with:"
    warn "  ${COMPOSE[*]} ps"
    warn "  ${COMPOSE[*]} logs backend"
    exit 1
fi
echo

# --- open a browser ----------------------------------------------------------

if [ "$OPEN_BROWSER" -eq 1 ]; then
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 || true   # macOS
    fi
fi
