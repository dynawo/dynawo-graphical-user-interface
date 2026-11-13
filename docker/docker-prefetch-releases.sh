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
# Downloads and extracts the most recent Linux releases listed in a versions
# JSON file. Used by the releases stage of Dockerfile.backend to bake Dynawo
# and DynaFlow Launcher into the image, so first run needs no download.
#
# Usage:  docker-prefetch-releases.sh <versions.json> <destination-dir> [count]
#
# <count> is how many releases to bake, most recent first (default 2): the
# nightly ("master") counts as the most recent, the numbered releases follow in
# descending order. 0 bakes every listed release. The releases left out stay
# in the list the app shows, available for download from the UI.
#
# Only the "Linux" section is read: the Windows archives contain no dynawo.sh
# or dynaflow-launcher.sh, which is what the executable lookups resolve.
#
# Extraction uses unzip rather than Python's zipfile because zipfile discards
# the executable bits and symlinks these releases depend on — see the manual
# restoration that backend/download_manager.py has to do for the same reason.

set -e

JSON="$1"
DEST="$2"
COUNT="${3:-2}"

if [ -z "$JSON" ] || [ -z "$DEST" ]; then
    echo "usage: $0 <versions.json> <destination-dir> [count]" >&2
    exit 2
fi
case "$COUNT" in
    ''|*[!0-9]*) echo "count must be a non-negative integer, not '$COUNT'" >&2; exit 2 ;;
esac

mkdir -p "$DEST"

LIST=$(mktemp)
python3 -c "
import json, re, sys
data = json.load(open(sys.argv[1]))
count = int(sys.argv[2])
releases = [(v, m.get('url')) for v, m in data.get('Linux', {}).items() if m.get('url')]
# Most recent first: master (the nightly) ahead of the numbered releases, those
# by number ('v1.7.1' and '1.7.1' sort alike).
releases.sort(key=lambda r: (r[0] == 'master', tuple(int(p) for p in re.findall(r'\\d+', r[0]))), reverse=True)
for version, url in releases[:count] if count else releases:
    print(version, url)
" "$JSON" "$COUNT" > "$LIST"

if [ ! -s "$LIST" ]; then
    echo "==> no Linux releases listed in $JSON, nothing to prefetch"
    rm -f "$LIST"
    exit 0
fi

# Read from a file rather than a pipe: a `... | while read` loop runs in a
# subshell, where set -e would not fail the build on a failed download.
while read -r version url; do
    [ -n "$version" ] || continue
    echo "==> fetching $version from $url"
    curl -fsSL --retry 3 --retry-delay 5 -o /tmp/release.zip "$url"
    mkdir -p "$DEST/$version"
    unzip -q /tmp/release.zip -d "$DEST/$version"
    rm -f /tmp/release.zip
done < "$LIST"

rm -f "$LIST"

echo "==> extracted into $DEST:"
du -sh "$DEST"/*
