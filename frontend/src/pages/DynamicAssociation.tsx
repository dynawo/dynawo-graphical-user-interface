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
  Alert, Button, Card, Collapse, Input, InputNumber, Radio, Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import { ArrowDownOutlined, ArrowUpOutlined, CodeOutlined, DeleteOutlined, PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail, retryTransient } from '../api/errors'

const { Title, Text, Paragraph } = Typography

// Dynamic association: rules putting a model on IIDM elements, applied in
// order. Rules and settings are saved with the session.

type ParSource = 'file' | 'generic' | 'manual'

interface Condition { attribute: string; operator: string; value: string | string[] }
interface Rule {
  element_type: string
  conditions: Condition[]
  model: string
  par_source: ParSource
  par_file: string
  parameter_set_id: string
}
// Page-only key, stable when rules are moved or removed.
type UiRule = Rule & { _key: number }

interface ElementType { id: string; label: string }
interface Attribute { name: string; kind: 'number' | 'boolean' | 'text'; operators: string[]; values: string[] | null }
interface Model { name: string; description: string; category: string; suggested: boolean }
interface ParFile { name: string; sets: string[] }
interface ParamSources { files: ParFile[]; generic: { available: boolean; sets: string[] } }
interface RuleReport { index: number; matched: number; applied: number; errors: string[]; warnings: string[] }
interface Assignment { element_type: string; static_id: string; model: string; category: string; rule: number }
interface TypeSummary { id: string; label: string; total: number; unmatched: string[] }
interface Resolution { rules: RuleReport[]; assignments: Assignment[]; element_types: TypeSummary[] }
interface Context {
  network_file: string | null
  dynawo: string | null
  jobs_files: string[]
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
const MAX_LISTED_IDS = 200

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
    par_source: 'file', par_file: parFiles[0]?.name ?? '', parameter_set_id: '',
  })
}

// ── One condition ─────────────────────────────────────────────────────────────

function ConditionRow({ condition, attributes, onChange, onRemove }: {
  condition: Condition
  attributes: Attribute[]
  onChange: (c: Condition) => void
  onRemove: () => void
}) {
  const attr = attributes.find(a => a.name === condition.attribute)
  const listValues = attr?.kind === 'boolean' ? ['true', 'false'] : attr?.values ?? null

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
      <Select mode="tags" style={{ minWidth: 220 }} placeholder="values"
        value={Array.isArray(condition.value) ? condition.value : []}
        options={(listValues ?? []).map(v => ({ value: v, label: v }))}
        onChange={v => onChange({ ...condition, value: v })} />
    )
  } else if (listValues && condition.operator !== 'matches') {
    valueWidget = (
      <Select showSearch style={{ minWidth: 220 }} placeholder="value"
        value={typeof condition.value === 'string' && condition.value ? condition.value : undefined}
        options={listValues.map(v => ({ value: v, label: v }))}
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

// ── One rule ──────────────────────────────────────────────────────────────────

function RuleCard({ rule, index, count, report, elementTypes, attributes, models, sources, onChange, onMove, onRemove }: {
  rule: UiRule
  index: number
  count: number
  report: RuleReport | undefined
  elementTypes: ElementType[]
  attributes: Attribute[]
  models: Model[]
  sources: ParamSources | null
  onChange: (r: UiRule) => void
  onMove: (delta: number) => void
  onRemove: () => void
}) {
  const typeLabel = elementTypes.find(t => t.id === rule.element_type)?.label ?? rule.element_type
  const modelOptions = useMemo(() => {
    const option = (m: Model) => ({
      value: m.name,
      label: <span title={m.description}>{m.name} <Text type="secondary">· {m.category}</Text></span>,
    })
    return [
      { label: `Suggested for ${typeLabel.toLowerCase()}`, options: models.filter(m => m.suggested).map(option) },
      { label: 'Other models', options: models.filter(m => !m.suggested).map(option) },
    ]
  }, [models, typeLabel])
  const parFile = sources?.files.find(f => f.name === rule.par_file)
  const category = models.find(m => m.name === rule.model)?.category
  const modelLabel = category ? `${rule.model} · ${category}` : rule.model

  const setCondition = (i: number, c: Condition) =>
    onChange({ ...rule, conditions: rule.conditions.map((x, j) => (j === i ? c : x)) })

  return (
    <Card size="small" style={{ marginBottom: 12 }}
      title={<Space><Text strong>Rule {index + 1}</Text>
        {report && !report.errors.length && (
          <Tag color={report.applied ? 'blue' : 'default'}>
            {report.applied} element(s){report.matched !== report.applied ? ` — ${report.matched - report.applied} taken by later rules` : ''}
          </Tag>
        )}
        {report?.errors.length ? <Tag color="red">skipped</Tag> : null}
      </Space>}
      extra={<Space size={0}>
        <Tooltip title="Move up (an earlier rule gives way to a later one)">
          <Button type="text" icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => onMove(-1)} />
        </Tooltip>
        <Tooltip title="Move down">
          <Button type="text" icon={<ArrowDownOutlined />} disabled={index === count - 1} onClick={() => onMove(1)} />
        </Tooltip>
        <Tooltip title="Remove this rule">
          <Button type="text" danger icon={<DeleteOutlined />} onClick={onRemove} />
        </Tooltip>
      </Space>}
    >
      <Space orientation="vertical" style={{ width: '100%' }} size={10}>
        <Space wrap>
          <Text>Put on</Text>
          <Select style={{ width: 240 }} value={rule.element_type}
            options={elementTypes.map(t => ({ value: t.id, label: t.label }))}
            onChange={v => onChange({ ...rule, element_type: v, conditions: [], model: '' })} />
          <Text>the model</Text>
          <Select showSearch style={{ width: fitWidth(modelLabel, 20), maxWidth: '100%' }} placeholder="model"
            value={rule.model || undefined} popupMatchSelectWidth={MODEL_POPUP_WIDTH}
            options={modelOptions} optionFilterProp="value"
            onChange={v => onChange({ ...rule, model: v })} />
        </Space>

        <div>
          <Text type="secondary">
            {rule.conditions.length ? 'Only the elements meeting all these conditions:' : `Every element of this type, unless conditions are added.`}
          </Text>
          <Space orientation="vertical" style={{ width: '100%', marginTop: 6 }} size={6}>
            {rule.conditions.map((c, i) => (
              <ConditionRow key={i} condition={c} attributes={attributes}
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
      </Space>
    </Card>
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
  const [models, setModels] = useState<Record<string, Model[]>>({})
  const [resolution, setResolution] = useState<Resolution | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [options, setOptions] = useState<GenerationOptions>(NO_OPTIONS)
  const [generating, setGenerating] = useState(false)
  const [result, setResult] = useState<GenerateResult | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const typesRequested = useRef(new Set<string>())

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
        setRules(saved.data.rules.map(withKey))
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
      client.get<{ attributes: Attribute[] }>(`/dynamic-association/element-types/${typeId}/attributes`)
        .then(res => setAttributes(prev => ({ ...prev, [typeId]: res.data.attributes })))
        .catch(err => setError(errorDetail(err, `Could not read the attributes of ${typeId}`)))
      client.get<{ models: Model[] }>('/dynamic-association/models', { params: { element_type: typeId } })
        .then(res => setModels(prev => ({ ...prev, [typeId]: res.data.models })))
        .catch(err => setError(errorDetail(err, 'Could not read the model catalogue')))
    }
  }, [rules, context?.network_file])

  // Save the rules and preview them, shortly after the last change.
  useEffect(() => {
    if (!loaded) return
    const timer = setTimeout(() => {
      const body = { rules: rules.map(withoutKey) }
      client.put('/dynamic-association/rules', body)
        .catch(err => setError(errorDetail(err, 'Could not save the rules')))
      if (!context?.network_file) return
      client.post<Resolution>('/dynamic-association/resolve', body)
        .then(res => { setResolution(res.data); setError(null) })
        .catch(err => setError(errorDetail(err, 'Could not preview the rules')))
    }, 400)
    return () => clearTimeout(timer)
  }, [rules, loaded, context?.network_file])

  // Save the generation settings, shortly after the last change.
  useEffect(() => {
    if (!loaded) return
    const timer = setTimeout(() => {
      client.put('/dynamic-association/options', options)
        .catch(err => setError(errorDetail(err, 'Could not save the generation settings')))
    }, 400)
    return () => clearTimeout(timer)
  }, [options, loaded])

  const updateRule = (i: number, rule: UiRule) => setRules(prev => prev.map((r, j) => (j === i ? rule : r)))
  const moveRule = (i: number, delta: number) => setRules(prev => {
    const next = [...prev]
    ;[next[i], next[i + delta]] = [next[i + delta], next[i]]
    return next
  })

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

  const modelCounts = useMemo(() => {
    const counts = new Map<string, { model: string; category: string; count: number }>()
    for (const a of resolution?.assignments ?? []) {
      const entry = counts.get(a.model) ?? { model: a.model, category: a.category, count: 0 }
      entry.count += 1
      counts.set(a.model, entry)
    }
    return [...counts.values()].sort((a, b) => b.count - a.count)
  }, [resolution])

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

      <Card title="Rules" style={{ marginBottom: 16 }}>
        {rules.map((rule, i) => (
          <RuleCard key={rule._key} rule={rule} index={i} count={rules.length}
            report={resolution?.rules[i]}
            elementTypes={elementTypes}
            attributes={attributes[rule.element_type] ?? []}
            models={models[rule.element_type] ?? []}
            sources={sources}
            onChange={r => updateRule(i, r)}
            onMove={delta => moveRule(i, delta)}
            onRemove={() => setRules(prev => prev.filter((_, j) => j !== i))} />
        ))}
        <Button type="dashed" icon={<PlusOutlined />} disabled={!loaded || !elementTypes.length}
          onClick={() => setRules(prev => [...prev, newRule(elementTypes[0].id, sources?.files ?? [])])}>
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
                { title: 'With a dynamic model', align: 'right', render: (_, t) => t.total - t.unmatched.length },
                { title: 'Without (default network model)', align: 'right', render: (_, t) => t.unmatched.length },
              ]}
              expandable={{
                rowExpandable: t => t.unmatched.length > 0,
                expandedRowRender: t => (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Without a dynamic model: {t.unmatched.slice(0, MAX_LISTED_IDS).join(', ')}
                    {t.unmatched.length > MAX_LISTED_IDS ? ` … and ${t.unmatched.length - MAX_LISTED_IDS} more` : ''}
                  </Text>
                ),
              }} />
            {modelCounts.length > 0 && (
              <Table size="small" pagination={false} rowKey="model" dataSource={modelCounts}
                columns={[
                  { title: 'Model', dataIndex: 'model' },
                  { title: 'Category', dataIndex: 'category' },
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
          {/* Remounted after loading, to open when settings are set. */}
          <Collapse size="small" key={loaded ? 'loaded' : 'loading'}
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
                with a dynamic model{result.reference_jobs ? `, settings taken from ${result.reference_jobs}` : ''}.
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
                    description={`PowSyBl left ${result.dropped.length} element(s) out of the job:`} />
                  <Table size="small" rowKey={d => `${d.element_type}/${d.static_id}`} dataSource={result.dropped}
                    pagination={{ pageSize: 10, hideOnSinglePage: true }}
                    columns={[
                      { title: 'Element', dataIndex: 'static_id' },
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
