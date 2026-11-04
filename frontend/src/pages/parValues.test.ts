//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { describe, expect, it } from 'vitest'
import { valuesEqual } from './parValues'

describe('parameter value comparison', () => {
  it('compares numbers by value', () => {
    expect(valuesEqual('1e-3', '0.001', 'DOUBLE')).toBe(true)
    expect(valuesEqual('2', '2.0', 'INT')).toBe(true)
    expect(valuesEqual('2', '3', 'DOUBLE')).toBe(false)
  })

  it('takes two values still to fill as unchanged', () => {
    // Both empty: not NaN against NaN, which would always look modified.
    expect(valuesEqual('', '', 'DOUBLE')).toBe(true)
    expect(valuesEqual('', '  ', 'INT')).toBe(true)
  })

  it('takes a value typed into an empty parameter, or cleared, as a change', () => {
    expect(valuesEqual('', '1', 'DOUBLE')).toBe(false)
    expect(valuesEqual('1', '', 'DOUBLE')).toBe(false)
  })

  it('compares booleans without case and strings exactly', () => {
    expect(valuesEqual('True', 'true', 'BOOL')).toBe(true)
    expect(valuesEqual('ABC', 'abc', 'STRING')).toBe(false)
    expect(valuesEqual('1e-3', '0.001', 'STRING')).toBe(false)
  })
})
