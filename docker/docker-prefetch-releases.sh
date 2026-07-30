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
# Downloads and extracts every Linux release listed in a versions JSON file.
# Used by the dynawo stage of Dockerfile.backend to bake Dynawo and DynaFlow
# Launcher into the image, so first run needs no download.
#
# Usage:  docker-prefetch-releases.sh <versions.json> <destination-dir>
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

if [ -z "$JSON" ] || [ -z "$DEST" ]; then
    echo "usage: $0 <versions.json> <destination-dir>" >&2
    exit 2
fi

mkdir -p "$DEST"

LIST=$(mktemp)
python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
for version, meta in data.get('Linux', {}).items():
    url = meta.get('url')
    if url:
        print(version, url)
" "$JSON" > "$LIST"

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
