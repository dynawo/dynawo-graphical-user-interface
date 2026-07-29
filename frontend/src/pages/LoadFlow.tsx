//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Button, Card, Collapse, Flex, Input, InputNumber, Progress, Segmented, Select,
  Space, Switch, Table, Tag, Tooltip, Typography,
} from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, DeleteOutlined, DownloadOutlined,
  FolderOpenOutlined, MinusCircleOutlined, SyncOutlined, ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import client from '../api/client'
import DirectoryPicker from './DirectoryPicker'
import SimOutputsModal, { type ConstraintItem, type LostEquipmentItem, type TimelineEvent } from '../components/SimOutputsModal'

const { Title, Text } = Typography

// ── Types ─────────────────────────────────────────────────────────────────────

// pypowsybl fields — keyed dynamically from the server (version-agnostic)
type LfDefaults = Record<string, unknown>

interface EnumOptions {
  voltage_init_mode?: string[]
  balance_type?: string[]
  connected_component_mode?: string[]
  [key: string]: string[] | undefined
}

interface SlackBusResult { id: string; active_power_mismatch: number }
interface ComponentResult {
  num: number
  status: string
  iteration_count: number
  slack_bus_results: SlackBusResult[]
}
interface LfResult {
  ok: boolean
  provider: string
  output_filename: string
  components: ComponentResult[]
  debug_files?: string[]
}

interface NetworkElement {
  id: string
  name: string | null
  type: string
  vl_ids: string[]
}

interface LimitViolationItem {
  subject_id: string
  subject_name: string
  limit_type: string
  limit_name: string
  limit: number
  acceptable_duration: number
  limit_reduction: number
  value: number
  side: string
}

// kind -> filename, e.g. {timeline: "net_N_timeline.xml", log: "net_N_dynawo.log"}
type OutputFiles = Record<string, string>

interface ContingencyResult {
  contingency_id: string
  status: string
  limit_violations: LimitViolationItem[]
  output_files: OutputFiles
}

interface SecurityAnalysisResult {
  ok: boolean
  debug_files?: string[]
  pre_contingency: { status: string; limit_violations: LimitViolationItem[]; output_files: OutputFiles }
  contingencies: ContingencyResult[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const VOLTAGE_INIT_LABELS: Record<string, string> = {
  UNIFORM_VALUES:  'Uniform values',
  PREVIOUS_VALUES: 'Previous values',
  DC_VALUES:       'DC values',
}
const BALANCE_TYPE_LABELS: Record<string, string> = {
  PROPORTIONAL_TO_GENERATION_P_MAX:               'Prop. to generation P max',
  PROPORTIONAL_TO_GENERATION_P:                   'Prop. to generation P',
  PROPORTIONAL_TO_GENERATION_REMAINING_MARGIN:    'Prop. to generation remaining margin',
  PROPORTIONAL_TO_GENERATION_PARTICIPATION_FACTOR: 'Prop. to generation participation factor',
  PROPORTIONAL_TO_LOAD:                           'Prop. to load',
  PROPORTIONAL_TO_CONFORM_LOAD:                   'Prop. to conform load',
}

const CONNECTED_COMPONENT_LABELS: Record<string, string> = {
  MAIN: 'Main component only',
  ALL:  'All components',
}

const FIELD_LABELS: Record<string, string> = {
  voltage_init_mode:              'Voltage initialisation',
  transformer_voltage_control_on: 'Transformer voltage control',
  no_generator_reactive_limits:   'No generator reactive limits',
  phase_shifter_regulation_on:    'Phase shifter regulation',
  twt_split_shunt_admittance:     'Split shunt admittances (TWT)',
  simul_shunt:                    'Simulate shunt',
  read_slack_bus:                 'Read slack bus',
  write_slack_bus:                'Write slack bus',
  distributed_slack:              'Distributed slack',
  balance_type:                   'Balance type',
  dc_use_transformer_ratio:       'Use transformer ratio (DC)',
  countries_to_balance:           'Countries to balance',
  connected_component_mode:       'Connected components',
  provider_parameters:            'Provider parameters',
}

function statusTag(status: string) {
  switch (status) {
    case 'CONVERGED':             return <Tag icon={<CheckCircleOutlined />} color="success">Converged</Tag>
    case 'MAX_ITERATION_REACHED': return <Tag icon={<WarningOutlined />}     color="warning">Max iterations reached</Tag>
    case 'SOLVER_FAILED':         return <Tag icon={<CloseCircleOutlined />} color="error">Solver failed</Tag>
    case 'NO_CALCULATION':        return <Tag icon={<MinusCircleOutlined />} color="default">No calculation</Tag>
    default:                      return <Tag icon={<CloseCircleOutlined />} color="error">{status}</Tag>
  }
}

// ── Provider parameters form (typed, spec-driven per provider) ────────────────

interface ProviderParamSpec {
  name: string
  category: string
  description: string
  type: 'BOOLEAN' | 'STRING' | 'STRING_LIST' | 'DOUBLE' | 'INTEGER'
  default: unknown
  possible_values: string[]
}

function ProviderParamsForm({
  specs, value, onChange,
}: {
  specs: ProviderParamSpec[]
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
}) {
  const set = (name: string, v: unknown) => onChange({ ...value, [name]: v })

  const renderControl = (spec: ProviderParamSpec) => {
    const val = value[spec.name] ?? spec.default
    switch (spec.type) {
      case 'BOOLEAN':
        return <Switch size="small" checked={!!val} onChange={v => set(spec.name, v)} />
      case 'STRING_LIST':
        return (
          <Select
            size="small" mode={spec.possible_values.length ? 'multiple' : 'tags'}
            value={(val as string[]) ?? []}
            onChange={v => set(spec.name, v)}
            style={{ width: 260 }}
            options={spec.possible_values.map(v => ({ label: v, value: v }))}
          />
        )
      case 'DOUBLE':
      case 'INTEGER':
        return (
          <InputNumber
            size="small" value={val as number | null}
            step={spec.type === 'INTEGER' ? 1 : undefined}
            precision={spec.type === 'INTEGER' ? 0 : undefined}
            onChange={v => set(spec.name, v)}
            style={{ width: 140 }}
          />
        )
      default: // STRING
        return spec.possible_values.length
          ? (
            <Select
              size="small" value={val as string} onChange={v => set(spec.name, v)}
              style={{ width: 200 }}
              options={spec.possible_values.map(v => ({ label: v, value: v }))}
            />
          )
          : (
            <Input
              size="small" value={val as string} onChange={e => set(spec.name, e.target.value)}
              style={{ width: 200 }}
            />
          )
    }
  }

  const row = (spec: ProviderParamSpec) => (
    <Space align="center" key={spec.name} style={{ marginBottom: 6 }}>
      <Tooltip title={spec.description}>
        <Text style={{ fontSize: 12, color: '#888', minWidth: 200, display: 'inline-block' }}>
          {spec.name}
        </Text>
      </Tooltip>
      {renderControl(spec)}
    </Space>
  )

  const byCategory: Record<string, ProviderParamSpec[]> = {}
  for (const s of specs) (byCategory[s.category || '_'] ??= []).push(s)
  const categories = Object.keys(byCategory)

  if (categories.length === 1 && categories[0] === '_') {
    return <Flex vertical>{specs.map(row)}</Flex>
  }

  return (
    <Collapse
      size="small"
      items={categories.sort().map(cat => ({
        key: cat,
        label: cat === '_' ? 'General' : cat,
        children: <Flex vertical>{byCategory[cat].map(row)}</Flex>,
      }))}
    />
  )
}

// ── Parameter form ─────────────────────────────────────────────────────────────

function ParamForm({
  provider, ac, onAcChange,
  lfParams, onLfParamsChange,
  enumOptions, defaults, providerParamSpecs,
}: {
  provider: string
  ac: boolean; onAcChange: (v: boolean) => void
  lfParams: LfDefaults; onLfParamsChange: (p: LfDefaults) => void
  enumOptions: EnumOptions; defaults: LfDefaults
  providerParamSpecs: ProviderParamSpec[]
}) {
  const set = (patch: LfDefaults) => onLfParamsChange({ ...lfParams, ...patch })
  const has = (k: string) => k in defaults

  const labelStyle: React.CSSProperties = { fontSize: 12, color: '#888', minWidth: 220, display: 'inline-block' }
  const row = (label: string, control: React.ReactNode) => (
    <Space align="center" style={{ marginBottom: 6 }}>
      <Text style={labelStyle}>{label}</Text>
      {control}
    </Space>
  )
  const boolRow = (field: string) => {
    if (!has(field)) return null
    return row(
      FIELD_LABELS[field] ?? field,
      <Switch size="small"
        checked={!!lfParams[field]}
        onChange={v => set({ [field]: v })}
      />
    )
  }
  const enumRow = (field: string, optLabels: Record<string, string>, optKey?: string) => {
    const key = optKey ?? field
    if (!has(field) || !enumOptions[key]?.length) return null
    return row(
      FIELD_LABELS[field] ?? field,
      <Select size="small" value={lfParams[field] as string} style={{ width: 240 }}
        onChange={v => set({ [field]: v })}
        options={(enumOptions[key] ?? []).map(k => ({ value: k, label: optLabels[k] ?? k }))}
      />
    )
  }

  const providerParamsSection = providerParamSpecs.length > 0 && (
    <div style={{ marginTop: 8, marginBottom: 8 }}>
      <Text style={{ fontSize: 12, color: '#888' }}>{FIELD_LABELS.provider_parameters}</Text>
      <div style={{ marginTop: 4 }}>
        <ProviderParamsForm
          specs={providerParamSpecs}
          value={(lfParams.provider_parameters as Record<string, unknown>) ?? {}}
          onChange={v => set({ provider_parameters: v })}
        />
      </div>
    </div>
  )

  // DynaFlow ignores nearly all generic load-flow fields — DC is unsupported,
  // and most booleans/enums are silently replaced by the IIDM network setup.
  // Only its own provider parameters actually take effect, so that's all we show.
  if (provider === 'DynaFlow') {
    return <Flex vertical gap={0}>{providerParamsSection}</Flex>
  }

  return (
    <Flex vertical gap={0}>
      {/* AC / DC */}
      <Space style={{ marginBottom: 14 }}>
        <Text strong>Mode:</Text>
        <Switch checked={ac} onChange={onAcChange} checkedChildren="AC" unCheckedChildren="DC" />
      </Space>

      {/* Enum selects */}
      {enumRow('voltage_init_mode', VOLTAGE_INIT_LABELS)}
      {enumRow('balance_type', BALANCE_TYPE_LABELS)}
      {enumRow('connected_component_mode', CONNECTED_COMPONENT_LABELS)}

      {/* Countries to balance (tag select) */}
      {has('countries_to_balance') && row(
        FIELD_LABELS.countries_to_balance,
        <Select size="small" mode="tags"
          value={lfParams.countries_to_balance as string[] ?? []}
          onChange={v => set({ countries_to_balance: v })}
          style={{ width: 240 }} placeholder="e.g. FR, DE"
        />
      )}

      {/* Boolean switches */}
      {boolRow('distributed_slack')}
      {boolRow('phase_shifter_regulation_on')}
      {boolRow('transformer_voltage_control_on')}
      {boolRow('no_generator_reactive_limits')}
      {boolRow('twt_split_shunt_admittance')}
      {boolRow('simul_shunt')}
      {boolRow('read_slack_bus')}
      {boolRow('write_slack_bus')}
      {!ac && boolRow('dc_use_transformer_ratio')}

      {providerParamsSection}
    </Flex>
  )
}

// ── Result display ─────────────────────────────────────────────────────────────

function ResultDisplay({ result }: { result: LfResult }) {
  const navigate = useNavigate()
  const [outputsOpen, setOutputsOpen] = useState(false)

  const handleViewInNetworkView = () => {
    navigate(`/network-view?iidmFile=${encodeURIComponent(result.output_filename)}`)
  }

  const debugFiles = result.debug_files ?? []
  const timelineFile       = debugFiles.find(f => f.endsWith('_timeline.xml'))
  const constraintsFile    = debugFiles.find(f => f.endsWith('_constraints.xml'))
  const logFile            = debugFiles.find(f => f.endsWith('_dynawo.log'))
  const lostEquipmentsFile = debugFiles.find(f => f.endsWith('_lostEquipments.xml'))
  const hasOutputs = Boolean(timelineFile || constraintsFile || logFile || lostEquipmentsFile)

  const allConverged = result.components.every(c => c.status === 'CONVERGED')
  const runLabel = result.provider === 'DynaFlow' ? 'DynaFlow simulation' : 'Load flow'
  const columns = [
    { title: 'Component', dataIndex: 'num',            key: 'num',   width: 100, render: (n: number) => `#${n}` },
    { title: 'Status',    key: 'status',                              width: 200, render: (_: unknown, r: ComponentResult) => statusTag(r.status) },
    { title: 'Iterations', dataIndex: 'iteration_count', key: 'iter', width: 100 },
    {
      title: 'Slack bus mismatch (MW)', key: 'slack',
      render: (_: unknown, r: ComponentResult) =>
        r.slack_bus_results.length === 0
          ? <Text type="secondary" style={{ fontSize: 12 }}>—</Text>
          : r.slack_bus_results.map(sbr => (
            <Space key={sbr.id} size={4}>
              <Text code style={{ fontSize: 11 }}>{sbr.id}</Text>
              <Text style={{ fontSize: 12 }}>{sbr.active_power_mismatch.toFixed(4)}</Text>
            </Space>
          )),
    },
  ]
  return (
    <div style={{ marginTop: 16 }}>
      <Alert
        type={allConverged ? 'success' : 'warning'}
        description={
          <>
            <Text strong>{allConverged ? `${runLabel} converged` : `${runLabel} did not fully converge`}</Text>
            <br />
            Result saved as <Text code>{result.output_filename}</Text> — available in <Text strong>Files</Text>.
            {' '}
            <Button size="small" type="link" style={{ padding: 0 }} onClick={handleViewInNetworkView}>
              Open in Network View
            </Button>
            {hasOutputs && (
              <>
                {' '}
                <Button size="small" type="link" style={{ padding: 0 }} onClick={() => setOutputsOpen(true)}>
                  View outputs
                </Button>
              </>
            )}
            {result.debug_files && result.debug_files.length > 0 && (
              <>
                <br />
                Input files saved:{' '}
                {result.debug_files.map(f => (
                  <Text code key={f} style={{ marginRight: 4 }}>{f}</Text>
                ))}
                — available in <Text strong>Files</Text>.
              </>
            )}
          </>
        }
        style={{ marginBottom: 12 }}
      />
      <Table size="small" dataSource={result.components} columns={columns} rowKey="num" pagination={false} />
      <SimOutputsModal
        open={outputsOpen}
        onClose={() => setOutputsOpen(false)}
        title="DynaFlow run outputs"
        fetchTimeline={timelineFile
          ? () => client.get<TimelineEvent[]>(`/files/${encodeURIComponent(timelineFile)}/timeline`).then(r => r.data)
          : undefined}
        fetchConstraints={constraintsFile
          ? () => client.get<ConstraintItem[]>(`/files/${encodeURIComponent(constraintsFile)}/constraints`).then(r => r.data)
          : undefined}
        fetchLog={logFile
          ? () => client.get<{ text: string }>(`/files/${encodeURIComponent(logFile)}/log`).then(r => r.data.text)
          : undefined}
        fetchLostEquipments={lostEquipmentsFile
          ? () => client.get<LostEquipmentItem[]>(`/files/${encodeURIComponent(lostEquipmentsFile)}/lost-equipments`).then(r => r.data)
          : undefined}
      />
    </div>
  )
}

// ── Security analysis (N-1, DynaFlow only) ────────────────────────────────────

function violationColumns() {
  return [
    { title: 'Subject', key: 'subject', render: (_: unknown, r: LimitViolationItem) => r.subject_name || r.subject_id },
    { title: 'Limit type', dataIndex: 'limit_type', key: 'limit_type' },
    { title: 'Limit', dataIndex: 'limit', key: 'limit', render: (v: number) => v?.toFixed(2) },
    { title: 'Value', dataIndex: 'value', key: 'value', render: (v: number) => v?.toFixed(2) },
    { title: 'Side', dataIndex: 'side', key: 'side' },
    { title: 'Acceptable duration (s)', dataIndex: 'acceptable_duration', key: 'acceptable_duration' },
  ]
}

function buildOutputFetchers(files: OutputFiles) {
  const enc = encodeURIComponent
  return {
    fetchTimeline: files.timeline
      ? () => client.get<TimelineEvent[]>(`/files/${enc(files.timeline)}/timeline`).then(r => r.data)
      : undefined,
    fetchConstraints: files.constraints
      ? () => client.get<ConstraintItem[]>(`/files/${enc(files.constraints)}/constraints`).then(r => r.data)
      : undefined,
    fetchLog: files.log
      ? () => client.get<{ text: string }>(`/files/${enc(files.log)}/log`).then(r => r.data.text)
      : undefined,
    fetchLostEquipments: files.lost_equipments
      ? () => client.get<LostEquipmentItem[]>(`/files/${enc(files.lost_equipments)}/lost-equipments`).then(r => r.data)
      : undefined,
  }
}

function SecurityAnalysisSection({
  inputFile, lfParams, initialResult,
}: {
  inputFile: string | null
  lfParams: LfDefaults
  initialResult: SecurityAnalysisResult | null
}) {
  const [elements, setElements] = useState<NetworkElement[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [contingenciesStartTime, setContingenciesStartTime] = useState<number | null>(null)
  const [keepDebugFiles, setKeepDebugFiles] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<SecurityAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string | null>(null)
  const [viewingOutputs, setViewingOutputs] = useState<{ title: string; files: OutputFiles } | null>(null)

  useEffect(() => {
    if (!inputFile) { setElements([]); return }
    client.get<{ elements: NetworkElement[] }>(`/network/file/${encodeURIComponent(inputFile)}/elements`)
      .then(r => setElements(r.data.elements.filter(e => e.type !== 'voltage_level')))
      .catch(() => setElements([]))
  }, [inputFile])

  const handleRun = async () => {
    if (!inputFile || selectedIds.length === 0) return
    setRunning(true); setError(null); setResult(null)
    try {
      const res = await client.post<SecurityAnalysisResult>('/loadflow/security-analysis/run', {
        input_filename: inputFile,
        element_ids: selectedIds,
        parameters: lfParams,
        contingencies_start_time: contingenciesStartTime,
        keep_debug_files: keepDebugFiles,
      })
      setResult(res.data)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Security analysis failed')
    } finally {
      setRunning(false)
    }
  }

  const viewOutputsButton = (title: string, files: OutputFiles) =>
    Object.keys(files).length > 0 && (
      <Button size="small" type="link" style={{ padding: 0 }}
        onClick={() => setViewingOutputs({ title, files })}>
        View outputs
      </Button>
    )

  return (
    <Flex vertical gap={8}>
      <Space wrap>
        <Text style={{ fontSize: 12, color: '#888' }}>Contingency elements (N-1)</Text>
        <Select
          size="small" mode="multiple" allowClear
          showSearch={{ optionFilterProp: 'label' }}
          style={{ width: 420 }}
          value={selectedIds}
          onChange={setSelectedIds}
          placeholder="Select any number of lines, generators, loads, transformers…"
          options={elements.map(e => ({
            value: e.id,
            label: `${e.type}: ${e.id}${e.name ? ` (${e.name})` : ''}`,
          }))}
        />
      </Space>
      <Space wrap>
        <Text style={{ fontSize: 12, color: '#888' }}>Contingencies start time (s)</Text>
        <InputNumber
          size="small" value={contingenciesStartTime} style={{ width: 100 }}
          onChange={v => setContingenciesStartTime(v == null ? null : Number(v))}
          placeholder="default"
        />
        <Text style={{ fontSize: 12, color: '#888' }}>Keep input files (.dyd/.par)</Text>
        <Switch size="small" checked={keepDebugFiles} onChange={setKeepDebugFiles} />
        <Button size="small" type="primary" loading={running}
          disabled={!inputFile || selectedIds.length === 0}
          onClick={handleRun}>
          Run security analysis
        </Button>
      </Space>

      {error && <Alert type="error" description={error} style={{ marginTop: 8 }} />}

      {result && (
        <div style={{ marginTop: 8 }}>
          <Space style={{ marginBottom: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>Pre-contingency (N):</Text>
            {statusTag(result.pre_contingency.status)}
            {result.pre_contingency.limit_violations.length > 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {result.pre_contingency.limit_violations.length} violation(s)
              </Text>
            )}
            {viewOutputsButton('Pre-contingency (N) outputs', result.pre_contingency.output_files)}
          </Space>
          <Table
            size="small"
            rowKey="contingency_id"
            dataSource={result.contingencies}
            pagination={{ pageSize: 10 }}
            columns={[
              { title: 'Contingency', dataIndex: 'contingency_id', key: 'contingency_id' },
              { title: 'Status', key: 'status', render: (_: unknown, r: ContingencyResult) => statusTag(r.status) },
              { title: 'Violations', key: 'violations', render: (_: unknown, r: ContingencyResult) => r.limit_violations.length },
              {
                title: 'Outputs', key: 'outputs',
                render: (_: unknown, r: ContingencyResult) =>
                  viewOutputsButton(`Contingency "${r.contingency_id}" outputs`, r.output_files),
              },
            ]}
            expandable={{
              rowExpandable: r => r.limit_violations.length > 0,
              expandedRowRender: r => (
                <Table size="small" rowKey="subject_id" dataSource={r.limit_violations}
                       columns={violationColumns()} pagination={false} />
              ),
            }}
          />
          {result.debug_files && result.debug_files.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>Input files saved: </Text>
              {result.debug_files.map(f => (
                <Text code key={f} style={{ marginRight: 4, fontSize: 11 }}>{f}</Text>
              ))}
              <Text type="secondary" style={{ fontSize: 12 }}> — available in <Text strong>Files</Text>.</Text>
            </div>
          )}
        </div>
      )}

      <SimOutputsModal
        open={!!viewingOutputs}
        onClose={() => setViewingOutputs(null)}
        title={viewingOutputs?.title ?? ''}
        {...(viewingOutputs ? buildOutputFetchers(viewingOutputs.files) : {})}
      />
    </Flex>
  )
}

// ── LfPanel sessionStorage persistence ────────────────────────────────────────

interface SavedLfPanel {
  ac: boolean
  lfParams: LfDefaults
  iidmVersion: string
  keepDebugFiles: boolean
  inputFile: string | null
  outputBaseName: string
}

function loadLfPanel(provider: string): SavedLfPanel | null {
  try { return JSON.parse(sessionStorage.getItem(`lfpanel_${provider}`) ?? 'null') } catch { return null }
}

function saveLfPanel(provider: string, s: SavedLfPanel): void {
  try { sessionStorage.setItem(`lfpanel_${provider}`, JSON.stringify(s)) } catch {}
}

// ── LF panel (one per provider) ────────────────────────────────────────────────

function LfPanel({
  provider, available, unavailableMessage, defaults, enumOptions, networkName, iidmFiles, initialResult,
  initialSecurityResult, onDynaflowActiveChange, onRunComplete,
}: {
  provider: string
  available: boolean
  unavailableMessage?: string
  defaults: LfDefaults
  enumOptions: EnumOptions
  networkName: string | null
  iidmFiles: string[]
  initialResult: LfResult | null
  initialSecurityResult?: SecurityAnalysisResult | null
  onDynaflowActiveChange?: (home: string | null) => void
  onRunComplete?: () => void
}) {
  const [saved] = useState<SavedLfPanel | null>(() => loadLfPanel(provider))
  const [ac, setAc] = useState(saved?.ac ?? true)
  const [lfParams, setLfParams] = useState<LfDefaults>(saved?.lfParams ?? { ...defaults })
  const [providerParamSpecs, setProviderParamSpecs] = useState<ProviderParamSpec[]>([])
  const [inputFile, setInputFile] = useState<string | null>(saved?.inputFile ?? null)
  const [outputBaseName, setOutputBaseName] = useState(saved?.outputBaseName ?? '')
  const [iidmVersion, setIidmVersion] = useState(saved?.iidmVersion ?? '1.5')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<LfResult | null>(initialResult)
  const [error, setError] = useState<string | null>(null)
  const [keepDebugFiles, setKeepDebugFiles] = useState(saved?.keepDebugFiles ?? false)

  // Persist state across navigation
  useEffect(() => {
    saveLfPanel(provider, { ac, lfParams, iidmVersion, keepDebugFiles, inputFile, outputBaseName })
  }, [ac, lfParams, iidmVersion, keepDebugFiles, inputFile, outputBaseName]) // eslint-disable-line react-hooks/exhaustive-deps

  // Provider parameters differ per provider (DynaFlow has ~11, OpenLoadFlow ~80) —
  // fetch the spec for this specific provider rather than relying on the shared defaults.
  useEffect(() => {
    client.get<ProviderParamSpec[]>(`/loadflow/provider-parameters/${provider}`)
      .then(r => {
        setProviderParamSpecs(r.data)
        const specDefaults = Object.fromEntries(r.data.map(s => [s.name, s.default]))
        setLfParams(prev => {
          const savedPp = prev.provider_parameters as Record<string, unknown> | undefined
          // Spec defaults for any new params, saved user values on top
          return { ...prev, provider_parameters: { ...specDefaults, ...(savedPp ?? {}) } }
        })
      })
      .catch(() => {})
  }, [provider])

  const toBaseName = (n: string) =>
    n.replace(/\.(iidm|xiidm|xml)$/i, '') + (provider === 'DynaFlow' ? '_DynaFlow' : '_lf')

  // Default the input IIDM to the currently loaded network when it arrives —
  // mirrors outputBaseName's tracking below. The user can still override via
  // the "Input IIDM" select; switching networkName elsewhere (e.g. loading a
  // new network in Network View) only resets this if the user hasn't picked
  // something else themselves.
  const networkNameRef = useRef(networkName)
  useEffect(() => {
    if (networkName && networkName !== networkNameRef.current) {
      networkNameRef.current = networkName
      setOutputBaseName(prev => prev || toBaseName(networkName))
      setInputFile(prev => prev ?? networkName)
    }
  }, [networkName])

  // Also set on first mount if networkName is already known — but only when
  // there is no restored state; otherwise the saved selection takes priority.
  useEffect(() => {
    if (networkName) {
      networkNameRef.current = networkName
      if (!saved) {
        setOutputBaseName(toBaseName(networkName))
        setInputFile(networkName)
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Switching the input file resets the suggested output base name to follow
  // it, same as the initial-network suggestion above.
  const handleInputFileChange = (v: string) => {
    setInputFile(v)
    setOutputBaseName(toBaseName(v))
  }

  const handleRun = async () => {
    if (!inputFile) return
    setRunning(true); setError(null); setResult(null) // clear any stale prior result/error first
    try {
      const res = await client.post<LfResult>('/loadflow/run', {
        provider,
        ac: provider === 'DynaFlow' ? true : ac, // DynaFlow does not support DC load flow
        input_filename: inputFile,
        output_filename: outputBaseName,
        iidm_version: iidmVersion,
        parameters: lfParams,
        keep_debug_files: provider === 'DynaFlow' ? keepDebugFiles : false,
      })
      setResult(res.data)
      onRunComplete?.() // result.xiidm is now an uploaded file — refresh the Input IIDM options
    } catch (e: any) {
      const fallback = provider === 'DynaFlow' ? 'DynaFlow simulation failed' : 'Load flow failed'
      setError(e.response?.data?.detail ?? fallback)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Card
      title={<Text strong>{provider}</Text>}
      style={{ marginBottom: 24, opacity: available ? 1 : 0.6 }}
      extra={
        <Button type="primary" loading={running}
          disabled={!available || !inputFile}
          onClick={handleRun}>
          Run
        </Button>
      }
    >
      {!available && (
        <Alert type="warning" style={{ marginBottom: 12 }}
          description={unavailableMessage ?? `${provider} is not registered as a pypowsybl provider in this environment`}
        />
      )}
      {iidmFiles.length === 0 && (
        <Alert type="info" style={{ marginBottom: 12 }}
          description="No IIDM file uploaded yet — upload one in the Files page first"
        />
      )}
      {iidmFiles.length > 0 && (
        <Space style={{ marginBottom: 12 }}>
          <Text style={{ fontSize: 12, color: '#888', minWidth: 70, display: 'inline-block' }}>Input IIDM</Text>
          <Select
            size="small" style={{ width: 280 }}
            value={inputFile}
            onChange={handleInputFileChange}
            options={iidmFiles.map(f => ({ value: f, label: f }))}
          />
        </Space>
      )}
      <Collapse
        ghost
        items={[
          ...(provider === 'DynaFlow' ? [{
            key: 'dynaflow-launcher',
            label: 'DynaFlow Launcher',
            // forceRender: mount immediately even while collapsed, so the active-home
            // fetch inside DynaflowLauncherSection runs without requiring the user to expand it first.
            forceRender: true,
            children: <DynaflowLauncherSection onActiveChange={onDynaflowActiveChange ?? (() => {})} />,
          }] : []),
          {
            key: 'params',
            label: 'Parameters',
            children: (
              <>
                {provider === 'DynaFlow' && <StartingPointModeControl />}
                <ParamForm
                  provider={provider}
                  ac={ac} onAcChange={setAc}
                  lfParams={lfParams} onLfParamsChange={setLfParams}
                  enumOptions={enumOptions} defaults={defaults}
                  providerParamSpecs={providerParamSpecs}
                />
              </>
            ),
          },
          ...(provider === 'DynaFlow' ? [{
            key: 'security-analysis',
            label: 'Security analysis (N-1)',
            children: (
              <SecurityAnalysisSection
                inputFile={inputFile} lfParams={lfParams}
                initialResult={initialSecurityResult ?? null}
              />
            ),
          }] : []),
        ]}
      />

      {/* Output — always visible, except for DynaFlow which uses fixed defaults */}
      {provider !== 'DynaFlow' && (
        <Space style={{ marginTop: 8, marginBottom: 4 }}>
          <Text style={{ fontSize: 12, color: '#888', minWidth: 120, display: 'inline-block' }}>Output base name</Text>
          <Input
            size="small" value={outputBaseName} style={{ width: 260 }}
            onChange={e => setOutputBaseName(e.target.value)}
            placeholder={inputFile ? toBaseName(inputFile) : 'result_lf'}
          />
          <Text style={{ fontSize: 12, color: '#888' }}>.xiidm</Text>
          <Text style={{ fontSize: 12, color: '#888' }}>IIDM version</Text>
          <InputNumber
            size="small" value={parseFloat(iidmVersion) || 1.5}
            min={1.0} max={1.9} step={0.1} precision={1} style={{ width: 80 }}
            onChange={v => setIidmVersion(v != null ? v.toFixed(1) : '1.5')}
          />
        </Space>
      )}

      {provider === 'DynaFlow' && (
        <Space style={{ marginTop: 8, marginBottom: 4 }}>
          <Text style={{ fontSize: 12, color: '#888' }}>Keep input files (.dyd/.par)</Text>
          <Switch size="small" checked={keepDebugFiles} onChange={setKeepDebugFiles} />
        </Space>
      )}

      {error  && <Alert type="error"   description={error}  style={{ marginTop: 12 }} />}
      {result && result.provider === provider && <ResultDisplay result={result} />}
    </Card>
  )
}

// ── DynaFlow Launcher version management ───────────────────────────────────────

interface DfDownloadedVersion {
  os_key: string
  version: string
  home_dir: string | null
  in_use: boolean
}

interface DfProgressEvent {
  fraction: number
  text: string
  done: boolean
  error?: string
}

function DynaflowLauncherSection({ onActiveChange }: { onActiveChange: (home: string | null) => void }) {
  const [versions, setVersions] = useState<Record<string, Record<string, { url: string }>>>({})
  const [downloaded, setDownloaded] = useState<DfDownloadedVersion[]>([])
  const [activeHome, setActiveHome] = useState<string | null>(null)
  const [customPath, setCustomPath] = useState('')
  const [homeError, setHomeError] = useState<string | null>(null)
  const [showPicker, setShowPicker] = useState(false)

  const [selectedOs, setSelectedOs] = useState('')
  const [selectedVersion, setSelectedVersion] = useState('')

  const [progress, setProgress] = useState<DfProgressEvent | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [updatingVersion, setUpdatingVersion] = useState<{ os_key: string; version: string } | null>(null)
  const sseRef = useRef<EventSource | null>(null)

  const fetchAll = async () => {
    const [vRes, dRes, aRes] = await Promise.all([
      client.get('/dynaflow-launcher/versions'),
      client.get<DfDownloadedVersion[]>('/dynaflow-launcher/downloaded'),
      client.get<{ home_dir: string | null }>('/dynaflow-launcher/active'),
    ])
    setVersions(vRes.data)
    setDownloaded(dRes.data)
    setActiveHome(aRes.data.home_dir)
    onActiveChange(aRes.data.home_dir)
    setCustomPath(prev => prev || aRes.data.home_dir || '')
    if (!selectedOs && Object.keys(vRes.data).length) {
      const firstOs = Object.keys(vRes.data)[0]
      setSelectedOs(firstOs)
      setSelectedVersion(Object.keys(vRes.data[firstOs])[0] ?? '')
    }
  }

  const startSse = (onDone: (err?: string) => void) => {
    setDownloading(true)
    const es = new EventSource('/api/dynaflow-launcher/download/progress', { withCredentials: true })
    sseRef.current = es
    es.onmessage = (e) => {
      const data: DfProgressEvent = JSON.parse(e.data)
      setProgress(data)
      if (data.done) {
        es.close()
        setDownloading(false)
        onDone(data.error)
      }
    }
    es.onerror = () => {
      es.close()
      setDownloading(false)
      onDone('Connection lost')
    }
  }

  useEffect(() => {
    fetchAll().then(async () => {
      // The backend download runs in a daemon thread independent of this
      // component's lifecycle — if one is already in progress (e.g. we just
      // navigated back to this page), resume showing it instead of resetting
      // to idle, which would otherwise invite a duplicate "Download" click.
      const res = await client.get<{ os_key: string | null; version: string | null }>(
        '/dynaflow-launcher/download/active'
      )
      if (res.data.os_key && res.data.version) {
        setSelectedOs(res.data.os_key)
        setSelectedVersion(res.data.version)
        startSse(async (err) => {
          if (err) setDownloadError(err)
          else await fetchAll()
        })
      }
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSetHome = async () => {
    setHomeError(null)
    try {
      const res = await client.post<{ home_dir: string }>('/dynaflow-launcher/home', { home_dir: customPath })
      setActiveHome(res.data.home_dir)
      onActiveChange(res.data.home_dir)
      await fetchAll()
    } catch (err: any) {
      setHomeError(err.response?.data?.detail ?? 'Error setting launcher path')
    }
  }

  const handleUse = async (os_key: string, version: string) => {
    const res = await client.post<{ home_dir: string }>(`/dynaflow-launcher/use/${os_key}/${version}`)
    setActiveHome(res.data.home_dir)
    onActiveChange(res.data.home_dir)
    setCustomPath(res.data.home_dir)
    await fetchAll()
  }

  const handleRemove = async (os_key: string, version: string) => {
    await client.delete(`/dynaflow-launcher/remove/${os_key}/${version}`)
    await fetchAll()
  }

  const handleUpdate = async (os_key: string, version: string) => {
    setDownloadError(null)
    setProgress(null)
    setUpdatingVersion({ os_key, version })
    try {
      await client.delete(`/dynaflow-launcher/remove/${os_key}/${version}`)
      await client.post('/dynaflow-launcher/download', { os_key, version })
    } catch (err: any) {
      setDownloadError(err.response?.data?.detail ?? 'Update failed')
      setUpdatingVersion(null)
      await fetchAll()
      return
    }
    startSse(async (err) => {
      setUpdatingVersion(null)
      if (err) setDownloadError(err)
      else await fetchAll()
    })
  }

  const handleDownload = async () => {
    setDownloadError(null)
    setProgress(null)
    try {
      await client.post('/dynaflow-launcher/download', { os_key: selectedOs, version: selectedVersion })
    } catch (err: any) {
      setDownloadError(err.response?.data?.detail ?? 'Download failed')
      return
    }
    startSse(async (err) => {
      if (err) setDownloadError(err)
      else await fetchAll()
    })
  }

  const osOptions = Object.keys(versions).map(o => ({ label: o, value: o }))
  const versionOptions = Object.keys(versions[selectedOs] ?? {}).map(v => ({ label: v, value: v }))
  const selectedUrl = versions[selectedOs]?.[selectedVersion]?.url ?? ''
  const alreadyDownloaded = downloaded.some(d => d.os_key === selectedOs && d.version === selectedVersion)

  const downloadedColumns = [
    { title: 'OS', dataIndex: 'os_key', key: 'os_key', width: 70 },
    { title: 'Version', dataIndex: 'version', key: 'version', width: 100 },
    {
      title: 'Status', key: 'status', width: 150,
      render: (_: unknown, d: DfDownloadedVersion) => {
        const isUpdating = updatingVersion?.os_key === d.os_key && updatingVersion?.version === d.version
        if (isUpdating) return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}>
            <Tag icon={<SyncOutlined spin />} color="processing">Updating…</Tag>
            {progress && (
              <Progress size="small" percent={Math.round(progress.fraction * 100)} status="active" format={() => progress.text} />
            )}
          </div>
        )
        return d.in_use
          ? <Tag icon={<CheckCircleOutlined />} color="success">In use</Tag>
          : <Tag color="default">Available</Tag>
      },
    },
    {
      title: '', key: 'actions',
      render: (_: unknown, d: DfDownloadedVersion) => {
        const isUpdating = updatingVersion?.os_key === d.os_key && updatingVersion?.version === d.version
        return (
          <Space>
            {!d.in_use && (
              <Button size="small" icon={<ThunderboltOutlined />} disabled={downloading}
                onClick={() => handleUse(d.os_key, d.version)}>
                Use
              </Button>
            )}
            <Button size="small" icon={<SyncOutlined />} disabled={downloading}
              loading={isUpdating}
              onClick={() => handleUpdate(d.os_key, d.version)}>
              Update
            </Button>
            <Button size="small" danger icon={<DeleteOutlined />} disabled={downloading}
              onClick={() => handleRemove(d.os_key, d.version)}>
              Remove
            </Button>
          </Space>
        )
      },
    },
  ]

  return (
    <Flex vertical>
      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
        DynaFlow needs the <Text code>dynaflow-launcher</Text> binary configured below before it can run.
      </Text>

      {activeHome && (
        <Alert type="success" style={{ marginBottom: 12 }}
          description={<Text code style={{ fontSize: 12 }}>{activeHome}</Text>}
        />
      )}

      <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
        <Input
          value={customPath}
          onChange={e => { setCustomPath(e.target.value); setShowPicker(false) }}
          placeholder="/path/to/dynaflow-launcher"
          onPressEnter={handleSetHome}
        />
        <Button icon={<FolderOpenOutlined />} onClick={() => setShowPicker(v => !v)}>Browse</Button>
        <Button type="primary" onClick={handleSetHome}>Set</Button>
      </Space.Compact>
      {showPicker && (
        <div style={{ marginBottom: 12 }}>
          <DirectoryPicker
            initialPath={customPath || undefined}
            onSelect={path => { setCustomPath(path); setShowPicker(false) }}
            onCancel={() => setShowPicker(false)}
          />
        </div>
      )}
      {homeError && <Alert type="error" description={homeError} style={{ marginBottom: 12 }} />}

      {downloaded.length > 0 && (
        <Table
          dataSource={downloaded}
          columns={downloadedColumns}
          rowKey={d => `${d.os_key}-${d.version}`}
          size="small"
          pagination={false}
          style={{ marginBottom: 12 }}
        />
      )}

      <Space style={{ marginBottom: 8 }} wrap>
        <Select options={osOptions} value={selectedOs}
          onChange={v => { setSelectedOs(v); setSelectedVersion(Object.keys(versions[v] ?? {})[0] ?? '') }}
          style={{ width: 100 }} size="small" />
        <Select options={versionOptions} value={selectedVersion} onChange={setSelectedVersion}
          style={{ width: 160 }} size="small" />
        <Button size="small" type="primary" icon={<DownloadOutlined />}
          onClick={handleDownload}
          disabled={downloading || alreadyDownloaded || !selectedUrl}
          loading={downloading}>
          {alreadyDownloaded ? 'Already downloaded' : 'Download'}
        </Button>
      </Space>

      {downloading && progress && (
        <Progress percent={Math.round(progress.fraction * 100)} status="active" format={() => progress.text} />
      )}
      {downloadError && <Alert type="error" description={downloadError} style={{ marginTop: 8 }} />}
    </Flex>
  )
}

// ── Starting Point Mode ────────────────────────────────────────────────────────

function StartingPointModeControl() {
  const [mode, setMode] = useState<'WARM' | 'FLAT'>('WARM')
  const [setting, setSetting] = useState(false)

  useEffect(() => {
    client.get<{ mode: string }>('/dynaflow-launcher/starting-point-mode')
      .then(r => setMode(r.data.mode === 'FLAT' ? 'FLAT' : 'WARM'))
      .catch(() => {})
  }, [])

  const handleChange = async (newMode: string) => {
    setSetting(true)
    try {
      await client.post('/dynaflow-launcher/starting-point-mode', { mode: newMode })
      setMode(newMode as 'WARM' | 'FLAT')
    } finally {
      setSetting(false)
    }
  }

  return (
    <Space style={{ marginBottom: 12 }}>
      <Tooltip title="WARM uses DC_VALUES voltage initialisation; FLAT uses UNIFORM_VALUES. Changing this restarts the JVM worker.">
        <Text style={{ fontSize: 12, color: '#888' }}>Starting Point Mode</Text>
      </Tooltip>
      <Segmented
        size="small"
        value={mode}
        options={['WARM', 'FLAT']}
        disabled={setting}
        onChange={handleChange}
      />
    </Space>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function LoadFlow() {
  const [providers, setProviders]     = useState<string[]>([])
  const [defaults, setDefaults]       = useState<LfDefaults | null>(null)
  const [enumOptions, setEnumOptions] = useState<EnumOptions>({})
  const [networkName, setNetworkName] = useState<string | null>(null)
  const [iidmFiles, setIidmFiles]     = useState<string[]>([])
  const [lastResult, setLastResult]   = useState<LfResult | null>(null)
  const [lastSecurityResult, setLastSecurityResult] = useState<SecurityAnalysisResult | null>(null)
  const [loadError, setLoadError]     = useState<string | null>(null)
  const [dynaflowHome, setDynaflowHome] = useState<string | null>(null)

  const fetchIidmFiles = () =>
    client.get<{ name: string; ftype: string }[]>('/files/')
      .then(r => setIidmFiles(r.data.filter(f => f.ftype === 'iidm').map(f => f.name)))
      .catch(() => {})

  useEffect(() => {
    Promise.all([
      client.get('/loadflow/providers'),
      client.get('/network/summary').catch(() => null),
      client.get<LfResult>('/loadflow/result').catch(() => null),
      client.get<SecurityAnalysisResult>('/loadflow/security-analysis/result').catch(() => null),
      fetchIidmFiles(),
    ]).then(([pRes, nRes, rRes, saRes]) => {
      setProviders(pRes.data.providers)
      setDefaults(pRes.data.defaults)
      setEnumOptions(pRes.data.enum_options)
      if (nRes) setNetworkName(nRes.data.filename)
      if (rRes) setLastResult(rRes.data)
      if (saRes) setLastSecurityResult(saRes.data)
    }).catch(e => setLoadError(e.message ?? 'Failed to load page'))
  }, [])

  if (loadError) return <Alert type="error" description={loadError} />
  if (!defaults) return null

  const dynaflowProviderOk = providers.includes('DynaFlow')
  const dynaflowReady = dynaflowProviderOk && !!dynaflowHome
  const dynaflowUnavailableMessage = !dynaflowProviderOk
    ? 'DynaFlow is not registered as a pypowsybl provider in this environment'
    : 'DynaFlow-launcher is not configured yet — expand "DynaFlow Launcher" below to set it up'

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Load Flow / DynaFlow</Title>

      <LfPanel
        provider="OpenLoadFlow"
        available={providers.includes('OpenLoadFlow')}
        defaults={defaults}
        enumOptions={enumOptions}
        networkName={networkName}
        iidmFiles={iidmFiles}
        initialResult={lastResult?.provider === 'OpenLoadFlow' ? lastResult : null}
        onRunComplete={fetchIidmFiles}
      />

      {dynaflowProviderOk && (
        <LfPanel
          provider="DynaFlow"
          available={dynaflowReady}
          unavailableMessage={dynaflowUnavailableMessage}
          defaults={defaults}
          enumOptions={enumOptions}
          networkName={networkName}
          iidmFiles={iidmFiles}
          initialResult={lastResult?.provider === 'DynaFlow' ? lastResult : null}
          initialSecurityResult={lastSecurityResult}
          onDynaflowActiveChange={setDynaflowHome}
          onRunComplete={fetchIidmFiles}
        />
      )}
    </div>
  )
}
