//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// @vitest-environment jsdom
// (the store persists to sessionStorage, which only a browser-like environment has)

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadCurves, saveCurves } from './curvesStore'

describe('curves selection persistence', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.spyOn(console, 'log').mockImplementation(() => {})  // the store logs every load/save
  })

  it('gives back what was saved', () => {
    const saved = {
      selectedRuns: [1, 3],
      selectedCols: ['GEN1_generator_omegaPu'],
      dashStyles: { GEN1_generator_omegaPu: 'dash' as const },
      placements: { GEN1_generator_omegaPu: { plot: 1, side: 'right' as const } },
      derivedCurves: [{ name: 'd', op: 'diff' as const, picks: ['A', 'B'] }],
      selectedDerived: [0],
      openModels: ['GEN1'],
      panelWidth: 320,
    }

    saveCurves(saved)

    expect(loadCurves()).toEqual(saved)
  })

  it('gives nothing before anything was saved', () => {
    expect(loadCurves()).toBeNull()
  })

  it('gives nothing, rather than failing, when what is stored is corrupt', () => {
    sessionStorage.setItem('dynawo_curves', '{not json')

    expect(loadCurves()).toBeNull()
  })
})
