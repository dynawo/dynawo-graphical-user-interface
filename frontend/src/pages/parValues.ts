//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

/** Whether a value typed in a parameter page is the one of the file, for a
 *  parameter of Dynawo type `type` (BOOL, INT, DOUBLE, STRING). Numbers compare
 *  by value ("1e-3" is "0.001"); a value still to fill is empty, not a number. */
export function valuesEqual(fileVal: string, widgetVal: string, type: string): boolean {
  if (fileVal.trim() === widgetVal.trim() || !fileVal.trim() || !widgetVal.trim())
    return fileVal.trim() === widgetVal.trim()
  if (type === 'BOOL') return fileVal.trim().toLowerCase() === widgetVal.trim().toLowerCase()
  if (type === 'INT') return parseInt(fileVal) === parseInt(widgetVal)
  if (type === 'STRING') return fileVal === widgetVal
  return parseFloat(fileVal) === parseFloat(widgetVal)
}
