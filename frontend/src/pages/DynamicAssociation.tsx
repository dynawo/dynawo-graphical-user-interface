//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Collapse, Input, InputNumber, Modal, Pagination, Popconfirm, Radio, Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, CodeOutlined, CopyOutlined, DeleteOutlined, DownOutlined, FolderOpenOutlined, ImportOutlined, PlusOutlined, RightOutlined, SaveOutlined, ThunderboltOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail, errorStatus, retryTransient } from '../api/errors'

const { Title, Text, Paragraph } = Typography

// Dynamic association: rules putting a model on IIDM elements, applied in
// order. Rules and settings are saved with the session. A rule with an
// automation-system model adds one automation system per element.

type ParSource = 'file' | 'generic' | 'manual'

interface Condition { attribute: string; operator: string; value: string | string[] }
interface Rule {
  element_type: string
  conditions: Condition[]
  model: string
  par_source: ParSource
  par_file: string
  parameter_set_id: string
  // Automation systems: their other fields (e.g. side); absent ones take the default.
  fields?: Record<string, string>
}
// Page-only key, stable when rules are moved or removed.
type UiRule = Rule & { _key: number }

interface ElementType { id: string; label: string }
interface Attribute { name: string; kind: 'number' | 'boolean' | 'text'; operators: string[]; values: string[] | null }
// custom: added by the user, shared (all sessions) or live (this session).
// unavailable: why the selected Dynawo cannot use it (not in its ddb).
// Automation-system field with its values, the first being the default.
interface AutomationField { name: string; label: string; values: { value: string; label: string }[] }
interface Model {
  name: string; description: string; category: string; suggested: boolean; custom?: 'shared' | 'live'; unavailable?: string
  // Automation systems: their fields, what they need on the element, their id prefix.
  automation?: boolean; fields?: AutomationField[]; requires?: string | null; id_prefix?: string; note?: string | null
}
interface ModelsResponse { dynawo_version: string | null; models: (Model & { available?: boolean })[] }

const withAvailability = (res: ModelsResponse): Model[] => res.models.map(({ available, ...m }) =>
  available === false ? { ...m, unavailable: `not available for Dynawo ${res.dynawo_version}` } : m)
interface CustomModel { lib: string; category: string; dynawo_version: string; shared: boolean }
interface ParFile { name: string; sets: string[] }
interface ParamSources { files: ParFile[]; generic: { available: boolean; sets: string[] } }
interface RuleReport { index: number; matched: number; applied: number; errors: string[]; warnings: string[] }
interface Assignment {
  element_type: string; static_id: string; model: string; category: string; rule: number
  automation?: boolean; dynamic_model_id?: string
}
// unmatched: the first ids only; unmatched_count: all of them.
interface TypeSummary { id: string; label: string; total: number; unmatched_count: number; unmatched: string[] }
interface ModelCount { model: string; category: string; automation: boolean; count: number }
interface Resolution { rules: RuleReport[]; models: ModelCount[]; element_types: TypeSummary[] }
interface Context {
  network_file: string | null
  dynawo: string | null
  jobs_files: string[]
  dyd_files: string[]
  default_reference_jobs: string | null
  generated_files: string[]
  script_available: boolean
  options: Partial<GenerationOptions>
  last_result: GenerateResult | null
}
// Generate card settings; null means "from the reference job" or default.
interface GenerationOptions {
  reference_jobs: string | null
  network_par_file: string | null
  network_par_id: string | null
  solver_lib: string | null
  solver_par_file: string | null
  solver_par_id: string | null
  start_time: number | null
  stop_time: number | null
}
const NO_OPTIONS: GenerationOptions = {
  reference_jobs: null, network_par_file: null, network_par_id: null,
  solver_lib: null, solver_par_file: null, solver_par_id: null, start_time: null, stop_time: null,
}
interface Dropped extends Assignment { reason: string }
// needed_by_powsybl: values given placeholders; regenerate once filled.
interface ToFill { file: string; set: string; for: 'models' | 'network' | 'solver'; needed_by_powsybl: string[] }
interface GenerateResult {
  files: string[]
  jobs: string
  reference_jobs: string | null
  dynawo_version: string
  rules: RuleReport[]
  assigned: number
  automation_systems: number
  dropped: Dropped[]
  to_fill: ToFill[]
  report: string
}

const OPERATOR_LABEL: Record<string, string> = {
  '=': '=', '!=': '≠', '<': '<', '<=': '≤', '>': '>', '>=': '≥', in: 'in', matches: 'matches',
}
const SOLVERS = [
  { value: 'dynawo_SolverIDA', label: 'IDA (dynawo_SolverIDA)' },
  { value: 'dynawo_SolverSIM', label: 'SIM (dynawo_SolverSIM)' },
]

// Rules shown per page, and values shown in a folded rule or a condition.
const RULES_PER_PAGE = 20
const SHOWN_VALUES = 30

// Model dropdown width (px), fitting the longest model names. A number keeps
// the dropdown virtual, unlike `false`.
const MODEL_POPUP_WIDTH = 640

// Width of a select fitting its value, within [min, max] characters.
const fitWidth = (text: string, min: number, max = 90) =>
  `${Math.min(max, Math.max(min, text.length + 6))}ch`

let nextKey = 1
const withKey = (rule: Rule): UiRule => ({ ...rule, _key: nextKey++ })
const withoutKey = ({ _key, ...rule }: UiRule): Rule => { void _key; return rule }

function newRule(elementType: string, parFiles: ParFile[]): UiRule {
  return withKey({
    element_type: elementType, conditions: [], model: '',
    par_source: 'file', par_file: parFiles[0]?.name ?? '', parameter_set_id: '', fields: {},
  })
}

// ── One condition ─────────────────────────────────────────────────────────────

function ConditionRow({ elementType, condition, attributes, notConnected, onChange, onRemove }: {
  elementType: string
  condition: Condition
  attributes: Attribute[]
  notConnected: Record<string, string>
  onChange: (c: Condition) => void
  onRemove: () => void
}) {
  const attr = attributes.find(a => a.name === condition.attribute)
  const listValues = attr?.kind === 'boolean' ? ['true', 'false'] : attr?.values ?? null
  // Text attributes with too many values to list (e.g. ids) are searched on the server.
  const searched = attr?.kind === 'text' && !attr.values
  const [found, setFound] = useState<string[]>([])
  const searchTimer = useRef<number | undefined>(undefined)
  const search = (text: string) => {
    window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => {
      client.get<{ values: string[] }>(`/dynamic-association/element-types/${elementType}/values`,
        { params: { attribute: condition.attribute, search: text, limit: 50 } })
        .then(res => setFound(res.data.values))
        .catch(() => setFound([]))
    }, 250)
  }
  const searchProps = searched
    ? { onSearch: search, onFocus: () => search(''), filterOption: false, notFoundContent: 'type part of a value' }
    : {}
  const proposed = searched ? found : listValues ?? []
  // Ids of elements that are not connected are greyed out, with the reason.
  const valueOption = (v: string) => {
    const note = condition.attribute === 'id' ? notConnected[v] : undefined
    return { value: v, label: note ? <Text type="secondary">{v} <Text type="warning">· {note}</Text></Text> : v }
  }

  const setAttribute = (name: string) => {
    const next = attributes.find(a => a.name === name)
    const operator = next?.operators[0] ?? '='
    onChange({ attribute: name, operator, value: operator === 'in' ? [] : '' })
  }
  const setOperator = (operator: string) => {
    const value = condition.value
    if (operator === 'in') onChange({ ...condition, operator, value: Array.isArray(value) ? value : value ? [value] : [] })
    else onChange({ ...condition, operator, value: Array.isArray(value) ? value[0] ?? '' : value })
  }

  let valueWidget
  if (condition.operator === 'in') {
    valueWidget = (
      // Proposed values are picked; free entry ("tags") only for numbers.
      <Select mode={searched || listValues ? 'multiple' : 'tags'} style={{ minWidth: 220 }} maxTagCount={SHOWN_VALUES}
        placeholder={searched ? 'type to search values' : 'values'}
        value={Array.isArray(condition.value) ? condition.value : []}
        options={proposed.map(valueOption)} {...searchProps}
        onChange={v => onChange({ ...condition, value: v })} />
    )
  } else if ((listValues || searched) && condition.operator !== 'matches') {
    valueWidget = (
      <Select showSearch style={{ minWidth: 220 }} placeholder={searched ? 'type to search a value' : 'value'}
        value={typeof condition.value === 'string' && condition.value ? condition.value : undefined}
        options={proposed.map(valueOption)} {...searchProps}
        onChange={v => onChange({ ...condition, value: v })} />
    )
  } else {
    valueWidget = (
      <Input style={{ width: 220 }}
        placeholder={condition.operator === 'matches' ? 'regular expression, e.g. ^GEN' : attr?.kind === 'number' ? 'number' : 'value'}
        value={typeof condition.value === 'string' ? condition.value : ''}
        onChange={e => onChange({ ...condition, value: e.target.value })} />
    )
  }

  return (
    <Space wrap>
      <Select showSearch style={{ width: 240 }} placeholder="attribute"
        value={condition.attribute || undefined}
        options={attributes.map(a => ({ value: a.name, label: `${a.name}  (${a.kind})` }))}
        onChange={setAttribute} />
      <Select style={{ width: 110 }} value={condition.operator}
        options={(attr?.operators ?? Object.keys(OPERATOR_LABEL)).map(op => ({ value: op, label: OPERATOR_LABEL[op] ?? op }))}
        onChange={setOperator} />
      {valueWidget}
      <Tooltip title="Remove this condition">
        <Button type="text" icon={<DeleteOutlined />} onClick={onRemove} />
      </Tooltip>
    </Space>
  )
}

// One line describing a folded rule: element type, conditions, model and its fields.
function ruleSummary(rule: Rule, typeLabel: string, model: Model | undefined): string {
  const conditions = rule.conditions
    .filter(c => c.attribute)
    .map(c => `${c.attribute} ${OPERATOR_LABEL[c.operator] ?? c.operator} ${Array.isArray(c.value)
      ? c.value.slice(0, 5).join(', ') + (c.value.length > 5 ? ` … (${c.value.length} values)` : '')
      : c.value}`)
  const fields = (model?.fields ?? []).map(f => `${f.label.toLowerCase()} ${rule.fields?.[f.name] || f.values[0].value}`)
  return `${typeLabel}${conditions.length ? ` where ${conditions.join(' and ')}` : ''} → ${rule.model || 'no model'}`
    + (fields.length ? ` (${fields.join(', ')})` : '')
}

// ── One rule ──────────────────────────────────────────────────────────────────

function RuleCard({ rule, index, count, report, elementTypes, attributes, notConnected, models, sources, onChange, onMove, onDuplicate, onRemove, onAddModel, expanded, onToggle }: {
  rule: UiRule
  index: number
  count: number
  report: RuleReport | undefined
  elementTypes: ElementType[]
  attributes: Attribute[]
  notConnected: Record<string, string>
  models: Model[]
  sources: ParamSources | null
  onChange: (r: UiRule) => void
  onMove: (delta: number) => void
  onDuplicate: () => void
  onRemove: () => void
  onAddModel: () => void
  expanded: boolean
  onToggle: () => void
}) {
  const typeLabel = elementTypes.find(t => t.id === rule.element_type)?.label ?? rule.element_type
  const modelOptions = useMemo(() => {
    const option = (m: Model) => ({
      value: m.name,
      disabled: !!m.unavailable,
      label: <span title={m.description}>{m.name} <Text type="secondary">· {m.category}{m.custom ? ' · added' : ''}</Text>
        {m.unavailable && <Text type="warning"> · {m.unavailable}</Text>}</span>,
    })
    const automations = models.filter(m => m.automation)
    return [
      { label: `Suggested for ${typeLabel.toLowerCase()}`, options: models.filter(m => m.suggested && !m.automation).map(option) },
      { label: 'Other models', options: models.filter(m => !m.suggested).map(option) },
      ...(automations.length ? [{ label: `Automation systems on ${typeLabel.toLowerCase()}`, options: automations.map(option) }] : []),
    ]
  }, [models, typeLabel])
  const parFile = sources?.files.find(f => f.name === rule.par_file)
  const selectedModel = models.find(m => m.name === rule.model)
  const category = selectedModel?.category
  const modelLabel = category ? `${rule.model} · ${category}${selectedModel?.custom ? ' · added' : ''}` : rule.model

  const setCondition = (i: number, c: Condition) =>
    onChange({ ...rule, conditions: rule.conditions.map((x, j) => (j === i ? c : x)) })

  return (
    <Card size="small" style={{ marginBottom: 12 }} styles={{ body: expanded ? undefined : { display: 'none' } }}
      title={<Space onClick={onToggle} style={{ cursor: 'pointer', maxWidth: '100%' }}>
        {expanded ? <DownOutlined /> : <RightOutlined />}
        <Text strong>Rule {index + 1}</Text>
        {report && !report.errors.length && (
          <Tag color={report.applied ? 'blue' : 'default'}>
            {report.applied} element(s){report.matched !== report.applied ? ` — ${report.matched - report.applied} taken by later rules` : ''}
          </Tag>
        )}
        {report?.errors.length ? <Tag color="red">skipped</Tag> : null}
        {!expanded && report?.warnings.length ? <Tag color="orange">{report.warnings.length} warning(s)</Tag> : null}
        {!expanded && <Text type="secondary" ellipsis style={{ maxWidth: 640 }}>{ruleSummary(rule, typeLabel, selectedModel)}</Text>}
      </Space>}
      extra={<Space size={0}>
        <Tooltip title="Move up (an earlier rule gives way to a later one)">
          <Button type="text" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => onMove(-1)} />
        </Tooltip>
        <Tooltip title="Move down">
          <Button type="text" icon={<ArrowDownOutlined />} disabled={index === count - 1} onClick={() => onMove(1)} />
        </Tooltip>
        <Tooltip title="Duplicate (the copy is added just after this rule)">
          <Button type="text" icon={<CopyOutlined />} onClick={onDuplicate} />
        </Tooltip>
        <Tooltip title="Remove this rule">
          <Button type="text" danger icon={<DeleteOutlined />} onClick={onRemove} />
        </Tooltip>
      </Space>}
    >
      {/* Built only when unfolded: rules can be many. */}
      {expanded && <Space orientation="vertical" style={{ width: '100%' }} size={10}>
        <Space wrap>
          <Text>Put on</Text>
          <Select style={{ width: 240 }} value={rule.element_type}
            options={elementTypes.map(t => ({ value: t.id, label: t.label }))}
            onChange={v => onChange({ ...rule, element_type: v, conditions: [], model: '' })} />
          <Text>the model</Text>
          <Select showSearch style={{ width: fitWidth(modelLabel, 20), maxWidth: '100%' }} placeholder="model"
            value={rule.model || undefined} popupMatchSelectWidth={MODEL_POPUP_WIDTH}
            options={modelOptions} optionFilterProp="value"
            onChange={v => onChange({ ...rule, model: v, fields: v === rule.model ? rule.fields : {} })} />
          <Tooltip title="Add a model of the selected Dynawo that is not listed">
            <Button size="small" icon={<PlusOutlined />} onClick={onAddModel}>Add a model…</Button>
          </Tooltip>
        </Space>
        {selectedModel?.automation && (
          <Space wrap>
            {selectedModel.fields?.map(f => (
              <Space key={f.name}>
                <Text>{f.label}</Text>
                <Select style={{ width: fitWidth(f.values.reduce((a, v) => (v.label.length > a.length ? v.label : a), ''), 12) }}
                  value={rule.fields?.[f.name] || f.values[0].value} options={f.values}
                  onChange={v => onChange({ ...rule, fields: { ...rule.fields, [f.name]: v } })} />
              </Space>
            ))}
            <Text type="secondary">
              One automation system per element, named {selectedModel.id_prefix}_&lt;element id&gt;
              {selectedModel.requires ? `; it ${selectedModel.requires}` : ''}. {selectedModel.note}
            </Text>
          </Space>
        )}
        {selectedModel?.description && (
          <Collapse size="small" items={[{
            key: 'info',
            label: <Text type="secondary">Model information</Text>,
            children: <Text>{selectedModel.description}</Text>,
          }]} />
        )}

        <div>
          <Text type="secondary">
            {rule.conditions.length ? 'Only the elements meeting all these conditions:' : `Every element of this type, unless conditions are added.`}
          </Text>
          <Space orientation="vertical" style={{ width: '100%', marginTop: 6 }} size={6}>
            {rule.conditions.map((c, i) => (
              <ConditionRow key={i} elementType={rule.element_type} condition={c} attributes={attributes} notConnected={notConnected}
                onChange={nc => setCondition(i, nc)}
                onRemove={() => onChange({ ...rule, conditions: rule.conditions.filter((_, j) => j !== i) })} />
            ))}
            <Button size="small" icon={<PlusOutlined />} disabled={!attributes.length}
              onClick={() => onChange({ ...rule, conditions: [...rule.conditions, { attribute: '', operator: '=', value: '' }] })}>
              Add condition
            </Button>
          </Space>
        </div>

        <Space wrap>
          <Text>Parameters</Text>
          <Radio.Group optionType="button" size="small" value={rule.par_source}
            onChange={e => onChange({ ...rule, par_source: e.target.value, parameter_set_id: '' })}
            options={[
              { value: 'file', label: 'From a .par file' },
              {
                value: 'generic', disabled: !sources?.generic.available,
                label: <Tooltip title={sources?.generic.available ? '' : 'The generic parameters file is not available yet'}>Generic</Tooltip>,
              },
              { value: 'manual', label: 'Fill manually' },
            ]} />
          {rule.par_source === 'file' && (<>
            <Select style={{ width: 220 }} placeholder=".par file" value={rule.par_file || undefined}
              options={(sources?.files ?? []).map(f => ({ value: f.name, label: f.name }))}
              onChange={v => onChange({ ...rule, par_file: v, parameter_set_id: '' })} />
            <Select showSearch style={{ width: 220 }} placeholder="parameter set" value={rule.parameter_set_id || undefined}
              options={(parFile?.sets ?? []).map(s => ({ value: s, label: s }))}
              onChange={v => onChange({ ...rule, parameter_set_id: v })} />
          </>)}
          {rule.par_source === 'generic' && (
            <Text type="secondary">the generic set of {rule.model || 'the model'}</Text>
          )}
          {rule.par_source === 'manual' && (<>
            <Input style={{ width: fitWidth(rule.parameter_set_id || rule.model, 20), maxWidth: '100%' }}
              placeholder={rule.model || 'set name'} value={rule.parameter_set_id}
              onChange={e => onChange({ ...rule, parameter_set_id: e.target.value })} />
            <Text type="secondary">created empty, to fill in Edit Parameters after generating</Text>
          </>)}
        </Space>

        {report?.errors.map((e, i) => <Alert key={`e${i}`} type="error" showIcon description={e} />)}
        {report?.warnings.map((w, i) => <Alert key={`w${i}`} type="warning" showIcon description={w} />)}
      </Space>}
    </Card>
  )
}

// ── Adding a model ────────────────────────────────────────────────────────────

// Libraries of the selected Dynawo unknown to PowSyBl that fit the chosen
// category, equipment or automation system.
function AddModelDialog({ open, categories, onClose, onAdded, onRemoved }: {
  open: boolean
  categories: { value: string; label: string }[]
  onClose: () => void
  onAdded: (lib: string) => void
  onRemoved: () => void
}) {
  const [category, setCategory] = useState<string | undefined>(undefined)
  const [candidates, setCandidates] = useState<string[] | null>(null)
  const [lib, setLib] = useState<string | undefined>(undefined)
  const [shared, setShared] = useState(false)
  const [added, setAdded] = useState<{ dynawo_version: string | null; models: CustomModel[] }>({ dynawo_version: null, models: [] })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadAdded = () => client.get<{ dynawo_version: string | null; models: CustomModel[] }>('/dynamic-association/custom-models')
    .then(res => setAdded(res.data))
    .catch(err => setError(errorDetail(err, 'Could not read the added models')))
  const chooseCategory = (c: string) => {
    setCategory(c); setLib(undefined); setCandidates(null); setError(null); setBusy(true)
    client.get<{ candidates: string[] }>('/dynamic-association/custom-models/candidates', { params: { category: c } })
      .then(res => setCandidates(res.data.candidates))
      .catch(err => setError(errorDetail(err, 'Could not list the libraries')))
      .finally(() => setBusy(false))
  }
  const add = () => {
    if (!category || !lib) return
    setBusy(true); setError(null)
    client.post('/dynamic-association/custom-models', { lib, category, shared })
      .then(() => { onAdded(lib); setLib(undefined); setCandidates(null); setCategory(undefined) })
      .catch(err => setError(errorDetail(err, 'Could not add the model')))
      .finally(() => setBusy(false))
  }
  const remove = (m: CustomModel) => client.delete(`/dynamic-association/custom-models/${encodeURIComponent(m.lib)}`, { params: { shared: m.shared } })
    .then(() => { loadAdded(); onRemoved() })
    .catch(err => setError(errorDetail(err, 'Could not remove the model')))

  return (
    <Modal open={open} title="Add a model" onCancel={onClose} afterOpenChange={visible => { if (visible) loadAdded() }}
      okText="Add" onOk={add} okButtonProps={{ disabled: !lib, loading: busy && !!lib }} width={640} destroyOnHidden>
      <Space orientation="vertical" style={{ width: '100%' }} size={12}>
        <Text type="secondary">
          Libraries of the selected Dynawo{added.dynawo_version ? ` (${added.dynawo_version})` : ''} that PowSyBl does not
          know and that fit the category. An added model is offered only with this Dynawo version.
        </Text>
        <Space wrap>
          <Text>Category</Text>
          <Select style={{ width: 260 }} placeholder="category" value={category}
            options={categories} onChange={chooseCategory} />
        </Space>
        {busy && !candidates && <Text type="secondary">Checking the libraries of the selected Dynawo…</Text>}
        {candidates && (candidates.length ? (
          <Space wrap>
            <Text>Library</Text>
            <Select showSearch style={{ width: 420 }} placeholder="library" value={lib}
              options={candidates.map(c => ({ value: c, label: c }))} onChange={setLib} />
          </Space>
        ) : <Alert type="info" showIcon description="No library of the selected Dynawo can be added to this category." />)}
        <Radio.Group value={shared} onChange={e => setShared(e.target.value)} options={[
          { value: false, label: 'For this session' },
          { value: true, label: 'For all sessions' },
        ]} />
        {error && <Alert type="error" showIcon description={error} />}
        {added.models.length > 0 && (
          <Table size="small" rowKey={m => `${m.lib}/${m.shared}`} dataSource={added.models} pagination={false}
            columns={[
              { title: 'Added model', dataIndex: 'lib' },
              { title: 'Category', dataIndex: 'category' },
              { title: 'Kept', dataIndex: 'shared', render: (s: boolean) => (s ? 'all sessions' : 'this session') },
              { title: '', width: 50, render: (_, m) => <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(m)} /> },
            ]} />
        )}
      </Space>
    </Modal>
  )
}

// ── Saved rule sets ───────────────────────────────────────────────────────────

// The "Network and solver parameters, time window" settings, saved with rule sets.
const SECTION_SETTINGS = ['network_par_file', 'network_par_id', 'solver_lib', 'solver_par_file', 'solver_par_id', 'start_time', 'stop_time'] as const
const sectionSettings = (o: Partial<GenerationOptions>): Partial<GenerationOptions> =>
  Object.fromEntries(SECTION_SETTINGS.filter(k => k in o).map(k => [k, o[k]]))

interface RuleSet { name: string; saved: string; count: number }

// Saves the current rules under a name in the app's config directory.
function SaveRulesDialog({ open, rules, options, onClose }: {
  open: boolean
  rules: Rule[]
  options: Partial<GenerationOptions>
  onClose: () => void
}) {
  const [name, setName] = useState('')
  const [sets, setSets] = useState<RuleSet[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  // The saved set this name would replace.
  const existing = sets.find(s => s.name === name.trim())

  const save = () => {
    setBusy(true); setError(null)
    client.put(`/dynamic-association/rule-sets/${encodeURIComponent(name.trim())}`, { rules, options }, { params: { overwrite: !!existing } })
      .then(() => setDone(name.trim()))
      .catch(err => setError(errorStatus(err) === 409
        ? `Rules named '${name.trim()}' were saved meanwhile: save again to replace them.`
        : errorDetail(err, 'Could not save the rules')))
      .then(() => client.get<{ rule_sets: RuleSet[] }>('/dynamic-association/rule-sets'))
      .then(res => { if (res) setSets(res.data.rule_sets) })
      .finally(() => setBusy(false))
  }
  return (
    <Modal open={open} title="Save rules" onCancel={onClose} width={640} destroyOnHidden
      afterOpenChange={visible => {
        if (!visible) return
        setDone(null); setError(null)
        client.get<{ rule_sets: RuleSet[] }>('/dynamic-association/rule-sets')
          .then(res => setSets(res.data.rule_sets))
          .catch(err => setError(errorDetail(err, 'Could not list the saved rules')))
      }}
      footer={done ? <Button type="primary" onClick={onClose}>Close</Button> : <Space>
        <Button onClick={onClose}>Cancel</Button>
        <Button type="primary" danger={!!existing} loading={busy} disabled={!name.trim()} onClick={save}>
          {existing ? 'Replace' : 'Save'}
        </Button>
      </Space>}>
      {done ? <Alert type="success" showIcon description={`${rules.length} rule(s) saved as '${done}'.`} /> : (
        <Space orientation="vertical" style={{ width: '100%' }} size={10}>
          <Text type="secondary">
            The rules, with the network and solver parameters and time window, are kept by the application and can be
            loaded in any session. Pick a saved set below to update it.
          </Text>
          <Input placeholder="name" value={name} onChange={e => setName(e.target.value)}
            onPressEnter={() => name.trim() && save()} />
          {existing && (
            <Alert type="warning" showIcon
              description={`Replaces the saved rules '${existing.name}' (${existing.count} rule(s), saved ${existing.saved.replace('T', ' ')}).`} />
          )}
          {error && <Alert type="error" showIcon description={error} />}
          {sets.length > 0 && (
            <Table size="small" rowKey="name" dataSource={sets} pagination={{ pageSize: 6, hideOnSinglePage: true }}
              onRow={s => ({ onClick: () => setName(s.name), style: { cursor: 'pointer' } })}
              rowClassName={s => (s.name === name.trim() ? 'ant-table-row-selected' : '')}
              columns={[
                { title: 'Saved rules', dataIndex: 'name' },
                { title: 'Saved', dataIndex: 'saved', width: 170, render: (s: string) => s.replace('T', ' ') },
                { title: 'Rules', dataIndex: 'count', width: 70, align: 'right' },
              ]} />
          )}
        </Space>
      )}
    </Modal>
  )
}

// Lists the saved rule sets: load one in place of the current rules or after them, or delete it.
function LoadRulesDialog({ open, onClose, onLoad }: {
  open: boolean
  onClose: () => void
  onLoad: (rules: Rule[], options: Partial<GenerationOptions> | null, mode: 'replace' | 'append') => void
}) {
  const [sets, setSets] = useState<RuleSet[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const list = () => client.get<{ rule_sets: RuleSet[] }>('/dynamic-association/rule-sets')
    .then(res => setSets(res.data.rule_sets))
    .catch(err => setError(errorDetail(err, 'Could not list the saved rules')))
  const load = (name: string, mode: 'replace' | 'append') =>
    client.get<{ rules: Rule[]; options: Partial<GenerationOptions> | null }>(`/dynamic-association/rule-sets/${encodeURIComponent(name)}`)
      .then(res => onLoad(res.data.rules, res.data.options, mode))
      .catch(err => setError(errorDetail(err, 'Could not load the rules')))
  const remove = (name: string) => client.delete(`/dynamic-association/rule-sets/${encodeURIComponent(name)}`)
    .then(list)
    .catch(err => setError(errorDetail(err, 'Could not delete the rules')))

  return (
    <Modal open={open} title="Load rules" onCancel={onClose} footer={null} width={720} destroyOnHidden
      afterOpenChange={visible => { if (visible) { setError(null); list() } }}>
      <Space orientation="vertical" style={{ width: '100%' }} size={10}>
        {error && <Alert type="error" showIcon description={error} />}
        {sets && !sets.length && <Text type="secondary">No rules saved yet.</Text>}
        {sets && sets.length > 0 && (
          <Table size="small" rowKey="name" dataSource={sets} pagination={{ pageSize: 10, hideOnSinglePage: true }}
            columns={[
              { title: 'Name', dataIndex: 'name' },
              { title: 'Saved', dataIndex: 'saved', width: 170, render: (s: string) => s.replace('T', ' ') },
              { title: 'Rules', dataIndex: 'count', width: 70, align: 'right' },
              { title: '', width: 230, render: (_, s) => (
                <Space size={4}>
                  <Tooltip title="Replace the current rules and generation settings"><Button size="small" type="primary" onClick={() => load(s.name, 'replace')}>Load</Button></Tooltip>
                  <Tooltip title="Add after the current rules, keeping the current generation settings"><Button size="small" onClick={() => load(s.name, 'append')}>Add</Button></Tooltip>
                  <Popconfirm title={`Delete '${s.name}'?`} okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => remove(s.name)}>
                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </Space>
              ) },
            ]} />
        )}
      </Space>
    </Modal>
  )
}

// ── Rules from a .dyd ─────────────────────────────────────────────────────────

interface ImportReport {
  equipment: number
  automation_systems: number
  left_out: { reason: string; count: number; examples: string[] }[]
  unknown_models: string[]
  missing_par_files: string[]
  differences: string[]
  difference_count: number
}

// Rebuilds rules from a .dyd of the session on the loaded network, with what they cannot give.
function ImportDydDialog({ open, dydFiles, onClose, onImport }: {
  open: boolean
  dydFiles: string[]
  onClose: () => void
  onImport: (rules: Rule[], mode: 'replace' | 'append') => void
}) {
  const [file, setFile] = useState<string | undefined>(undefined)
  const [result, setResult] = useState<{ rules: Rule[]; report: ImportReport } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const read = (name: string) => {
    setFile(name); setResult(null); setError(null); setBusy(true)
    client.post<{ rules: Rule[]; report: ImportReport }>('/dynamic-association/import', { dyd_file: name })
      .then(res => setResult(res.data))
      .catch(err => setError(errorDetail(err, 'Could not read the .dyd')))
      .finally(() => setBusy(false))
  }
  const report = result?.report
  const examples = (ids: string[], count: number) => `${ids.join(', ')}${count > ids.length ? ' …' : ''}`

  return (
    <Modal open={open} title="Import rules from a .dyd" onCancel={onClose} width={760} destroyOnHidden
      afterOpenChange={visible => { if (visible) { setFile(undefined); setResult(null); setError(null) } }}
      footer={<Space>
        <Button onClick={onClose}>Cancel</Button>
        <Tooltip title="Add after the current rules"><Button disabled={!result?.rules.length} onClick={() => result && onImport(result.rules, 'append')}>Add</Button></Tooltip>
        <Tooltip title="Replace the current rules"><Button type="primary" disabled={!result?.rules.length} onClick={() => result && onImport(result.rules, 'replace')}>Replace rules</Button></Tooltip>
      </Space>}>
      <Space orientation="vertical" style={{ width: '100%' }} size={10}>
        <Text type="secondary">
          Rules giving the models of the .dyd on the loaded network, with their parameter sets. Models that rules
          cannot give are listed below.
        </Text>
        <Space wrap>
          <Text>.dyd file</Text>
          <Select style={{ width: 420 }} placeholder={dydFiles.length ? '.dyd of the session' : 'no .dyd in the session'}
            value={file} options={dydFiles.map(f => ({ value: f, label: f }))} onChange={read} />
        </Space>
        {busy && <Text type="secondary">Reading the .dyd…</Text>}
        {error && <Alert type="error" showIcon description={error} />}
        {report && result && (<>
          <Alert type={report.difference_count ? 'warning' : 'success'} showIcon description={<>
            {result.rules.length} rule(s) for {report.equipment} element model(s)
            {report.automation_systems ? ` and ${report.automation_systems} automation system(s)` : ''}.{' '}
            {report.difference_count
              ? `Applied to the network, they differ from the .dyd on ${report.difference_count} element(s):`
              : 'Applied to the network, they give the same models and parameter sets as the .dyd.'}
            {report.difference_count > 0 && (
              <ul style={{ margin: '4px 0' }}>{report.differences.map(d => <li key={d}>{d}</li>)}</ul>
            )}
          </>} />
          {report.unknown_models.length > 0 && (
            <Alert type="warning" showIcon description={<>
              Models unknown to PowSyBl: {report.unknown_models.join(', ')}. Their rules stay in error until they are
              added with "Add a model…", if the selected Dynawo has them.
            </>} />
          )}
          {report.missing_par_files.length > 0 && (
            <Alert type="warning" showIcon
              description={`Parameter files not among your session files: ${report.missing_par_files.join(', ')}. Upload them, or choose others in the rules.`} />
          )}
          {report.left_out.length > 0 && (
            <div>
              <Text>Not turned into rules:</Text>
              <ul style={{ margin: '4px 0' }}>
                {report.left_out.map(l => (
                  <li key={l.reason}><Tag>{l.count}</Tag>{l.reason} <Text type="secondary">— {examples(l.examples, l.count)}</Text></li>
                ))}
              </ul>
              <Text type="secondary">
                PowSyBl writes its own connections: connections written by hand in the .dyd are not kept.
              </Text>
            </div>
          )}
        </>)}
      </Space>
    </Modal>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function DynamicAssociation() {
  const navigate = useNavigate()
  const [context, setContext] = useState<Context | null>(null)
  const [elementTypes, setElementTypes] = useState<ElementType[]>([])
  const [sources, setSources] = useState<ParamSources | null>(null)
  const [rules, setRules] = useState<UiRule[]>([])
  const [loaded, setLoaded] = useState(false)
  const [attributes, setAttributes] = useState<Record<string, Attribute[]>>({})
  // Per element type: {id: why it is not connected}.
  const [notConnected, setNotConnected] = useState<Record<string, Record<string, string>>>({})
  const [models, setModels] = useState<Record<string, Model[]>>({})
  const [resolution, setResolution] = useState<Resolution | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [options, setOptions] = useState<GenerationOptions>(NO_OPTIONS)
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<GenerateResult | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const typesRequested = useRef(new Set<string>())
  const [addingFor, setAddingFor] = useState<number | null>(null)
  // Keys of the unfolded rules.
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [savingRules, setSavingRules] = useState(false)
  // Settings restored from saved rules: reopens their panel.
  const [settingsLoads, setSettingsLoads] = useState(0)
  const [loadingRules, setLoadingRules] = useState(false)
  const [importing, setImporting] = useState(false)
  // Page of the rules list (from 1).
  const [rulePage, setRulePage] = useState(1)
  const pageOf = (index: number) => Math.floor(index / RULES_PER_PAGE) + 1
  // Changes not saved yet, sent at once when leaving the page.
  const pendingRules = useRef<{ rules: Rule[] } | null>(null)
  const pendingOptions = useRef<GenerationOptions | null>(null)
  // Only the answer to the latest preview request is shown.
  const resolveRequest = useRef(0)

  const loadContext = () =>
    retryTransient(() => client.get<Context>('/dynamic-association/context')).then(res => setContext(res.data))

  useEffect(() => {
    Promise.all([
      retryTransient(() => client.get<Context>('/dynamic-association/context')),
      retryTransient(() => client.get<{ element_types: ElementType[] }>('/dynamic-association/element-types')),
      retryTransient(() => client.get<ParamSources>('/dynamic-association/parameter-sources')),
      retryTransient(() => client.get<{ rules: Rule[] }>('/dynamic-association/rules')),
    ])
      .then(([ctx, types, srcs, saved]) => {
        setContext(ctx.data)
        // Default reference job when never set.
        setOptions({ ...NO_OPTIONS, reference_jobs: ctx.data.default_reference_jobs, ...ctx.data.options })
        setResult(ctx.data.last_result)
        setElementTypes(types.data.element_types)
        setSources(srcs.data)
        const loadedRules = saved.data.rules.map(withKey)
        setRules(loadedRules)
        // A single rule opens; several start folded.
        setExpanded(new Set(loadedRules.length === 1 ? [loadedRules[0]._key] : []))
        setLoaded(true)
      })
      .catch(err => setError(errorDetail(err, 'Could not load the page')))
  }, [])

  // Attributes and models of each element type in use, read once each.
  useEffect(() => {
    if (!context?.network_file) return
    for (const typeId of new Set(rules.map(r => r.element_type))) {
      if (typesRequested.current.has(typeId)) continue
      typesRequested.current.add(typeId)
      client.get<{ attributes: Attribute[]; not_connected: Record<string, string> }>(`/dynamic-association/element-types/${typeId}/attributes`)
        .then(res => {
          setAttributes(prev => ({ ...prev, [typeId]: res.data.attributes }))
          setNotConnected(prev => ({ ...prev, [typeId]: res.data.not_connected }))
        })
        .catch(err => setError(errorDetail(err, `Could not read the attributes of ${typeId}`)))
      client.get<ModelsResponse>('/dynamic-association/models', { params: { element_type: typeId } })
        .then(res => setModels(prev => ({ ...prev, [typeId]: withAvailability(res.data) })))
        .catch(err => setError(errorDetail(err, 'Could not read the model catalogue')))
    }
  }, [rules, context?.network_file])

  // Save the rules and preview them, shortly after the last change.
  useEffect(() => {
    if (!loaded) return
    const body = { rules: rules.map(withoutKey) }
    pendingRules.current = body
    const timer = setTimeout(() => {
      pendingRules.current = null
      client.put('/dynamic-association/rules', body)
        .catch(err => setError(errorDetail(err, 'Could not save the rules')))
      if (!context?.network_file) return
      const request = ++resolveRequest.current
      client.post<Resolution>('/dynamic-association/resolve', body)
        .then(res => { if (request === resolveRequest.current) { setResolution(res.data); setError(null) } })
        .catch(err => setError(errorDetail(err, 'Could not preview the rules')))
    }, 400)
    return () => clearTimeout(timer)
  }, [rules, loaded, context?.network_file])

  // Save the generation settings, shortly after the last change.
  useEffect(() => {
    if (!loaded) return
    pendingOptions.current = options
    const timer = setTimeout(() => {
      pendingOptions.current = null
      client.put('/dynamic-association/options', options)
        .catch(err => setError(errorDetail(err, 'Could not save the generation settings')))
    }, 400)
    return () => clearTimeout(timer)
  }, [options, loaded])

  // Leaving the page: what is not saved yet is sent at once.
  useEffect(() => () => {
    if (pendingRules.current) client.put('/dynamic-association/rules', pendingRules.current).catch(() => undefined)
    if (pendingOptions.current) client.put('/dynamic-association/options', pendingOptions.current).catch(() => undefined)
  }, [])

  // After adding or removing a model, the model lists are read again.
  const reloadModels = () => {
    for (const typeId of Object.keys(models)) {
      client.get<ModelsResponse>('/dynamic-association/models', { params: { element_type: typeId } })
        .then(res => setModels(prev => ({ ...prev, [typeId]: withAvailability(res.data) })))
        .catch(err => setError(errorDetail(err, 'Could not read the model catalogue')))
    }
  }
  const addingRule = addingFor !== null ? rules[addingFor] : undefined
  const addingCategories = [...new Map((models[addingRule?.element_type ?? ''] ?? []).filter(m => m.suggested)
    .map(m => [m.category, { value: m.category, label: m.automation ? `${m.category} (automation system)` : m.category }])).values()]

  const updateRule = (i: number, rule: UiRule) => setRules(prev => prev.map((r, j) => (j === i ? rule : r)))
  const duplicateRule = (i: number) => {
    const copy = withKey({ ...withoutKey(rules[i]), conditions: rules[i].conditions.map(c => ({ ...c, value: Array.isArray(c.value) ? [...c.value] : c.value })) })
    setRules(prev => [...prev.slice(0, i + 1), copy, ...prev.slice(i + 1)])
    setExpanded(new Set([copy._key]))
    setRulePage(pageOf(i + 1))
  }
  const addRule = () => {
    const rule = newRule(elementTypes[0].id, sources?.files ?? [])
    setRules(prev => [...prev, rule])
    setExpanded(new Set([rule._key]))
    setRulePage(pageOf(rules.length))
  }
  // Rules replaced (from page 1) or added after the current ones (shown from the first added).
  const putRules = (added: Rule[], mode: 'replace' | 'append') => {
    const keyed = added.map(withKey)
    setRules(prev => (mode === 'replace' ? keyed : [...prev, ...keyed]))
    setExpanded(new Set())
    setRulePage(mode === 'replace' ? 1 : pageOf(rules.length))
  }
  const toggleRule = (key: number) => setExpanded(prev => {
    const next = new Set(prev)
    if (!next.delete(key)) next.add(key)
    return next
  })
  const moveRule = (i: number, delta: number) => {
    setRules(prev => {
      const next = [...prev]
      ;[next[i], next[i + delta]] = [next[i + delta], next[i]]
      return next
    })
    setRulePage(pageOf(i + delta))
  }

  const generate = async () => {
    setGenerating(true)
    setGenerateError(null)
    setResult(null)
    try {
      const res = await client.post<GenerateResult>('/dynamic-association/generate', {
        rules: rules.map(withoutKey),
        ...options,
      })
      setResult(res.data)
      setResolution(prev => (prev ? { ...prev, rules: res.data.rules } : prev))
      await loadContext()
    } catch (err) {
      setGenerateError(errorDetail(err, 'Generation failed'))
    } finally {
      setGenerating(false)
    }
  }

  const setOption = (changes: Partial<GenerationOptions>) => setOptions(prev => ({ ...prev, ...changes }))
  const referenceJobs = options.reference_jobs
  const hasOverrides = Object.entries(options).some(([k, v]) => k !== 'reference_jobs' && v !== null)
  const fromWhere = referenceJobs ? `from ${referenceJobs}` : 'created empty, to fill'
  const parSelect = (value: string | undefined, onChange: (v: string | undefined) => void) => (
    <Select allowClear style={{ width: 220 }} placeholder={fromWhere} value={value}
      options={(sources?.files ?? []).map(f => ({ value: f.name, label: f.name }))} onChange={onChange} />
  )
  const setSelect = (file: string | undefined, value: string | undefined, onChange: (v: string | undefined) => void) => (
    <Select allowClear showSearch style={{ width: 220 }} placeholder={file ? 'parameter set' : fromWhere} value={value}
      options={(sources?.files.find(f => f.name === file)?.sets ?? []).map(s => ({ value: s, label: s }))}
      onChange={onChange} />
  )

  const modelCounts = resolution?.models ?? []
  const shownPage = Math.min(rulePage, Math.max(1, Math.ceil(rules.length / RULES_PER_PAGE)))
  const firstShown = (shownPage - 1) * RULES_PER_PAGE

  const noNetwork = context !== null && !context.network_file
  const noDynawo = context !== null && !context.dynawo

  return (
    <div style={{ maxWidth: 1100 }}>
      <Title level={3}>Dynamic Association</Title>
      <Paragraph type="secondary">
        Put a dynamic model on the elements of the network with rules. Rules apply in order: when two rules
        match the same element, the later one wins. PowSyBl then writes the job, its .dyd and .par files,
        which are added to the session next to your own files.
      </Paragraph>

      {error && <Alert type="error" showIcon closable onClose={() => setError(null)} description={error} style={{ marginBottom: 16 }} />}
      {noNetwork && (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          description={<>No network loaded: load an IIDM file first. <a onClick={() => navigate('/')}>Upload Files</a></>} />
      )}

      <AddModelDialog open={addingFor !== null} categories={addingCategories}
        onClose={() => setAddingFor(null)}
        onAdded={lib => {
          if (addingFor !== null && addingRule) updateRule(addingFor, { ...addingRule, model: lib, fields: {} })
          setAddingFor(null)
          reloadModels()
        }}
        onRemoved={reloadModels} />

      <SaveRulesDialog open={savingRules} rules={rules.map(withoutKey)} options={sectionSettings(options)}
        onClose={() => setSavingRules(false)} />
      <LoadRulesDialog open={loadingRules} onClose={() => setLoadingRules(false)}
        onLoad={(loaded, savedOptions, mode) => {
          putRules(loaded, mode)
          if (mode === 'replace' && savedOptions) {
            setOptions(prev => ({ ...prev, ...sectionSettings(savedOptions) }))
            setSettingsLoads(n => n + 1)
          }
          setLoadingRules(false)
        }} />

      <ImportDydDialog open={importing} dydFiles={context?.dyd_files ?? []} onClose={() => setImporting(false)}
        onImport={(imported, mode) => { putRules(imported, mode); setImporting(false) }} />

      <Card title="Rules" style={{ marginBottom: 16 }} extra={<Space>
        <Button size="small" icon={<SaveOutlined />} disabled={!rules.length} onClick={() => setSavingRules(true)}>Save rules…</Button>
        <Button size="small" icon={<FolderOpenOutlined />} onClick={() => setLoadingRules(true)}>Load rules…</Button>
        <Tooltip title="Rules giving the models of a .dyd of the session">
          <Button size="small" icon={<ImportOutlined />} disabled={noNetwork}
            onClick={() => { loadContext().catch(() => undefined); setImporting(true) }}>Import from a .dyd…</Button>
        </Tooltip>
      </Space>}>
        {rules.length > RULES_PER_PAGE && (
          <Pagination size="small" style={{ marginBottom: 12 }} current={shownPage} pageSize={RULES_PER_PAGE}
            total={rules.length} showSizeChanger={false} onChange={setRulePage}
            showTotal={(total, [from, to]) => `rules ${from}–${to} of ${total}`} />
        )}
        {rules.slice(firstShown, firstShown + RULES_PER_PAGE).map((rule, j) => [rule, firstShown + j] as const).map(([rule, i]) => (
          <RuleCard key={rule._key} rule={rule} index={i} count={rules.length}
            report={resolution?.rules[i]}
            elementTypes={elementTypes}
            attributes={attributes[rule.element_type] ?? []}
            notConnected={notConnected[rule.element_type] ?? {}}
            models={models[rule.element_type] ?? []}
            sources={sources}
            onChange={r => updateRule(i, r)}
            onMove={delta => moveRule(i, delta)}
            onDuplicate={() => duplicateRule(i)}
            onRemove={() => setRules(prev => prev.filter((_, j) => j !== i))}
            onAddModel={() => setAddingFor(i)}
            expanded={expanded.has(rule._key)}
            onToggle={() => toggleRule(rule._key)} />
        ))}
        <Button type="dashed" icon={<PlusOutlined />} disabled={!loaded || !elementTypes.length}
          onClick={addRule}>
          Add rule
        </Button>
      </Card>

      {resolution && (
        <Card title="Preview" style={{ marginBottom: 16 }}>
          <Space orientation="vertical" style={{ width: '100%' }} size={16}>
            <Table size="small" pagination={false} rowKey="id"
              dataSource={resolution.element_types.filter(t => t.total > 0)}
              columns={[
                { title: 'Elements', dataIndex: 'label' },
                { title: 'In the network', dataIndex: 'total', align: 'right' },
                { title: 'With a dynamic model', align: 'right', render: (_, t) => t.total - t.unmatched_count },
                { title: 'Without (default network model)', align: 'right', render: (_, t) => t.unmatched_count },
              ]}
              expandable={{
                rowExpandable: t => t.unmatched_count > 0,
                expandedRowRender: t => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Without a dynamic model: {t.unmatched.join(', ')}
                    {t.unmatched_count > t.unmatched.length ? ` … and ${t.unmatched_count - t.unmatched.length} more` : ''}
                  </Text>
                ),
              }} />
            {modelCounts.length > 0 && (
              <Table size="small" pagination={false} rowKey="model" dataSource={modelCounts}
                columns={[
                  { title: 'Model', dataIndex: 'model' },
                  { title: 'Category', dataIndex: 'category',
                    render: (c: string, m) => (m.automation ? <>{c} <Tag>automation system</Tag></> : c) },
                  { title: 'Elements', dataIndex: 'count', align: 'right' },
                ]} />
            )}
          </Space>
        </Card>
      )}

      <Card title="Generate" style={{ marginBottom: 16 }}>
        <Space orientation="vertical" style={{ width: '100%' }} size={12}>
          {noDynawo && (
            <Alert type="warning" showIcon
              description={<>No Dynawo selected: its version decides what PowSyBl writes. <a onClick={() => navigate('/dynawo-version')}>Dynawo Version</a></>} />
          )}
          <Space wrap>
            <Text>Reference job</Text>
            <Select allowClear style={{ width: 260 }} placeholder="none" value={referenceJobs ?? undefined}
              options={(context?.jobs_files ?? []).map(j => ({ value: j, label: j }))}
              onChange={v => setOption({ reference_jobs: v ?? null })} />
            <Text type="secondary">network and solver parameters, time window and outputs are taken from it</Text>
          </Space>
          {/* Remounted when settings are loaded, to open when some are set. */}
          <Collapse size="small" key={loaded ? `loaded-${settingsLoads}` : 'loading'}
            defaultActiveKey={hasOverrides ? ['overrides'] : []} items={[{
            key: 'overrides',
            label: 'Network and solver parameters, time window',
            children: (
              <Space orientation="vertical" size={10}>
                <Space wrap>
                  <Text style={{ width: 70, display: 'inline-block' }}>Network</Text>
                  {parSelect(options.network_par_file ?? undefined, v => setOption({ network_par_file: v ?? null, network_par_id: null }))}
                  {setSelect(options.network_par_file ?? undefined, options.network_par_id ?? undefined, v => setOption({ network_par_id: v ?? null }))}
                </Space>
                <Space wrap>
                  <Text style={{ width: 70, display: 'inline-block' }}>Solver</Text>
                  <Select allowClear style={{ width: 220 }} placeholder={referenceJobs ? `from ${referenceJobs}` : 'IDA'}
                    value={options.solver_lib ?? undefined} options={SOLVERS} onChange={v => setOption({ solver_lib: v ?? null })} />
                  {parSelect(options.solver_par_file ?? undefined, v => setOption({ solver_par_file: v ?? null, solver_par_id: null }))}
                  {setSelect(options.solver_par_file ?? undefined, options.solver_par_id ?? undefined, v => setOption({ solver_par_id: v ?? null }))}
                </Space>
                <Space wrap>
                  <Text style={{ width: 70, display: 'inline-block' }}>Time (s)</Text>
                  <InputNumber placeholder={referenceJobs ? 'start: from job' : 'start: 0'} value={options.start_time}
                    onChange={v => setOption({ start_time: v })} style={{ width: 160 }} />
                  <InputNumber placeholder={referenceJobs ? 'stop: from job' : 'stop: 30'} value={options.stop_time}
                    onChange={v => setOption({ stop_time: v })} style={{ width: 160 }} />
                </Space>
              </Space>
            ),
          }]} />
          <Space>
            <Button type="primary" icon={<ThunderboltOutlined />} loading={generating}
              disabled={noNetwork || noDynawo || !rules.length}
              onClick={generate}>
              Generate
            </Button>
            <Tooltip title="The last generation as a pypowsybl script, to run outside the GUI in the folder of the session's files (Download Files)">
              <Button icon={<CodeOutlined />} disabled={!context?.script_available}
                onClick={() => { window.location.href = '/api/dynamic-association/script' }}>
                Export as Python script
              </Button>
            </Tooltip>
            {context?.generated_files.length ? (
              <Text type="secondary">generating again replaces the generated files: {context.generated_files.join(', ')}</Text>
            ) : null}
          </Space>

          {generateError && <Alert type="error" showIcon description={generateError} />}
          {result && (
            <Space orientation="vertical" style={{ width: '100%' }} size={12}>
              <Alert type="success" showIcon description={<>
                Job <Text code>{result.jobs}</Text> generated for Dynawo {result.dynawo_version}: {result.assigned} element(s)
                with a dynamic model{result.automation_systems ? `, ${result.automation_systems} automation system(s)` : ''}
                {result.reference_jobs ? `, settings taken from ${result.reference_jobs}` : ''}.
                Files added to the session: {result.files.join(', ')}.
              </>} />
              {result.to_fill.length > 0 && (
                <Alert type="info" showIcon description={<>
                  Parameter sets to fill before running:
                  <ul style={{ margin: '4px 0' }}>
                    {result.to_fill.map(f => (
                      <li key={`${f.file}/${f.set}`}>
                        <Text code>{f.set}</Text> in {f.file} ({f.for}) — {' '}
                        <a onClick={() => navigate(f.for === 'solver' ? '/edit-solver-parameters' : '/edit-parameters')}>
                          {f.for === 'solver' ? 'Edit Solver Params' : 'Edit Parameters'}
                        </a>
                        {f.needed_by_powsybl.length > 0 && (<>
                          <br />
                          <Text type="warning">
                            PowSyBl needs {f.needed_by_powsybl.join(', ')} from this set to write the job and used
                            placeholders for now: generate again once they are filled.
                          </Text>
                        </>)}
                      </li>
                    ))}
                  </ul>
                </>} />
              )}
              {result.dropped.length > 0 && (
                <>
                  <Alert type="warning" showIcon
                    description={`PowSyBl left ${result.dropped.length} model(s) out of the job:`} />
                  <Table size="small" rowKey={d => d.dynamic_model_id ?? `${d.element_type}/${d.static_id}`} dataSource={result.dropped}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    columns={[
                      { title: 'Element', dataIndex: 'static_id',
                        render: (s: string, d) => (d.dynamic_model_id ? `${s} (automation system ${d.dynamic_model_id})` : s) },
                      { title: 'Model', dataIndex: 'model' },
                      { title: 'Rule', dataIndex: 'rule', render: (r: number) => r + 1 },
                      { title: 'Reason (PowSyBl)', dataIndex: 'reason' },
                    ]} />
                </>
              )}
              <Collapse size="small" items={[{
                key: 'report', label: 'PowSyBl report',
                children: <pre style={{ fontSize: 12, margin: 0, whiteSpace: 'pre-wrap' }}>{result.report}</pre>,
              }]} />
            </Space>
          )}
        </Space>
      </Card>
    </div>
  )
}
