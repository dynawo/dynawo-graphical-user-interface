//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Pure helpers of the curves plot on the Run simulation page: no React, no
// Plotly — kept apart from the page so they can be unit-tested.

import type { AxisSide, CurvesData, CurvesInfo, DerivedCurve } from './curvesStore'

// Vertical gap between two stacked subplots, as a fraction of the plot area.
export const SUBPLOT_GAP = 0.09

// Plotly axis names. Each subplot slot owns two y axes: an odd-numbered one for
// its left side and the next even-numbered one overlaying it on the right.
// Slot 0 → y / y2, slot 1 → y3 / y4, ...
export function yAxisNum(slot: number, side: AxisSide): number {
  return 2 * slot + (side === 'left' ? 1 : 2)
}
export function yRef(slot: number, side: AxisSide): string {
  const n = yAxisNum(slot, side)
  return n === 1 ? 'y' : `y${n}`
}
export function yKey(slot: number, side: AxisSide): string {
  const n = yAxisNum(slot, side)
  return n === 1 ? 'yaxis' : `yaxis${n}`
}
// Each subplot gets its own x axis so hover labels stay local to it; they are
// tied together with `matches` so zooming/panning time stays synchronised.
export function xRef(slot: number): string {
  return slot === 0 ? 'x' : `x${slot + 1}`
}
export function xKey(slot: number): string {
  return slot === 0 ? 'xaxis' : `xaxis${slot + 1}`
}

// Top-to-bottom vertical band of the figure occupied by a subplot slot.
export function slotDomain(slot: number, nPlots: number): [number, number] {
  const h = (1 - SUBPLOT_GAP * (nPlots - 1)) / nPlots
  const top = 1 - slot * (h + SUBPLOT_GAP)
  return [Math.max(0, top - h), top]
}

export function label(col: string, info: CurvesInfo): string {
  const p = info[col]
  return p ? `${p[0]} — ${p[1]}` : col
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s`
}

export function computeDerived(dc: DerivedCurve, data: CurvesData): number[] | null {
  const n = data.time.length
  if (dc.op === 'scale' && dc.base && dc.base in data.signals) {
    const f = dc.factor ?? 1
    return data.signals[dc.base].map(v => v * f)
  }
  if ((dc.op === 'sum' || dc.op === 'diff') && dc.picks.length > 0) {
    const result = new Array<number>(n).fill(0)
    dc.picks.forEach((col, idx) => {
      const sign = dc.op === 'diff' && idx > 0 ? -1 : 1
      const vals = data.signals[col]
      if (vals) vals.forEach((v, i) => { result[i] += sign * v })
    })
    return result
  }
  return null
}
