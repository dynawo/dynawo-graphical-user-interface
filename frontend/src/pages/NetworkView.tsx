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
import { useSearchParams } from 'react-router-dom'
import {
  Alert, Button, Card, Cascader, Checkbox, Drawer, Flex, InputNumber,
  Popover, Select, Space, Spin, Switch, Table, Tabs, Tooltip, Typography,
} from 'antd'
import { SaveOutlined, SettingOutlined } from '@ant-design/icons'
import client from '../api/client'
import SvgViewer from '../components/SvgViewer'

const { Title, Text } = Typography

// ── Types ─────────────────────────────────────────────────────────────────────

interface NetworkElement {
  id: string
  name: string | null
  type: string
  vl_ids: string[]
}

interface Par { name: string; type: string; value: string }
interface Ref { name: string; origData: string; origName: string }

interface DynModel {
  dyn_id: string
  lib: string
  parFile: string
  parId: string
  color: string
  pars: Par[]
  refs: Ref[]
}

interface RunEntry {
  run_id: number
  label: string
  has_final_state_iidm: boolean
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Encode a Dynawo staticId into the PowSyBl SLD SVG element id (mirror of _svg_id).
 *  PowSyBl escapes every non-alphanumeric character as `_<ascii-decimal>_`
 *  (e.g. `_` → `_95_`, `-` → `_45_`, space → `_32_`), so ids with spaces such as
 *  "B1-G1 " become `idB1_45_G1_32_`. Escaping only `_`/`-` misses those. */
function svgId(staticId: string): string {
  return 'id' + staticId.replace(/[^A-Za-z0-9]/g, c => `_${c.charCodeAt(0)}_`)
}

/** Inject CSS glow rules for dynamic models into an SVG string. */
function injectDynCss(svg: string, dynModels: Record<string, DynModel>): string {
  const rules: string[] = []
  for (const [sid, info] of Object.entries(dynModels)) {
    const eid = svgId(sid)
    rules.push(
      `#${eid} { filter: drop-shadow(0 0 5px ${info.color}) drop-shadow(0 0 2px ${info.color}); }`
    )
  }
  if (!rules.length) return svg
  const css = rules.join('\n')
  return svg.replace(
    ']]></style>',
    `\n/* dynawo-ihm dyn overlay */\n${css}\n]]></style>`,
  )
}

function valuesEqual(a: string, b: string, type: string): boolean {
  try {
    if (type === 'BOOL') return a.trim().toLowerCase() === b.trim().toLowerCase()
    if (type === 'INT') return parseInt(a) === parseInt(b)
    return parseFloat(a) === parseFloat(b)
  } catch {
    return a === b
  }
}

// ── Shared: auto-load the IIDM file if no network is in session ───────────────

interface FileEntry { name: string; ftype: string | null }

async function ensureNetworkLoaded(): Promise<void> {
  const res = await client.get<FileEntry[]>('/files/')
  const iidm = res.data.find(f => f.ftype === 'iidm')
  if (!iidm) throw new Error('No IIDM file uploaded. Go to the Upload page and add one.')
  await client.post(`/network/load?filename=${encodeURIComponent(iidm.name)}`)
}

// ── Diff config ───────────────────────────────────────────────────────────────

interface DiffConfig {
  moderatePct: number
  largePct: number
  moderateColour: string
  largeColour: string
  negModerateColour: string
  negLargeColour: string
  signed: boolean
  quantities: string[]
}

const DEFAULT_DIFF_CONFIG: DiffConfig = {
  moderatePct: 1,
  largePct: 5,
  moderateColour: '#faad14',
  largeColour: '#f5222d',
  negModerateColour: '#69c0ff',
  negLargeColour: '#1890ff',
  signed: false,
  quantities: ['voltage', 'current', 'active_power'],
}

const QUANTITY_OPTIONS = [
  { label: 'Bus voltage',    value: 'voltage' },
  { label: 'Branch current', value: 'current' },
  { label: 'Active power',   value: 'active_power' },
]

function diffQueryString(cfg: DiffConfig): string {
  const params = new URLSearchParams({
    moderate_pct:        String(cfg.moderatePct),
    large_pct:           String(cfg.largePct),
    moderate_colour:     cfg.moderateColour,
    large_colour:        cfg.largeColour,
    neg_moderate_colour: cfg.negModerateColour,
    neg_large_colour:    cfg.negLargeColour,
    signed:              String(cfg.signed),
  })
  cfg.quantities.forEach(q => params.append('quantities', q))
  return params.toString()
}

// ── NAD Tab ───────────────────────────────────────────────────────────────────

// Networks with more voltage levels than this default to a windowed (neighbourhood) view,
// since rendering the whole grid as one SVG gets very slow.
const NAD_AUTO_WINDOW_THRESHOLD = 150
const DEFAULT_NAD_DEPTH = 1

// Module-level: survives component unmounts so navigating away and back is instant
const nadSvgCache = new Map<string, string>()

function buildNadUrl(endpoint: string, diffQs: string, windowed: boolean, centerVl: string | null, depth: number): string {
  const params = new URLSearchParams(diffQs)
  if (windowed && centerVl) {
    params.set('depth', String(depth))
    params.append('vl_ids', centerVl)
  }
  const qs = params.toString()
  return qs ? `${endpoint}?${qs}` : endpoint
}

function NadTab({ networkBase, diffMode, diffConfig }: {
  networkBase: string; diffMode: boolean; diffConfig: DiffConfig
}) {
  const isUploaded = networkBase === '/network'
  const activeDiff = diffMode && !isUploaded
  const nadEndpoint = activeDiff ? `${networkBase}/diff/nad` : `${networkBase}/nad`
  const diffQs = activeDiff ? diffQueryString(diffConfig) : ''

  const [vlIds, setVlIds] = useState<string[]>([])
  const [vlListLoaded, setVlListLoaded] = useState(false)
  const [windowed, setWindowed] = useState(false)
  const [centerVl, setCenterVl] = useState<string | null>(null)
  const [depth, setDepth] = useState(DEFAULT_NAD_DEPTH)

  const [svg, setSvg] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch the VL list (cheap, no rendering) to decide whether to default to a windowed view.
  useEffect(() => {
    setVlListLoaded(false)
    const fetchVls = () => client.get<{ vl_ids: string[] }>(`${networkBase}/voltage-levels`)
    const run = isUploaded
      ? () => fetchVls().catch(async e => {
          if (e.response?.status === 404) { await ensureNetworkLoaded(); return fetchVls() }
          throw e
        })
      : fetchVls
    run()
      .then(r => {
        const ids = r.data.vl_ids
        setVlIds(ids)
        setWindowed(ids.length > NAD_AUTO_WINDOW_THRESHOLD)
        setCenterVl(prev => (prev && ids.includes(prev)) ? prev : (ids[0] ?? null))
      })
      .catch(() => setVlIds([]))
      .finally(() => setVlListLoaded(true))
  }, [networkBase])

  const nadUrl = buildNadUrl(nadEndpoint, diffQs, windowed, centerVl, depth)

  useEffect(() => {
    if (!vlListLoaded) return
    const cached = nadSvgCache.get(nadUrl)
    if (cached) { setSvg(cached); return }

    setLoading(true); setSvg(null)
    const fetchNad = () =>
      client.get<string>(nadUrl, { responseType: 'text' })
        .then(r => { nadSvgCache.set(nadUrl, r.data); setSvg(r.data) })

    const run = isUploaded
      ? () => fetchNad().catch(async e => {
          if (e.response?.status === 404) { await ensureNetworkLoaded(); await fetchNad() }
          else throw e
        })
      : fetchNad

    run()
      .catch(e => setError(e.message ?? e.response?.data?.detail ?? 'Failed to render NAD'))
      .finally(() => setLoading(false))
  }, [nadUrl, vlListLoaded])

  const controls = vlIds.length > 0 && (
    <Space style={{ marginBottom: 8 }} wrap>
      <Switch checked={windowed} onChange={setWindowed} size="small" />
      <Text style={{ fontSize: 12 }}>Show only a neighbourhood</Text>
      {windowed && (
        <>
          <Select
            showSearch
            size="small"
            style={{ width: 240 }}
            value={centerVl ?? undefined}
            options={vlIds.map(id => ({ value: id, label: id }))}
            onChange={setCenterVl}
            placeholder="Center voltage level"
          />
          <Text style={{ fontSize: 12 }}>Depth:</Text>
          <InputNumber
            size="small" min={0} max={6}
            value={depth}
            onChange={v => setDepth(v ?? DEFAULT_NAD_DEPTH)}
            style={{ width: 60 }}
          />
        </>
      )}
      {!windowed && vlIds.length > NAD_AUTO_WINDOW_THRESHOLD && (
        <Text type="warning" style={{ fontSize: 12 }}>
          Large network ({vlIds.length} voltage levels) — full rendering may be slow.
        </Text>
      )}
    </Space>
  )

  return (
    <>
      {controls}
      {loading && <Spin description="Rendering Network Area Diagram…" style={{ display: 'block', marginTop: 40 }} />}
      {error && <Alert type="warning" description={error} />}
      {!loading && !error && svg && (
        <>
          <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
            Scroll to zoom · Drag to pan · Double-click to reset
          </Text>
          <SvgViewer svg={svg} height={700} />
        </>
      )}
    </>
  )
}

// ── Dynamic model side panel ──────────────────────────────────────────────────

interface PanelProps {
  sid: string
  model: DynModel
  onClose: () => void
  onApplied: () => void
}

function DynModelPanel({ sid, model, onClose, onApplied }: PanelProps) {
  const [formVals, setFormVals] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {}
    for (const p of model.pars) init[p.name] = p.value
    return init
  })
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const changed = model.pars.some(p => !valuesEqual(p.value, formVals[p.name] ?? p.value, p.type))

  const handleApply = async () => {
    setSaving(true); setError(null); setSuccess(null)
    try {
      const res = await client.put<{ changed: number }>(
        `/parameters/model/${encodeURIComponent(sid)}`, { values: formVals })
      setSuccess(`${res.data.changed} parameter(s) updated.`)
      onApplied()
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer
      open
      title={
        <Space>
          <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: model.color }} />
          <Text strong>{model.lib}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{model.dyn_id}</Text>
        </Space>
      }
      size={340}
      onClose={onClose}
      extra={
        <Button
          type="primary"
          size="small"
          icon={<SaveOutlined />}
          disabled={!changed}
          loading={saving}
          onClick={handleApply}
        >
          Apply
        </Button>
      }
    >
      {success && <Alert type="success" description={success} style={{ marginBottom: 8 }} />}
      {error   && <Alert type="error"   description={error}   style={{ marginBottom: 8 }} />}

      {model.pars.length > 0 && (
        <Table
          size="small"
          pagination={false}
          dataSource={model.pars}
          rowKey="name"
          columns={[
            {
              title: 'Name', dataIndex: 'name',
              render: (n: string) => <Text code style={{ fontSize: 11 }}>{n}</Text>,
            },
            {
              title: 'Value', key: 'val', width: 120,
              render: (_: unknown, par: Par) => {
                const val = formVals[par.name] ?? par.value
                const dirty = !valuesEqual(par.value, val, par.type)
                if (par.type === 'BOOL') {
                  return (
                    <Checkbox
                      checked={val.toLowerCase() === 'true'}
                      onChange={e => setFormVals(prev => ({ ...prev, [par.name]: e.target.checked ? 'true' : 'false' }))}
                    />
                  )
                }
                if (par.type === 'INT') {
                  return (
                    <InputNumber
                      size="small"
                      value={parseInt(val)}
                      step={1}
                      precision={0}
                      style={{ width: '100%', borderColor: dirty ? '#faad14' : undefined }}
                      onChange={v => setFormVals(prev => ({ ...prev, [par.name]: String(v ?? par.value) }))}
                    />
                  )
                }
                return (
                  <InputNumber
                    size="small"
                    value={parseFloat(val)}
                    style={{ width: '100%', borderColor: dirty ? '#faad14' : undefined }}
                    onChange={v => setFormVals(prev => ({ ...prev, [par.name]: String(v ?? par.value) }))}
                  />
                )
              },
            },
          ]}
        />
      )}

      {model.refs.length > 0 && (
        <>
          <Title level={5} style={{ marginTop: 16 }}>IIDM references</Title>
          <Table
            size="small"
            pagination={false}
            dataSource={model.refs}
            rowKey="name"
            columns={[
              { title: 'Name',   dataIndex: 'name',     render: (n: string) => <Text code style={{ fontSize: 11 }}>{n}</Text> },
              { title: 'Source', key: 'src', render: (_: unknown, r: Ref) => <Text type="secondary" style={{ fontSize: 11 }}>← {r.origName}</Text> },
            ]}
          />
        </>
      )}

      {model.pars.length === 0 && model.refs.length === 0 && (
        <Text type="secondary">No parameters found for set ID `{model.parId}`.</Text>
      )}
    </Drawer>
  )
}

// ── SLD search helpers ────────────────────────────────────────────────────────

interface SldCascaderOption {
  value: string
  label: string
  vl_id?: string
  children?: SldCascaderOption[]
}

const TYPE_LABEL: Record<string, string> = {
  voltage_level:            'Voltage Levels',
  generator:                'Generators',
  load:                     'Loads',
  line:                     'Lines',
  two_winding_transformer:  '2W Transformers',
  three_winding_transformer:'3W Transformers',
  shunt_compensator:        'Shunt Compensators',
  static_var_compensator:   'SVCs',
  dangling_line:            'Dangling Lines',
  battery:                  'Batteries',
  lcc_converter_station:    'LCC Stations',
  vsc_converter_station:    'VSC Stations',
  busbar_section:           'Busbar Sections',
}

const TYPE_ORDER = [
  'voltage_level', 'generator', 'load', 'line',
  'two_winding_transformer', 'three_winding_transformer',
  'shunt_compensator', 'static_var_compensator', 'dangling_line',
  'battery', 'lcc_converter_station', 'vsc_converter_station', 'busbar_section',
]

function buildCascaderOptions(
  elements: NetworkElement[],
  dynModels?: Record<string, DynModel>,
): SldCascaderOption[] {
  const grouped: Record<string, NetworkElement[]> = {}
  for (const e of elements) {
    if (!grouped[e.type]) grouped[e.type] = []
    grouped[e.type].push(e)
  }

  const elementById: Record<string, NetworkElement> = {}
  for (const e of elements) elementById[e.id] = e

  const iidmOptions: SldCascaderOption[] = TYPE_ORDER
    .filter(t => grouped[t]?.length)
    .map(t => ({
      value: t,
      label: TYPE_LABEL[t] ?? t,
      children: grouped[t].map(e => {
        const mainLabel = e.name ? `${e.id} — ${e.name}` : e.id
        const vlLabel = e.vl_ids.length > 1 ? e.vl_ids.join(' ↔ ') : e.vl_ids[0]
        return {
          value: e.id,
          label: e.type === 'voltage_level' ? mainLabel : `${mainLabel} (${vlLabel})`,
          vl_id: e.vl_ids[0],
        }
      }),
    }))

  if (!dynModels || Object.keys(dynModels).length === 0) return iidmOptions

  // One group per library; each leaf is the IIDM component using that library
  const byLib: Record<string, SldCascaderOption[]> = {}
  for (const [sid, model] of Object.entries(dynModels)) {
    if (!byLib[model.lib]) byLib[model.lib] = []
    const elem = elementById[sid]
    const vl_id = elem?.vl_ids[0]
    const baseLabel = elem?.name ? `${sid} — ${elem.name}` : sid
    byLib[model.lib].push({
      value: sid,
      label: vl_id ? `${baseLabel} (${vl_id})` : baseLabel,
      vl_id,
    })
  }

  const dynOptions: SldCascaderOption[] = Object.entries(byLib)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([lib, children]) => ({ value: `dyn_lib:${lib}`, label: lib, children }))

  return [
    ...iidmOptions,
    { value: '__dyn_models__', label: 'Dynamic Models', children: dynOptions },
  ]
}

// ── SLD Tab ───────────────────────────────────────────────────────────────────

// Module-level: survives component unmounts
const sldCache = new Map<string, { svg: string; feeders: Record<string, string> }>()
let lastSldVl: { networkBase: string; vl: string } | null = null

function SldTab({ networkBase, diffMode, diffConfig }: {
  networkBase: string; diffMode: boolean; diffConfig: DiffConfig
}) {
  const isUploadedNetwork = networkBase === '/network'
  const activeDiff = diffMode && !isUploadedNetwork
  const diffQs = activeDiff ? `?${diffQueryString(diffConfig)}` : ''

  const [elements, setElements] = useState<NetworkElement[]>([])
  // Restore last VL so the user returns to the same diagram after navigating away
  const [selectValue, setSelectValue] = useState<string[] | null>(() =>
    lastSldVl?.networkBase === networkBase && lastSldVl.vl
      ? ['voltage_level', lastSldVl.vl]
      : null
  )
  const [selectedVl, setSelectedVl] = useState<string | null>(() =>
    lastSldVl?.networkBase === networkBase ? lastSldVl.vl : null
  )
  const [sldData, setSldData] = useState<{ svg: string; feeders: Record<string, string> } | null>(null)
  const [dynModels, setDynModels] = useState<Record<string, DynModel>>({})
  const [showOverlay, setShowOverlay] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [panelSid, setPanelSid] = useState<string | null>(null)
  const svgContainerRef = useRef<HTMLDivElement>(null)

  // Stable ref so the SVG effect always sees the current feeder/dyn data
  const feedersRef = useRef<Record<string, string>>({})
  const dynModelsRef = useRef<Record<string, DynModel>>({})
  // Map svgId → sid
  const svgIdToSid = useRef<Record<string, string>>({})

  // Re-fetch elements when source changes; preserve the current VL if it exists in the new network
  useEffect(() => {
    setError(null)
    const fetchElements = () =>
      client.get<{ elements: NetworkElement[] }>(`${networkBase}/elements`)
        .then(r => {
          const elems = r.data.elements
          setElements(elems)
          setSelectedVl(prevVl => {
            const vlExists = prevVl !== null && elems.some(e => e.type === 'voltage_level' && e.id === prevVl)
            const newVl = vlExists ? prevVl : (elems.find(e => e.type === 'voltage_level')?.id ?? null)
            // Always normalise the cascader to the VL path so it stays valid after a source switch
            if (newVl) setSelectValue(['voltage_level', newVl])
            return newVl
          })
        })

    const run = isUploadedNetwork
      ? () => fetchElements().catch(async e => {
          if (e.response?.status === 404) { await ensureNetworkLoaded(); await fetchElements() }
          else throw e
        })
      : fetchElements

    run().catch(e => setError(e.message ?? 'No network loaded. Upload an IIDM file first.'))
  }, [networkBase])

  // Re-fetch dyn-models when switching to/from the uploaded network
  useEffect(() => {
    if (!isUploadedNetwork) {
      setDynModels({})
      dynModelsRef.current = {}
      svgIdToSid.current = {}
      return
    }
    client.get<Record<string, DynModel>>('/network/dyn-models')
      .then(r => {
        setDynModels(r.data)
        dynModelsRef.current = r.data
        const rev: Record<string, string> = {}
        for (const sid of Object.keys(r.data)) rev[svgId(sid)] = sid
        svgIdToSid.current = rev
      })
      .catch(() => {})
  }, [isUploadedNetwork])

  // Persist the current VL so it can be restored after navigating away and back
  useEffect(() => {
    if (selectedVl) lastSldVl = { networkBase, vl: selectedVl }
  }, [selectedVl, networkBase])

  // Re-fetch SLD when VL, source, diff mode, or diff config changes
  useEffect(() => {
    if (!selectedVl) return
    const url = activeDiff
      ? `${networkBase}/diff/sld/${encodeURIComponent(selectedVl)}${diffQs}`
      : `${networkBase}/sld/${encodeURIComponent(selectedVl)}`
    const cached = sldCache.get(url)
    if (cached) { setSldData(cached); feedersRef.current = cached.feeders; return }
    setLoading(true)
    setSldData(null)
    client.get<{ svg: string; feeders: Record<string, string> }>(url)
      .then(r => { sldCache.set(url, r.data); setSldData(r.data); feedersRef.current = r.data.feeders })
      .catch(e => setError(e.response?.data?.detail ?? 'Failed to render SLD'))
      .finally(() => setLoading(false))
  }, [selectedVl, networkBase, diffQs])

  // When overlay is turned off while a dyn-model entry is selected, snap back to the current VL
  useEffect(() => {
    if (!showOverlay && selectValue?.[0]?.startsWith('dyn_lib:') && selectedVl) {
      setSelectValue(['voltage_level', selectedVl])
    }
  }, [showOverlay])

  // Re-fetch dyn model par data after a panel Apply (par values may have changed)
  const refreshDynModels = () => {
    client.get<Record<string, DynModel>>('/network/dyn-models')
      .then(r => {
        setDynModels(r.data)
        dynModelsRef.current = r.data
        setSldData(prev => prev ? { ...prev } : null)
      })
      .catch(() => {})
  }

  // Attach feeder + dynamic click listeners after SVG is rendered
  const handleSvgReady = (svgEl: SVGSVGElement) => {
    svgEl.addEventListener('click', (e: Event) => {
      const target = e.target as Element

      let node: Element | null = target
      while (node && node !== svgEl) {
        if (node.id && feedersRef.current[node.id]) {
          const newVl = feedersRef.current[node.id]
          setSelectedVl(newVl)
          setSelectValue(['voltage_level', newVl])
          return
        }
        node = node.parentElement
      }

      if (isUploadedNetwork) {
        node = target
        while (node && node !== svgEl) {
          if (node.id && svgIdToSid.current[node.id]) {
            setPanelSid(svgIdToSid.current[node.id])
            return
          }
          node = node.parentElement
        }
      }
    })

    svgEl.addEventListener('mouseover', (e: Event) => {
      const target = e.target as Element
      let node: Element | null = target
      while (node && node !== svgEl) {
        if (node.id && (feedersRef.current[node.id] || (isUploadedNetwork && svgIdToSid.current[node.id]))) {
          svgEl.style.cursor = 'pointer'
          return
        }
        node = node.parentElement
      }
      svgEl.style.cursor = 'grab'
    })
  }

  const displaySvg = sldData
    ? (isUploadedNetwork && showOverlay && Object.keys(dynModels).length > 0 ? injectDynCss(sldData.svg, dynModels) : sldData.svg)
    : null

  const libColors = Object.values(dynModels).reduce<Record<string, string>>((acc, m) => {
    acc[m.lib] = m.color; return acc
  }, {})

  const libCounts = Object.values(dynModels).reduce<Record<string, number>>((acc, m) => {
    acc[m.lib] = (acc[m.lib] ?? 0) + 1; return acc
  }, {})

  if (error) return <Alert type="warning" description={error} />

  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Cascader<SldCascaderOption>
          options={buildCascaderOptions(elements, isUploadedNetwork && showOverlay ? dynModels : undefined)}
          value={selectValue ?? undefined}
          onChange={(value: (string | number)[], selectedOptions: SldCascaderOption[]) => {
            const leaf = selectedOptions[selectedOptions.length - 1]
            if (leaf?.vl_id) {
              setSelectedVl(leaf.vl_id)
              setSelectValue(value as string[])
            }
          }}
          showSearch={{
            filter: (input, path) => {
              const q = input.toLowerCase()
              return path.some(opt =>
                opt.label.toLowerCase().includes(q) ||
                opt.value.toLowerCase().includes(q)
              )
            },
          }}
          displayRender={labels => labels.join(' › ')}
          expandTrigger="hover"
          placeholder="Search voltage level, generator, load, dyn model…"
          style={{ width: 420 }}
        />
        {isUploadedNetwork && Object.keys(dynModels).length > 0 && (
          <Tooltip title="Highlight components that have a dynamic model in the .dyd file">
            <Space>
              <Switch checked={showOverlay} onChange={setShowOverlay} size="small" />
              <Text>Dynamic overlay</Text>
            </Space>
          </Tooltip>
        )}
      </Space>

      <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
        Scroll to zoom · Drag to pan · Double-click to reset · Click a feeder arrow to navigate
        {isUploadedNetwork && ' · Click a glowing element to edit its parameters'}
      </Text>

      {loading && <Spin description="Rendering…" style={{ display: 'block', marginTop: 40 }} />}

      {displaySvg && (
        <div ref={svgContainerRef}>
          <SvgViewer
            key={selectedVl + (isUploadedNetwork && showOverlay ? '_dyn' : '_raw')}
            svg={displaySvg}
            height={700}
            onSvgReady={handleSvgReady}
          />
        </div>
      )}

      {/* Legend */}
      {isUploadedNetwork && showOverlay && Object.keys(libColors).length > 0 && (
        <Card size="small" style={{ marginTop: 12 }}>
          <Space wrap>
            <Text strong style={{ fontSize: 12 }}>Dynamic model legend:</Text>
            {Object.entries(libColors).map(([lib, color]) => (
              <Space key={lib} size={4}>
                <span style={{
                  display: 'inline-block', width: 12, height: 12, borderRadius: 2,
                  background: color, boxShadow: `0 0 6px ${color}`, verticalAlign: 'middle',
                }} />
                <Text style={{ fontSize: 12 }}>{lib} ({libCounts[lib] ?? 0})</Text>
              </Space>
            ))}
          </Space>
        </Card>
      )}

      {isUploadedNetwork && showOverlay && Object.keys(dynModels).length > 0 && (
        <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
          {Object.keys(dynModels).length} component(s) have a dynamic model.
        </Text>
      )}

      {/* Dynamic model side panel */}
      {panelSid && dynModels[panelSid] && (
        <DynModelPanel
          sid={panelSid}
          model={dynModels[panelSid]}
          onClose={() => setPanelSid(null)}
          onApplied={refreshDynModels}
        />
      )}
    </>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// "uploaded" | "run:<id>" | "file:<filename>"
function initialSource(searchParams: URLSearchParams): string {
  const iidmFile = searchParams.get('iidmFile')
  if (iidmFile) return `file:${iidmFile}`
  const finalStateRun = searchParams.get('finalStateRun')
  if (finalStateRun) return `run:${finalStateRun}`
  return 'uploaded'
}

export default function NetworkView() {
  const [searchParams] = useSearchParams()
  const [runsWithFinalState, setRunsWithFinalState] = useState<RunEntry[]>([])
  const [iidmFiles, setIidmFiles] = useState<string[]>([])
  const [uploadedFilename, setUploadedFilename] = useState<string | null>(null)
  const [source, setSource] = useState<string>(() => initialSource(searchParams))
  const [diffMode, setDiffMode] = useState(false)
  const [diffConfig, setDiffConfig] = useState<DiffConfig>(DEFAULT_DIFF_CONFIG)

  useEffect(() => {
    client.get<RunEntry[]>('/simulation/runs')
      .then(r => setRunsWithFinalState(r.data.filter(run => run.has_final_state_iidm)))
      .catch(() => {})

    const loadUploadedFilename = () =>
      client.get<{ filename: string }>('/network/summary').then(r => setUploadedFilename(r.data.filename))

    // /network/summary 404s if no network has been loaded into the session yet
    // (this happens before any tab has triggered ensureNetworkLoaded) — load it once here too.
    loadUploadedFilename().catch(async e => {
      if (e.response?.status === 404) {
        try { await ensureNetworkLoaded(); await loadUploadedFilename() } catch { /* no IIDM uploaded yet */ }
      }
    })

    client.get<{ name: string; ftype: string }[]>('/files/')
      .then(r => setIidmFiles(r.data.filter(f => f.ftype === 'iidm').map(f => f.name)))
      .catch(() => {})
  }, [])

  const networkBase = source === 'uploaded'
    ? '/network'
    : source.startsWith('run:')
      ? `/network/run/${source.slice(4)}`
      : `/network/file/${encodeURIComponent(source.slice(5))}`

  const sourceOptions = [
    { value: 'uploaded', label: 'Uploaded IIDM' },
    ...iidmFiles
      .filter(f => f !== uploadedFilename)
      .map(f => ({ value: `file:${f}`, label: f })),
    ...runsWithFinalState.map(run => ({ value: `run:${run.run_id}`, label: `${run.label} — final state` })),
  ]

  const handleSourceChange = (v: string) => {
    setSource(v)
    if (v === 'uploaded') setDiffMode(false)
  }

  const colourPicker = (value: string, onChange: (c: string) => void) => (
    <input
      type="color"
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{ width: 40, height: 24, cursor: 'pointer', border: 'none', padding: 0, background: 'none' }}
    />
  )

  const diffSettingsContent = (
    <div style={{ width: 250 }}>
      <Flex vertical gap={8} style={{ width: '100%' }}>
        <Space>
          <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Moderate threshold (%):</Text>
          <InputNumber
            size="small" min={0.1} max={99} step={0.5}
            value={diffConfig.moderatePct}
            onChange={v => setDiffConfig(prev => ({ ...prev, moderatePct: v ?? 1 }))}
            style={{ width: 65 }}
          />
        </Space>
        <Space>
          <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Large threshold (%):</Text>
          <InputNumber
            size="small" min={0.1} max={100} step={0.5}
            value={diffConfig.largePct}
            onChange={v => setDiffConfig(prev => ({ ...prev, largePct: v ?? 5 }))}
            style={{ width: 65 }}
          />
        </Space>

        <Space>
          <Switch
            size="small"
            checked={diffConfig.signed}
            onChange={v => setDiffConfig(prev => ({ ...prev, signed: v }))}
          />
          <Text style={{ fontSize: 12 }}>Signed deviations (+ / −)</Text>
        </Space>

        {diffConfig.signed ? (
          <>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Increase moderate:</Text>
              {colourPicker(diffConfig.moderateColour, c => setDiffConfig(prev => ({ ...prev, moderateColour: c })))}
            </Space>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Increase large:</Text>
              {colourPicker(diffConfig.largeColour, c => setDiffConfig(prev => ({ ...prev, largeColour: c })))}
            </Space>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Decrease moderate:</Text>
              {colourPicker(diffConfig.negModerateColour, c => setDiffConfig(prev => ({ ...prev, negModerateColour: c })))}
            </Space>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Decrease large:</Text>
              {colourPicker(diffConfig.negLargeColour, c => setDiffConfig(prev => ({ ...prev, negLargeColour: c })))}
            </Space>
          </>
        ) : (
          <>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Moderate colour:</Text>
              {colourPicker(diffConfig.moderateColour, c => setDiffConfig(prev => ({ ...prev, moderateColour: c })))}
            </Space>
            <Space>
              <Text style={{ fontSize: 12, width: 140, display: 'inline-block' }}>Large colour:</Text>
              {colourPicker(diffConfig.largeColour, c => setDiffConfig(prev => ({ ...prev, largeColour: c })))}
            </Space>
          </>
        )}

        <div>
          <Text style={{ fontSize: 12 }}>Highlighted quantities:</Text>
          <Checkbox.Group
            options={QUANTITY_OPTIONS}
            value={diffConfig.quantities}
            onChange={vals => setDiffConfig(prev => ({ ...prev, quantities: vals as string[] }))}
            style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}
          />
        </div>
        <Button size="small" onClick={() => setDiffConfig(DEFAULT_DIFF_CONFIG)}>
          Reset to defaults
        </Button>
      </Flex>
    </div>
  )

  const diffLegend = diffConfig.signed
    ? [
        { colour: diffConfig.moderateColour,    label: `Increase ${diffConfig.moderatePct} – ${diffConfig.largePct} %` },
        { colour: diffConfig.largeColour,        label: `Increase > ${diffConfig.largePct} %` },
        { colour: diffConfig.negModerateColour,  label: `Decrease ${diffConfig.moderatePct} – ${diffConfig.largePct} %` },
        { colour: diffConfig.negLargeColour,     label: `Decrease > ${diffConfig.largePct} %` },
      ]
    : [
        { colour: diffConfig.moderateColour, label: `Moderate change (${diffConfig.moderatePct} – ${diffConfig.largePct} %)` },
        { colour: diffConfig.largeColour,    label: `Large change (> ${diffConfig.largePct} %)` },
      ]

  return (
    <div style={{ maxWidth: 1100 }}>
      <Title level={3}>Network View</Title>

      {sourceOptions.length > 1 && (
        <Space style={{ marginBottom: 12 }} wrap>
          <Text type="secondary" style={{ fontSize: 13 }}>Network source:</Text>
          <Select
            value={source}
            onChange={handleSourceChange}
            options={sourceOptions}
            style={{ width: 280 }}
            size="small"
          />
          {source !== 'uploaded' && (
            <Space size={6}>
              <Switch
                size="small"
                checked={diffMode}
                onChange={setDiffMode}
              />
              <Text style={{ fontSize: 13 }}>Diff view</Text>
              <Popover
                content={diffSettingsContent}
                title="Diff thresholds & colours"
                trigger="click"
                placement="bottomLeft"
              >
                <Tooltip title="Configure diff settings">
                  <Button size="small" icon={<SettingOutlined />} type="text" />
                </Tooltip>
              </Popover>
            </Space>
          )}
        </Space>
      )}

      {diffMode && source !== 'uploaded' && (
        <Card size="small" style={{ marginBottom: 12, display: 'inline-block' }}>
          <Space size={16}>
            <Text strong style={{ fontSize: 12 }}>Change legend:</Text>
            {diffLegend.map(({ colour, label }) => (
              <Space key={colour} size={4}>
                <span style={{
                  display: 'inline-block', width: 12, height: 12, borderRadius: 2,
                  background: colour, verticalAlign: 'middle',
                }} />
                <Text style={{ fontSize: 12 }}>{label}</Text>
              </Space>
            ))}
          </Space>
        </Card>
      )}

      <Tabs
        defaultActiveKey="nad"
        items={[
          {
            key: 'nad',
            label: 'Network Area Diagram',
            children: <NadTab key={networkBase + (diffMode ? '_diff' : '')} networkBase={networkBase} diffMode={diffMode} diffConfig={diffConfig} />,
          },
          {
            key: 'sld',
            label: 'Single Line Diagram',
            children: <SldTab networkBase={networkBase} diffMode={diffMode} diffConfig={diffConfig} />,
          },
        ]}
      />
    </div>
  )
}
