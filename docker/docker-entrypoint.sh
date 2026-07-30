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
# Publishes the Dynawo and DynaFlow Launcher releases baked into the image (see
# the releases stage in Dockerfile.backend) into the directories the app scans
# for downloaded versions, then hands over to the real command.
#
# Both routers treat a version as available when its directory exists and is
# not empty (_is_downloaded in api/routers/dynawo_version.py and
# api/routers/dynaflow_launcher.py), so a symlink is enough and no application
# code needs to know about preinstallation.
#
# Symlinks rather than copies: the targets live in the dynawo-data volume, and
# copying ~1 GB into it on every start would be slow and would pin a stale copy
# there after an image update. Links are re-pointed at the current image
# content on each start instead.

set -e

CONFIG_BASE="${HOME}/.config/dynawo_ihm"

# link_releases <source-dir> <destination-dir>
link_releases() {
    src_base="$1"
    dest="$2"

    [ -d "$src_base" ] || return 0
    mkdir -p "$dest"

    for src in "$src_base"/*; do
        [ -d "$src" ] || continue
        name=$(basename "$src")
        target="$dest/$name"

        # Leave a real directory alone: the user downloaded that version
        # through the UI and it takes precedence over the baked copy.
        if [ -d "$target" ] && [ ! -L "$target" ]; then
            continue
        fi

        # Refresh the link so it follows the image, not whatever an older
        # image version pointed at.
        [ -L "$target" ] && rm -f "$target"
        ln -s "$src" "$target"
    done
}

link_releases /opt/dynawo-preinstalled   "$CONFIG_BASE/dynawo_versions/Linux"
link_releases /opt/dynaflow-preinstalled "$CONFIG_BASE/dynaflow_versions/Linux"

exec "$@"
