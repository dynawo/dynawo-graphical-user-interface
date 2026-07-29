//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

export type DashStyle = 'solid' | 'dash' | 'dot' | 'dashdot'

export interface DerivedCurve {
  name: string
  op: 'sum' | 'diff' | 'scale'
  picks: string[]
  base?: string
  factor?: number
}

export interface CurvesInfo { [col: string]: [string, string] }
export interface CurvesData { time: number[]; signals: { [col: string]: number[] } }

// ── sessionStorage persistence for user selections ────────────────────────────

const SS_KEY = 'dynawo_curves'

interface Saved {
  selectedRuns:   number[]
  selectedCols:   string[]
  dashStyles:     Record<string, DashStyle>
  derivedCurves:  DerivedCurve[]
  selectedDerived: number[]
  openModels:     string[]
  panelWidth?:    number
}

export function loadCurves(): Saved | null {
  try {
    const raw = sessionStorage.getItem(SS_KEY)
    const parsed = JSON.parse(raw ?? 'null')
    console.log('[curves] LOAD', parsed)
    return parsed
  } catch (e) {
    console.log('[curves] LOAD failed', e)
    return null
  }
}

export function saveCurves(s: Saved) {
  try {
    sessionStorage.setItem(SS_KEY, JSON.stringify(s))
    console.log('[curves] SAVE', s)
  } catch (e) {
    console.log('[curves] SAVE failed', e)
  }
}

// ── In-memory cache for large data (not serialised) ───────────────────────────

export const curvesCache: {
  runData:    Record<number, CurvesData>
  curvesInfo: CurvesInfo
} = { runData: {}, curvesInfo: {} }
