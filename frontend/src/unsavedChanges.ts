//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// What the current page has not saved, so that leaving it can ask first.

let message: string | null = null

export function setUnsavedChanges(value: string | null): void {
  message = value
}

export function unsavedChanges(): string | null {
  return message
}
