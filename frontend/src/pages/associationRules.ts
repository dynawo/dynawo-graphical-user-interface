//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Rules of the Dynamic Association page and what the page shows of them.

export type ParSource = 'file' | 'generic' | 'manual'

export interface Condition { attribute: string; operator: string; value: string | string[] }
export interface Rule {
  element_type: string
  conditions: Condition[]
  model: string
  par_source: ParSource
  par_file: string
  parameter_set_id: string
  // Automation systems: their other fields (e.g. side); absent ones take the default.
  fields?: Record<string, string>
}

// custom: added by the user, shared (all sessions) or live (this session).
// unavailable: why the selected Dynawo cannot use it (not in its ddb).
// Automation-system field with its values, the first being the default.
export interface AutomationField { name: string; label: string; values: { value: string; label: string }[] }
export interface Model {
  name: string; description: string; category: string; suggested: boolean; custom?: 'shared' | 'live'; unavailable?: string
  // Automation systems: their fields, what they need on the element, their id prefix.
  automation?: boolean; fields?: AutomationField[]; requires?: string | null; id_prefix?: string; note?: string | null
}
export interface ModelsResponse { dynawo_version: string | null; models: (Model & { available?: boolean })[] }

export const withAvailability = (res: ModelsResponse): Model[] => res.models.map(({ available, ...m }) =>
  available === false ? { ...m, unavailable: `not available for Dynawo ${res.dynawo_version}` } : m)

export interface GenerationOptions {
  reference_jobs: string | null
  network_par_file: string | null
  network_par_id: string | null
  solver_lib: string | null
  solver_par_file: string | null
  solver_par_id: string | null
  start_time: number | null
  stop_time: number | null
}

export const OPERATOR_LABEL: Record<string, string> = {
  '=': '=', '!=': '≠', '<': '<', '<=': '≤', '>': '>', '>=': '≥', in: 'in', matches: 'matches',
}

// Width of a select fitting its value, within [min, max] characters.
export const fitWidth = (text: string, min: number, max = 90) =>
  `${Math.min(max, Math.max(min, text.length + 6))}ch`

// One line describing a folded rule: element type, conditions, model and its fields.
export function ruleSummary(rule: Rule, typeLabel: string, model: Model | undefined): string {
  const conditions = rule.conditions
    .filter(c => c.attribute)
    .map(c => `${c.attribute} ${OPERATOR_LABEL[c.operator] ?? c.operator} ${Array.isArray(c.value)
      ? c.value.slice(0, 5).join(', ') + (c.value.length > 5 ? ` … (${c.value.length} values)` : '')
      : c.value}`)
  const fields = (model?.fields ?? []).map(f => `${f.label.toLowerCase()} ${rule.fields?.[f.name] || f.values[0].value}`)
  return `${typeLabel}${conditions.length ? ` where ${conditions.join(' and ')}` : ''} → ${rule.model || 'no model'}`
    + (fields.length ? ` (${fields.join(', ')})` : '')
}

// The "Network and solver parameters, time window" settings, saved with rule sets.
export const SECTION_SETTINGS = ['network_par_file', 'network_par_id', 'solver_lib', 'solver_par_file', 'solver_par_id', 'start_time', 'stop_time'] as const
export const sectionSettings = (o: Partial<GenerationOptions>): Partial<GenerationOptions> =>
  Object.fromEntries(SECTION_SETTINGS.filter(k => k in o).map(k => [k, o[k]]))
