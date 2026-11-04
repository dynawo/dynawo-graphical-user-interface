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
import { fitWidth, ruleSummary, sectionSettings, withAvailability, type Model, type Rule } from './associationRules'

const rule = (extra: Partial<Rule> = {}): Rule => ({
  element_type: 'LOAD', conditions: [], model: 'LoadAlphaBeta', par_source: 'manual', par_file: '',
  parameter_set_id: 'S', ...extra,
})

const TAP_CHANGER: Model = {
  name: 'TapChangerAutomationSystem', description: '', category: 'TapChanger', suggested: true, automation: true,
  fields: [{ name: 'side', label: 'Transformer', values: [{ value: 'NONE', label: 'NONE' }, { value: 'HIGH_VOLTAGE', label: 'HIGH' }] }],
}

describe('folded rule summary', () => {
  it('names the elements, the conditions and the model', () => {
    const summary = ruleSummary(rule({ conditions: [
      { attribute: 'p0', operator: '>=', value: '10' },
      { attribute: '', operator: '=', value: '' },                          // not filled in yet: not shown
    ] }), 'Loads', undefined)

    expect(summary).toBe('Loads where p0 ≥ 10 → LoadAlphaBeta')
  })

  it('shortens long lists of values', () => {
    const ids = ['A', 'B', 'C', 'D', 'E', 'F', 'G']

    expect(ruleSummary(rule({ conditions: [{ attribute: 'id', operator: 'in', value: ids }] }), 'Loads', undefined))
      .toBe('Loads where id in A, B, C, D, E … (7 values) → LoadAlphaBeta')
  })

  it('gives the fields of an automation system, defaults included', () => {
    const tapChanger = rule({ model: 'TapChangerAutomationSystem' })

    expect(ruleSummary(tapChanger, 'Loads', TAP_CHANGER)).toBe('Loads → TapChangerAutomationSystem (transformer NONE)')
    expect(ruleSummary({ ...tapChanger, fields: { side: 'HIGH_VOLTAGE' } }, 'Loads', TAP_CHANGER))
      .toBe('Loads → TapChangerAutomationSystem (transformer HIGH_VOLTAGE)')
    expect(ruleSummary(rule({ model: '' }), 'Loads', undefined)).toBe('Loads → no model')
  })
})

describe('models of the selected Dynawo', () => {
  it('says why a model is not available', () => {
    const models = withAvailability({ dynawo_version: '2.0.0', models: [
      { name: 'LoadAlphaBeta', description: '', category: 'Load', suggested: true, available: true },
      { name: 'LoadPQ', description: '', category: 'Load', suggested: true, available: false },
    ] })

    expect(models.map(m => [m.name, m.unavailable])).toEqual([
      ['LoadAlphaBeta', undefined], ['LoadPQ', 'not available for Dynawo 2.0.0']])
    expect('available' in models[0]).toBe(false)
  })
})

describe('settings saved with rule sets', () => {
  it('keeps the network, solver and time settings only', () => {
    expect(sectionSettings({ reference_jobs: 'case.jobs', solver_lib: 'dynawo_SolverIDA', stop_time: 20 }))
      .toEqual({ solver_lib: 'dynawo_SolverIDA', stop_time: 20 })
  })
})

describe('select width', () => {
  it('fits the text within the bounds', () => {
    expect(fitWidth('abc', 20)).toBe('20ch')
    expect(fitWidth('x'.repeat(30), 20)).toBe('36ch')
    expect(fitWidth('x'.repeat(200), 20)).toBe('90ch')
  })
})
