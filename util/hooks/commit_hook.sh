#!/bin/bash
#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#
# commit-msg hook — prefix the commit message with the ticket number carried by
# the branch name, so that commits auto-link to their GitHub issue.
#
# Branches must be named "<number>_<whatever>" (e.g. 1_dockerfile_Windows_OS).
# A commit on such a branch whose message does not already start with '#' gets
# "#<number> " prepended automatically.
#
# Adapted from dynawo/dynawo:util/hooks/commit_hook.sh. Same policy and same
# messages; the branch/cherry-pick detection is done with porcelain-independent
# git commands instead of grepping 'git branch' and 'git status' output, which
# is translated when git runs under a non-English locale.
#
# Installed by install.sh as .git/hooks/commit-msg. Requires 'core.commentchar'
# to be something other than '#', otherwise git strips the '#<number> ' line as
# a comment when the message is written through an editor.

set -u

msg_file="$1"

# Detached HEAD (also the case mid-rebase): no branch to read a ticket from.
branch_name=$(git symbolic-ref --quiet --short HEAD) || exit 0

# During a cherry-pick the message comes from the original commit and already
# carries its own ticket number — leave it alone.
if [ -f "$(git rev-parse --git-path CHERRY_PICK_HEAD)" ]; then
  exit 0
fi

if [ -z "$(echo "$branch_name" | grep -E "^[0-9]+_[^_]*")" ]; then
  echo "[POLICY] Invalid branch name: branch should be a number followed by a '_' and then whatever you wish."
  exit 1
fi

ticket_num=$(echo "$branch_name" | cut -d '_' -f 1)

if [[ "$(cat "$msg_file")" != \#* ]]; then
  # No '#' prefix yet: add it.
  (echo -n "#$ticket_num "; cat "$msg_file") > "$msg_file.tmp"
  mv "$msg_file.tmp" "$msg_file"
  exit 0
fi

hashtag_message=$(grep -Eo "^#[0-9]+ " "$msg_file")
if [ -z "$hashtag_message" ]; then
  echo "[POLICY] Either your commit message should start with # followed by the ticket number AND a space, or by something entirely different (in which case the ticket number is added automatically). You can't start with a # followed by anything else than the ticket number."
  exit 1
fi

num_in_message=$(echo "$hashtag_message" | grep -Eo "[0-9]+")
if [ "$num_in_message" != "$ticket_num" ]; then
  echo "[WARNING] The ticket number given (namely $num_in_message) is not the same as the one suggested by the branch name (namely $ticket_num))."
fi
