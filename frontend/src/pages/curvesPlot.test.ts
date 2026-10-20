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
import {
  SUBPLOT_GAP, computeDerived, formatDuration, label, slotDomain, xKey, xRef, yKey, yRef,
} from './curvesPlot'
import type { CurvesData } from './curvesStore'

describe('Plotly axis names', () => {
  it('gives each subplot a left y axis and a right one overlaying it', () => {
    expect([yRef(0, 'left'), yRef(0, 'right'), yRef(1, 'left'), yRef(1, 'right')])
      .toEqual(['y', 'y2', 'y3', 'y4'])
    expect([yKey(0, 'left'), yKey(0, 'right'), yKey(1, 'left')])
      .toEqual(['yaxis', 'yaxis2', 'yaxis3'])
  })

  it('gives each subplot its own x axis', () => {
    expect([xRef(0), xRef(1), xRef(2)]).toEqual(['x', 'x2', 'x3'])
    expect([xKey(0), xKey(1), xKey(2)]).toEqual(['xaxis', 'xaxis2', 'xaxis3'])
  })
})

describe('slotDomain', () => {
  it('gives a single plot the whole height', () => {
    expect(slotDomain(0, 1)).toEqual([0, 1])
  })

  it('stacks plots top to bottom, a gap apart, without overlap', () => {
    const n = 3
    const domains = [0, 1, 2].map(slot => slotDomain(slot, n))

    expect(domains[0][1]).toBe(1)
    expect(domains[n - 1][0]).toBeCloseTo(0)
    for (let i = 1; i < n; i++) {
      expect(domains[i - 1][0] - domains[i][1]).toBeCloseTo(SUBPLOT_GAP)
    }
  })
})

describe('label', () => {
  it('names a column after its model and variable', () => {
    expect(label('GEN1_generator_omegaPu', { GEN1_generator_omegaPu: ['GEN1', 'generator_omegaPu'] }))
      .toBe('GEN1 — generator_omegaPu')
  })

  it('falls back to the column name when the .crv does not describe it', () => {
    expect(label('time', {})).toBe('time')
  })
})

describe('formatDuration', () => {
  it.each([
    [5.2, '5.2s'],
    [59.94, '59.9s'],
    [60, '1m 0s'],
    [125.7, '2m 5s'],
  ])('%s seconds reads %s', (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text)
  })
})

describe('computeDerived', () => {
  const data: CurvesData = {
    time: [0, 1, 2],
    signals: { A: [1, 2, 3], B: [10, 20, 30], C: [100, 100, 100] },
  }

  it('sums the picked curves', () => {
    expect(computeDerived({ name: 's', op: 'sum', picks: ['A', 'B', 'C'] }, data)).toEqual([111, 122, 133])
  })

  it('subtracts every other picked curve from the first', () => {
    expect(computeDerived({ name: 'd', op: 'diff', picks: ['C', 'A', 'B'] }, data)).toEqual([89, 78, 67])
  })

  it('scales a curve by its factor, 1 when none is given', () => {
    expect(computeDerived({ name: 'k', op: 'scale', picks: [], base: 'A', factor: -2 }, data)).toEqual([-2, -4, -6])
    expect(computeDerived({ name: 'k', op: 'scale', picks: [], base: 'A' }, data)).toEqual([1, 2, 3])
  })

  it('counts a curve missing from this run as zero', () => {
    // A derived curve defined on one run is also drawn for the others.
    expect(computeDerived({ name: 's', op: 'sum', picks: ['A', 'NOT_IN_THIS_RUN'] }, data)).toEqual([1, 2, 3])
  })

  it('gives nothing when there is nothing to compute', () => {
    expect(computeDerived({ name: 's', op: 'sum', picks: [] }, data)).toBeNull()
    expect(computeDerived({ name: 'k', op: 'scale', picks: [], base: 'NOT_IN_THIS_RUN' }, data)).toBeNull()
  })
})
