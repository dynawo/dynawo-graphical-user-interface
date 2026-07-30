//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { loadCurves, saveCurves, curvesCache } from './curvesStore'
import type { AxisSide, DashStyle, DerivedCurve, CurvesInfo, CurvesData, Placement } from './curvesStore'
import {
  Alert, Button, Card, Checkbox, Collapse, Divider, Flex, Input,
  Popconfirm, Select, Space, Spin, Tag, Typography,
} from 'antd'
import {
  DeleteOutlined, PlayCircleOutlined, RedoOutlined,
  StopOutlined,
} from '@ant-design/icons'
import Plotly from 'plotly.js-dist-min'
import client from '../api/client'
import SimOutputsModal, { type ConstraintItem, type LostEquipmentItem, type TimelineEvent } from '../components/SimOutputsModal'

const { Title, Text } = Typography

// ── Types ─────────────────────────────────────────────────────────────────────

interface RunSummary {
  run_id: number
  label: string
  jobs_file: string
  output_dir: string
  returncode: number | null
  start_time: number
  stop_time: number
  started_at: number | null
  finished_at: number | null
  has_output: boolean
  has_final_state_iidm: boolean
  has_timeline: boolean
  has_constraints: boolean
  has_log: boolean
  has_lost_equipments: boolean
}

// CurvesInfo, CurvesData, DerivedCurve, DashStyle are defined in curvesStore

// ── Helpers ───────────────────────────────────────────────────────────────────

const COLORS = [
  '#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A',
  '#19D3F3', '#FF6692', '#B6E880', '#FF97FF', '#FECB52',
]
const DASH_OPTS = ['solid', 'dash', 'dot', 'dashdot'] as const

const DEFAULT_PLACEMENT: Placement = { plot: 0, side: 'left' }
// Plotly's typings can't express dynamically numbered axes (yaxis5, x3, …), so
// the axis map is kept loosely typed and cast once when handed to Plotly.
type AxisMap = Record<string, Record<string, unknown>>
// Vertical gap between two stacked subplots, as a fraction of the plot area.
const SUBPLOT_GAP = 0.09

// Plotly axis names. Each subplot slot owns two y axes: an odd-numbered one for
// its left side and the next even-numbered one overlaying it on the right.
// Slot 0 → y / y2, slot 1 → y3 / y4, ...
function yAxisNum(slot: number, side: AxisSide): number {
  return 2 * slot + (side === 'left' ? 1 : 2)
}
function yRef(slot: number, side: AxisSide): string {
  const n = yAxisNum(slot, side)
  return n === 1 ? 'y' : `y${n}`
}
function yKey(slot: number, side: AxisSide): string {
  const n = yAxisNum(slot, side)
  return n === 1 ? 'yaxis' : `yaxis${n}`
}
// Each subplot gets its own x axis so hover labels stay local to it; they are
// tied together with `matches` so zooming/panning time stays synchronised.
function xRef(slot: number): string {
  return slot === 0 ? 'x' : `x${slot + 1}`
}
function xKey(slot: number): string {
  return slot === 0 ? 'xaxis' : `xaxis${slot + 1}`
}

// Top-to-bottom vertical band of the figure occupied by a subplot slot.
function slotDomain(slot: number, nPlots: number): [number, number] {
  const h = (1 - SUBPLOT_GAP * (nPlots - 1)) / nPlots
  const top = 1 - slot * (h + SUBPLOT_GAP)
  return [Math.max(0, top - h), top]
}

function label(col: string, info: CurvesInfo): string {
  const p = info[col]
  return p ? `${p[0]} — ${p[1]}` : col
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s`
}

function computeDerived(dc: DerivedCurve, data: CurvesData): number[] | null {
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

// ── Run history item ──────────────────────────────────────────────────────────

interface RunItemProps {
  run: RunSummary
  onDelete: (id: number) => void
  onRename: (id: number, label: string) => void
  onStream: (id: number) => void
  streaming: boolean
}

function RunItem({ run, onDelete, onRename, onStream, streaming }: RunItemProps) {
  const [editLabel, setEditLabel] = useState(run.label)
  const [showOutput, setShowOutput] = useState(false)
  const [output, setOutput] = useState<string | null>(null)
  const [outputsOpen, setOutputsOpen] = useState(false)
  const navigate = useNavigate()
  const hasOutputs = run.has_timeline || run.has_constraints || run.has_log || run.has_lost_equipments

  const loadOutput = async () => {
    if (output !== null) { setShowOutput(v => !v); return }
    const res = await client.get<string>(`/simulation/${run.run_id}/output-text`).catch(() => null)
    setOutput(res?.data ?? '(no output)')
    setShowOutput(true)
  }

  // Live-tick the elapsed time while the run is still going
  const [, tick] = useState(0)
  useEffect(() => {
    if (run.returncode !== null || !run.started_at) return
    const id = setInterval(() => tick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [run.returncode, run.started_at])

  const durationText = run.started_at
    ? formatDuration((run.finished_at ?? Date.now() / 1000) - run.started_at)
    : null

  const statusTag = run.returncode === null
    ? <Tag color="processing">Running…</Tag>
    : run.returncode === 0
      ? <Tag color="success">Success</Tag>
      : <Tag color="error">Failed (rc={run.returncode})</Tag>

  return (
    <Card
      size="small"
      style={{ marginBottom: 8 }}
      title={
        <Space>
          {statusTag}
          <Text type="secondary" style={{ fontSize: 12 }}>{run.output_dir}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {run.start_time}s – {run.stop_time}s
          </Text>
          {durationText && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {run.returncode === null ? `Running for ${durationText}` : `Ran in ${durationText}`}
            </Text>
          )}
        </Space>
      }
      extra={
        <Space>
          {run.returncode === null && !streaming && (
            <Button size="small" icon={<PlayCircleOutlined />} onClick={() => onStream(run.run_id)}>
              Attach output
            </Button>
          )}
          <Popconfirm title="Delete this run and its output files?" onConfirm={() => onDelete(run.run_id)}>
            <Button size="small" icon={<DeleteOutlined />} danger />
          </Popconfirm>
        </Space>
      }
    >
      <Flex vertical gap={6} style={{ width: '100%' }}>
        <Space>
          <Input
            size="small"
            value={editLabel}
            onChange={e => setEditLabel(e.target.value)}
            onBlur={() => { if (editLabel.trim() && editLabel !== run.label) onRename(run.run_id, editLabel) }}
            style={{ width: 200 }}
          />
        </Space>
        <Space>
          {run.has_output && (
            <Button size="small" type="link" onClick={loadOutput} style={{ padding: 0 }}>
              {showOutput ? 'Hide' : 'Show'} console output
            </Button>
          )}
          {run.has_final_state_iidm && (
            <Button
              size="small"
              type="link"
              onClick={() => navigate(`/network-view?finalStateRun=${run.run_id}`)}
              style={{ padding: 0 }}
            >
              View final state
            </Button>
          )}
          {hasOutputs && (
            <Button size="small" type="link" onClick={() => setOutputsOpen(true)} style={{ padding: 0 }}>
              View outputs
            </Button>
          )}
        </Space>
        {showOutput && output !== null && (
          <pre style={{ fontSize: 11, maxHeight: 200, overflow: 'auto', background: '#1a1a1a', color: '#d4d4d4', padding: 8, borderRadius: 4 }}>
            {output}
          </pre>
        )}
      </Flex>
      <SimOutputsModal
        open={outputsOpen}
        onClose={() => setOutputsOpen(false)}
        title={`Run ${run.run_id} outputs`}
        fetchTimeline={run.has_timeline
          ? () => client.get<TimelineEvent[]>(`/simulation/${run.run_id}/timeline`).then(r => r.data)
          : undefined}
        fetchConstraints={run.has_constraints
          ? () => client.get<ConstraintItem[]>(`/simulation/${run.run_id}/constraints`).then(r => r.data)
          : undefined}
        fetchLog={run.has_log
          ? () => client.get<{ text: string }>(`/simulation/${run.run_id}/log`).then(r => r.data.text)
          : undefined}
        fetchLostEquipments={run.has_lost_equipments
          ? () => client.get<LostEquipmentItem[]>(`/simulation/${run.run_id}/lost-equipments`).then(r => r.data)
          : undefined}
      />
    </Card>
  )
}

// ── Plotly chart wrapper ──────────────────────────────────────────────────────

function PlotlyChart({ traces, axes, height }: { traces: Plotly.Data[]; axes: AxisMap; height: number }) {
  const divRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!divRef.current) return
    Plotly.react(
      divRef.current,
      traces as Plotly.Data[],
      {
        hovermode: 'x unified',
        legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1 },
        margin: { t: 40 },
        autosize: true,
        ...axes,
      } as unknown as Partial<Plotly.Layout>,
      { responsive: true },
    )
  }, [traces, axes])

  // Plotly only re-reads the container size on resize, so force a relayout when
  // the number of subplots changes the height.
  useEffect(() => {
    if (divRef.current) Plotly.Plots.resize(divRef.current)
  }, [height])

  return <div ref={divRef} style={{ width: '100%', height }} />
}

// ── Curves builder ────────────────────────────────────────────────────────────

interface CurvesProps {
  runs: RunSummary[]
  jobsFile: string
}

function CurvesBuilder({ runs, jobsFile }: CurvesProps) {
  // Curves are attempted for any finished run, even a failed/diverged one — Dynawo
  // normally still writes out the curves file up to the point of divergence.
  const okRuns = runs.filter(r => r.returncode !== null && r.jobs_file === jobsFile)

  // Restore user selections from sessionStorage (survives navigation & HMR)
  const [saved]           = useState(loadCurves)
  const [runData,         setRunData]         = useState<Record<number, CurvesData>>(() => curvesCache.runData)
  const [curvesInfo,      setCurvesInfo]      = useState<CurvesInfo>(() => curvesCache.curvesInfo)
  const [selectedRuns,    setSelectedRuns]    = useState<Set<number>>(() => new Set(saved?.selectedRuns ?? []))
  const [selectedCols,    setSelectedCols]    = useState<Set<string>>(() => new Set(saved?.selectedCols ?? []))
  const [dashStyles,      setDashStyles]      = useState<Record<string, DashStyle>>(() => saved?.dashStyles ?? {})
  const [placements,      setPlacements]      = useState<Record<string, Placement>>(() => saved?.placements ?? {})
  const [derivedCurves,   setDerivedCurves]   = useState<DerivedCurve[]>(() => saved?.derivedCurves ?? [])
  const [selectedDerived, setSelectedDerived] = useState<Set<number>>(() => new Set(saved?.selectedDerived ?? []))
  const [openModels,      setOpenModels]      = useState<string[]>(() => saved?.openModels ?? [])
  const [panelWidth,      setPanelWidth]      = useState<number>(() => saved?.panelWidth ?? 320)

  // Persist selections to sessionStorage whenever they change
  useEffect(() => {
    saveCurves({
      selectedRuns:    [...selectedRuns],
      selectedCols:    [...selectedCols],
      dashStyles,
      placements,
      derivedCurves,
      selectedDerived: [...selectedDerived],
      openModels,
      panelWidth,
    })
  }, [selectedRuns, selectedCols, dashStyles, placements, derivedCurves, selectedDerived, openModels, panelWidth])

  // Keep memory cache up to date for instant restore within the same page session
  useEffect(() => { curvesCache.runData    = runData    }, [runData])
  useEffect(() => { curvesCache.curvesInfo = curvesInfo }, [curvesInfo])

  const [fetchingCurves, setFetchingCurves] = useState(false)
  const [curvesError, setCurvesError] = useState<string | null>(null)
  // derived curve builder form state
  const [dcOp, setDcOp] = useState<'sum' | 'diff' | 'scale'>('sum')
  const [dcPicks, setDcPicks] = useState<string[]>([])
  const [dcBase, setDcBase] = useState<string>('')
  const [dcFactor, setDcFactor] = useState<string>('1')
  const [dcName, setDcName] = useState<string>('')

  // Load curves data and info for each new ok run
  useEffect(() => {
    if (okRuns.length === 0) return
    const missing = okRuns.filter(run => !curvesCache.runData[run.run_id])
    if (missing.length === 0) {
      // Data already in store — ensure selectedRuns covers these runs
      setSelectedRuns(prev => {
        const hasMatch = okRuns.some(r => prev.has(r.run_id))
        return hasMatch ? prev : new Set(okRuns.map(r => r.run_id))
      })
      return
    }

    setFetchingCurves(true)
    setCurvesError(null)

    const dataPromises = missing.map(run =>
      client.get<CurvesData>(`/simulation/${run.run_id}/curves/data`)
        .then(r => setRunData(prev => ({ ...prev, [run.run_id]: r.data })))
        .catch((e: any) => {
          if (e.response?.status !== 404) {
            setCurvesError(e.response?.data?.detail ?? 'Failed to load curves data.')
          }
          // 404 = no curves CSV for this run (expected for runs before a CRV file was created)
        })
    )

    // Merge info from every new run so columns added in later runs become visible
    const infoPromise = Promise.all(
      missing.map(run =>
        client.get<CurvesInfo>(`/simulation/${run.run_id}/curves/info`)
          .then(r => r.data)
          .catch(() => ({} as CurvesInfo))
      )
    ).then(infos => setCurvesInfo(prev => Object.assign({}, prev, ...infos)))

    Promise.all([...dataPromises, infoPromise]).finally(() => setFetchingCurves(false))

    setSelectedRuns(prev => {
      const hasMatch = okRuns.some(r => prev.has(r.run_id))
      return hasMatch ? prev : new Set(okRuns.map(r => r.run_id))
    })
  }, [okRuns.length])

  const dragging = useRef(false)

  const onDragStart = (e: React.MouseEvent) => {
    dragging.current = true
    const startX = e.clientX
    const startW = panelWidth
    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      setPanelWidth(Math.max(220, Math.min(640, startW + ev.clientX - startX)))
    }
    const onUp = () => { dragging.current = false; window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  if (okRuns.length === 0) return null

  if (fetchingCurves) return <Spin description="Loading curves…" style={{ display: 'block', marginTop: 20 }} />
  if (curvesError) return <Alert type="warning" description={curvesError} style={{ marginTop: 12 }} />

  if (Object.keys(runData).length === 0) return <Alert type="info" description="No curve data available. The simulation may not have produced a curves CSV file." style={{ marginTop: 12 }} />

  // Union of columns across all runs so signals added in later runs are visible
  const allCols = [...new Set(Object.values(runData).flatMap(d => Object.keys(d.signals)))]
  const multiRun = selectedRuns.size > 1

  // Group signals by model — skip columns with no crv info
  const byModel: Record<string, string[]> = {}
  for (const col of allCols) {
    const model = curvesInfo[col]?.[0]
    if (!model) continue
    ;(byModel[model] ??= []).push(col)
  }

  // ── Subplot / axis layout ───────────────────────────────────────────────────
  // Every plotted curve carries a placement (subplot index + left/right axis).
  // Placements the user has moved away from leave holes in the numbering, so the
  // indices actually in use are compacted to consecutive display slots.
  const placementOf = (key: string): Placement => placements[key] ?? DEFAULT_PLACEMENT
  const plottedKeys = [
    ...[...selectedCols].filter(col => Object.values(runData).some(d => col in d.signals)),
    ...[...selectedDerived].filter(i => derivedCurves[i]).map(i => `dc_${i}`),
  ]
  const usedPlots = [...new Set(plottedKeys.map(k => placementOf(k).plot))].sort((a, b) => a - b)
  const slotOfPlot = new Map(usedPlots.map((p, i) => [p, i]))
  const nPlots = Math.max(1, usedPlots.length)
  const slotOf = (key: string) => slotOfPlot.get(placementOf(key).plot) ?? 0
  // Which axes actually carry data, so empty ones can be hidden.
  const liveAxes = new Set(plottedKeys.map(k => `${slotOf(k)}:${placementOf(k).side}`))

  // Build Plotly traces
  const traces: Plotly.Data[] = []
  let colorIdx = 0
  for (const runId of selectedRuns) {
    const data = runData[runId]
    if (!data) continue
    const run = okRuns.find(r => r.run_id === runId)
    if (!run) continue
    for (const col of selectedCols) {
      if (!(col in data.signals)) continue
      const dash = dashStyles[col] ?? 'solid'
      const slot = slotOf(col)
      traces.push({
        type: 'scatter', mode: 'lines',
        x: data.time, y: data.signals[col],
        name: multiRun ? `${run.label} — ${label(col, curvesInfo)}` : label(col, curvesInfo),
        line: { color: COLORS[colorIdx % COLORS.length], dash },
        xaxis: xRef(slot), yaxis: yRef(slot, placementOf(col).side),
      } as Plotly.Data)
      colorIdx++
    }
    for (const idx of selectedDerived) {
      const dc = derivedCurves[idx]
      if (!dc) continue
      const vals = computeDerived(dc, data)
      if (vals) {
        const key = `dc_${idx}`
        const slot = slotOf(key)
        traces.push({
          type: 'scatter', mode: 'lines',
          x: data.time, y: vals,
          name: multiRun ? `${run.label} — ${dc.name}` : dc.name,
          line: { color: COLORS[colorIdx % COLORS.length], dash: dashStyles[key] ?? 'solid' },
          xaxis: xRef(slot), yaxis: yRef(slot, placementOf(key).side),
        } as Plotly.Data)
        colorIdx++
      }
    }
  }

  // Stacked subplots: each slot owns a vertical band with a left y axis, an
  // optional right y axis overlaying it, and an x axis matched to the first one.
  // Only the bottom subplot carries the time ticks and title.
  const axes: AxisMap = {}
  for (let slot = 0; slot < nPlots; slot++) {
    const domain = slotDomain(slot, nPlots)
    const bottom = slot === nPlots - 1
    axes[xKey(slot)] = {
      domain: [0, 1],
      anchor: yRef(slot, 'left'),
      showticklabels: bottom,
      ...(bottom ? { title: { text: 'Time (s)' } } : {}),
      ...(slot === 0 ? {} : { matches: 'x' }),
    }
    axes[yKey(slot, 'left')] = {
      domain,
      anchor: xRef(slot),
      // A subplot may hold right-axis curves only — don't show an empty left scale.
      visible: liveAxes.has(`${slot}:left`) || !liveAxes.has(`${slot}:right`),
    }
    if (liveAxes.has(`${slot}:right`)) {
      axes[yKey(slot, 'right')] = {
        domain,
        anchor: xRef(slot),
        overlaying: yRef(slot, 'left'),
        side: 'right',
      }
    }
  }
  const chartHeight = nPlots === 1 ? 500 : Math.min(1400, 300 * nPlots)

  // Options for the per-curve placement picker: every existing subplot (left or
  // right axis) plus a slot for a brand-new subplot at the bottom.
  const placementOptions = [
    ...Array.from({ length: nPlots }, (_, slot) => [
      { label: `Plot ${slot + 1} L`, value: `${slot}:left` },
      { label: `Plot ${slot + 1} R`, value: `${slot}:right` },
    ]).flat(),
    { label: '+ New plot', value: `${nPlots}:left` },
  ]

  // Value shown by a curve's picker. A curve that is not currently plotted can
  // still hold a subplot index no visible curve uses, so show the slot it would
  // land on once enabled — or "+ New plot" if that would be a fresh subplot.
  const placementValue = (key: string): string => {
    const { plot, side } = placementOf(key)
    const known = slotOfPlot.get(plot)
    if (known !== undefined) return `${known}:${side}`
    const wouldBe = [...new Set([...usedPlots, plot])].sort((a, b) => a - b).indexOf(plot)
    return wouldBe >= nPlots ? `${nPlots}:left` : `${wouldBe}:${side}`
  }
  const setPlacement = (key: string, value: string) => {
    const [plot, side] = value.split(':')
    setPlacements(prev => ({ ...prev, [key]: { plot: Number(plot), side: side as AxisSide } }))
  }

  const placementSelect = (key: string) => (
    <Select
      size="small"
      value={placementValue(key)}
      options={placementOptions}
      onChange={v => setPlacement(key, v)}
      style={{ width: 96 }}
      title="Plot and axis (L/R) this curve is drawn on"
    />
  )

  const addDerived = () => {
    if (!dcName.trim()) return
    const dc: DerivedCurve = dcOp === 'scale'
      ? { name: dcName, op: 'scale', picks: [], base: dcBase, factor: parseFloat(dcFactor) || 1 }
      : { name: dcName, op: dcOp, picks: dcPicks }
    setDerivedCurves(prev => {
      const next = [...prev, dc]
      setSelectedDerived(s => { const ns = new Set(s); ns.add(next.length - 1); return ns })
      return next
    })
    setDcName('')
    setDcPicks([])
  }

  return (
    <>
      <Divider>Curves</Divider>
      <div style={{ display: 'flex', gap: 0 }}>
        {/* Left panel */}
        <div style={{ width: panelWidth, flexShrink: 0, minWidth: 220, maxWidth: 640 }}>
          <Text strong style={{ fontSize: 13 }}>Runs</Text>
          {okRuns.map(run => (
            <div key={run.run_id}>
              <Checkbox
                checked={selectedRuns.has(run.run_id)}
                onChange={e => setSelectedRuns(prev => {
                  const s = new Set(prev)
                  e.target.checked ? s.add(run.run_id) : s.delete(run.run_id)
                  return s
                })}
              >
                <Text style={{ fontSize: 12 }}>{run.label}</Text>
              </Checkbox>
            </div>
          ))}

          <Divider style={{ margin: '8px 0' }} />
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text strong style={{ fontSize: 13 }}>Signals</Text>
            <Space size={4}>
              {nPlots > 1 || liveAxes.size > 1 ? (
                <Button size="small" onClick={() => setPlacements({})} title="Draw every curve on a single plot, left axis">
                  Merge plots
                </Button>
              ) : null}
              {selectedCols.size > 0 && (
                <Button size="small" onClick={() => setSelectedCols(new Set())}>Clear</Button>
              )}
            </Space>
          </Space>

          <Collapse
            size="small"
            style={{ marginTop: 4 }}
            // Trim the default panel padding so long variable names get the width.
            styles={{ body: { padding: '6px 8px' } }}
            activeKey={openModels}
            onChange={keys => setOpenModels(keys as string[])}
            items={Object.entries(byModel).map(([model, cols]) => ({
              key: model,
              label: <Text strong style={{ fontSize: 14 }}>{model}</Text>,
              // The name gets a line to itself — variable names are long, and
              // squeezing style pickers next to them shreds them over 3 lines.
              // The pickers only appear once the signal is actually plotted.
              children: cols.map(col => (
                <div key={col} style={{ marginBottom: selectedCols.has(col) ? 6 : 2 }}>
                  <Checkbox
                    checked={selectedCols.has(col)}
                    style={{ width: '100%' }}
                    onChange={e => setSelectedCols(prev => {
                      const s = new Set(prev); e.target.checked ? s.add(col) : s.delete(col); return s
                    })}
                  >
                    <Text style={{ fontSize: 13, wordBreak: 'break-word' }} title={label(col, curvesInfo)}>
                      {curvesInfo[col]?.[1] ?? col}
                    </Text>
                  </Checkbox>
                  {selectedCols.has(col) && (
                    <Space size={4} style={{ marginLeft: 24, marginTop: 2 }}>
                      <Select
                        size="small"
                        value={dashStyles[col] ?? 'solid'}
                        options={DASH_OPTS.map(d => ({ label: d, value: d }))}
                        onChange={v => setDashStyles(prev => ({ ...prev, [col]: v }))}
                        style={{ width: 84 }}
                      />
                      {placementSelect(col)}
                    </Space>
                  )}
                </div>
              )),
            }))}
          />

          {/* Derived curves */}
          {derivedCurves.length > 0 && (
            <>
              <Divider style={{ margin: '8px 0' }} />
              <Text strong style={{ fontSize: 13 }}>Derived curves</Text>
              {derivedCurves.map((dc, i) => (
                <div key={i} style={{ marginBottom: selectedDerived.has(i) ? 6 : 2 }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4 }}>
                    <Checkbox
                      checked={selectedDerived.has(i)}
                      style={{ minWidth: 0, flex: 1 }}
                      onChange={e => setSelectedDerived(prev => {
                        const s = new Set(prev); e.target.checked ? s.add(i) : s.delete(i); return s
                      })}
                    >
                      <Text style={{ fontSize: 12, wordBreak: 'break-word' }} title={dc.name}>{dc.name}</Text>
                    </Checkbox>
                    <Button
                      size="small" type="text" danger icon={<DeleteOutlined />}
                      style={{ flexShrink: 0 }}
                      onClick={() => {
                        setDerivedCurves(prev => prev.filter((_, j) => j !== i))
                        setSelectedDerived(prev => { const s = new Set(prev); s.delete(i); return s })
                      }}
                    />
                  </div>
                  {selectedDerived.has(i) && (
                    <Space size={4} style={{ marginLeft: 24, marginTop: 2 }}>
                      <Select
                        size="small"
                        value={dashStyles[`dc_${i}`] ?? 'solid'}
                        options={DASH_OPTS.map(d => ({ label: d, value: d }))}
                        onChange={v => setDashStyles(prev => ({ ...prev, [`dc_${i}`]: v }))}
                        style={{ width: 84 }}
                      />
                      {placementSelect(`dc_${i}`)}
                    </Space>
                  )}
                </div>
              ))}
            </>
          )}

          {/* Add derived curve */}
          <Collapse size="small" style={{ marginTop: 8 }} items={[{
            key: 'dc',
            label: <Text style={{ fontSize: 12 }}>Add derived curve</Text>,
            children: (
              <Flex vertical gap={6} style={{ width: '100%' }}>
                <Select
                  size="small" style={{ width: '100%' }}
                  value={dcOp}
                  options={[
                    { label: 'Sum', value: 'sum' },
                    { label: 'Difference', value: 'diff' },
                    { label: 'Scale', value: 'scale' },
                  ]}
                  onChange={v => { setDcOp(v); setDcPicks([]); setDcBase('') }}
                />
                {dcOp === 'scale' ? (
                  <>
                    <Select
                      size="small" style={{ width: '100%' }} placeholder="Curve"
                      value={dcBase || undefined}
                      options={allCols.map(c => ({ label: label(c, curvesInfo), value: c }))}
                      onChange={setDcBase}
                      showSearch={{ optionFilterProp: 'label' }}
                    />
                    <Input size="small" style={{ width: '100%' }} value={dcFactor}
                      onChange={e => setDcFactor(e.target.value)} placeholder="Factor" />
                  </>
                ) : (
                  <Select
                    mode="multiple" size="small" style={{ width: '100%' }} placeholder="Curves"
                    value={dcPicks}
                    options={allCols.map(c => ({ label: label(c, curvesInfo), value: c }))}
                    onChange={setDcPicks}
                    showSearch={{ optionFilterProp: 'label' }}
                  />
                )}
                <Input size="small" placeholder="Name" value={dcName}
                  onChange={e => setDcName(e.target.value)} />
                <Button size="small" block onClick={addDerived}
                  disabled={!dcName.trim() || (dcOp === 'scale' ? !dcBase : dcPicks.length === 0)}>
                  Add
                </Button>
              </Flex>
            ),
          }]} />
        </div>

        {/* Resize handle */}
        <div
          onMouseDown={onDragStart}
          style={{
            width: 5, flexShrink: 0, cursor: 'col-resize',
            background: 'transparent', margin: '0 4px',
            borderLeft: '2px solid #d9d9d9',
          }}
        />

        {/* Chart */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {traces.length > 0 ? (
            <PlotlyChart traces={traces} axes={axes} height={chartHeight} />
          ) : (
            <Alert type="info" description="Select at least one run and one signal on the left to display the chart." />
          )}
        </div>
      </div>
    </>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RunSimulation() {
  const [jobsFiles, setJobsFiles] = useState<string[]>([])
  const [selectedJobs, setSelectedJobs] = useState<string | null>(null)
  const [exe, setExe] = useState<string | null>(null)
  const [config, setConfig] = useState<{ start_time: number; stop_time: number; output_dir: string; iidm_file?: string | null } | null>(null)
  const [iidmFiles, setIidmFiles] = useState<string[]>([])
  const [iidmOverride, setIidmOverride] = useState<string | null>(null)
  const [baseDir, setBaseDir] = useState<string>('outputs')
  const [startTime, setStartTime] = useState<string>('0')
  const [stopTime, setStopTime] = useState<string>('30')
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [running, setRunning] = useState(false)
  const [streamingRunId, setStreamingRunId] = useState<number | null>(null)
  const [outputLines, setOutputLines] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const outputRef = useRef<HTMLPreElement>(null)
  const esRef = useRef<EventSource | null>(null)

  // Load session state
  useEffect(() => {
    client.get<{ exe: string }>('/dynawo/executable')
      .then(r => setExe(r.data.exe || null))
      .catch(() => {})
    client.get<{ name: string; ftype: string }[]>('/files/')
      .then(r => {
        const jobs = r.data.filter(f => f.ftype === 'jobs').map(f => f.name)
        setJobsFiles(jobs)
        if (jobs.length === 1) setSelectedJobs(jobs[0])
        setIidmFiles(r.data.filter(f => f.ftype === 'iidm').map(f => f.name))
      })
      .catch(() => {})
    client.get<RunSummary[]>('/simulation/runs')
      .then(r => setRuns(r.data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!selectedJobs) return
    let cancelled = false
    setIidmOverride(null)
    client.get<{ start_time: number; stop_time: number; output_dir: string; iidm_file?: string | null }>(`/simulation/config/${encodeURIComponent(selectedJobs)}`)
      .then(r => {
        if (cancelled) return
        setConfig(r.data)
        setStartTime(String(r.data.start_time))
        setStopTime(String(r.data.stop_time))
        // Strip _runN suffix from output_dir to get base
        setBaseDir(r.data.output_dir.replace(/_run\d+$/, '') || 'outputs')
      })
      .catch(() => {})
    // Refresh IIDM list each time a jobs file is selected so LF results are visible
    client.get<{ name: string; ftype: string }[]>('/files/')
      .then(r => { if (!cancelled) setIidmFiles(r.data.filter(f => f.ftype === 'iidm').map(f => f.name)) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [selectedJobs])

  const jobRuns = runs.filter(r => r.jobs_file === selectedJobs)
  const nextRunId = Math.max(0, ...runs.map(r => r.run_id)) + 1
  const nextOutDir = `${baseDir.trim() || 'outputs'}_run${nextRunId}`

  const autoScroll = () => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight
  }

  const openSSE = useCallback((runId: number) => {
    esRef.current?.close()
    setStreamingRunId(runId)
    setOutputLines([])
    setRunning(true)

    const es = new EventSource(`/api/simulation/${runId}/output`, { withCredentials: true })
    esRef.current = es

    es.onmessage = e => {
      const msg = JSON.parse(e.data)
      if (msg.line) {
        setOutputLines(prev => { const next = [...prev, msg.line]; return next })
        setTimeout(autoScroll, 30)
      }
      if (msg.done) {
        es.close()
        esRef.current = null
        setRunning(false)
        setStreamingRunId(null)
        client.get<RunSummary[]>('/simulation/runs').then(r => setRuns(r.data)).catch(() => {})
        if (selectedJobs) {
          client.get<{ start_time: number; stop_time: number; output_dir: string; iidm_file?: string | null }>(`/simulation/config/${encodeURIComponent(selectedJobs)}`)
            .then(r => { setConfig(r.data); setIidmOverride(null) })
            .catch(() => {})
        }
      }
    }
    es.onerror = () => { es.close(); esRef.current = null; setRunning(false); setStreamingRunId(null) }
  }, [selectedJobs])

  const handleRun = async () => {
    if (!selectedJobs) return
    setError(null)
    setOutputLines([])
    try {
      const res = await client.post<{ run_id: number; output_dir: string }>('/simulation/run', {
        jobs_file: selectedJobs,
        start_time: parseFloat(startTime),
        stop_time: parseFloat(stopTime),
        base_output_dir: baseDir.trim() || 'outputs',
        ...(iidmOverride ? { iidm_override: iidmOverride } : {}),
      })
      // Optimistically add a pending run
      const newRun: RunSummary = {
        run_id: res.data.run_id,
        label: `Run ${res.data.run_id}`,
        jobs_file: selectedJobs,
        output_dir: res.data.output_dir,
        returncode: null,
        start_time: parseFloat(startTime),
        stop_time: parseFloat(stopTime),
        started_at: Date.now() / 1000,
        finished_at: null,
        has_output: false,
        has_final_state_iidm: false,
        has_timeline: false,
        has_constraints: false,
        has_log: false,
        has_lost_equipments: false,
      }
      setRuns(prev => [newRun, ...prev])
      openSSE(res.data.run_id)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Failed to start simulation')
    }
  }

  const handleCancel = () => {
    esRef.current?.close()
    esRef.current = null
    client.post('/simulation/cancel').catch(() => {})
    setRunning(false)
    setStreamingRunId(null)
  }

  const handleDelete = async (runId: number) => {
    await client.delete(`/simulation/${runId}`)
    setRuns(prev => prev.filter(r => r.run_id !== runId))
  }

  const handleRename = async (runId: number, newLabel: string) => {
    await client.put(`/simulation/${runId}/label`, { label: newLabel })
    setRuns(prev => prev.map(r => r.run_id === runId ? { ...r, label: newLabel } : r))
  }

  const handleRestore = async () => {
    if (!selectedJobs) return
    await client.post(`/simulation/restore/${encodeURIComponent(selectedJobs)}`)
    if (selectedJobs) {
      const r = await client.get<{ start_time: number; stop_time: number; output_dir: string; iidm_file?: string | null }>(`/simulation/config/${encodeURIComponent(selectedJobs)}`)
      setConfig(r.data)
      setIidmOverride(null)
      setStartTime(String(r.data.start_time))
      setStopTime(String(r.data.stop_time))
      setBaseDir(r.data.output_dir.replace(/_run\d+$/, '') || 'outputs')
    }
  }

  const paramsValid = parseFloat(stopTime) > parseFloat(startTime)

  return (
    <div style={{ maxWidth: 1100 }}>
      <Title level={3}>Run Simulation</Title>

      {/* Status bar */}
      {!exe && <Alert type="warning" description="No Dynawo executable configured. Go to Dynawo Version first." style={{ marginBottom: 12 }} />}
      {exe && <Alert type="info" description={<Text>Executable: <Text code>{exe}</Text></Text>} style={{ marginBottom: 12 }} />}

      {jobsFiles.length === 0 && (
        <Alert type="warning" description="No .jobs file found. Go to Upload Files and upload your input files." style={{ marginBottom: 12 }} />
      )}

      {jobsFiles.length > 1 && (
        <Select
          options={jobsFiles.map(f => ({ label: f, value: f }))}
          value={selectedJobs}
          onChange={setSelectedJobs}
          style={{ width: 320, marginBottom: 12 }}
          placeholder="Select jobs file…"
        />
      )}
      {jobsFiles.length === 1 && selectedJobs && (
        <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>Jobs file: <Text code>{selectedJobs}</Text></Text>
      )}

      {/* Simulation parameters */}
      {selectedJobs && config && (
        <Card title="Simulation parameters" style={{ marginBottom: 16 }}
          extra={
            <Button size="small" icon={<RedoOutlined />} onClick={handleRestore}>
              Restore original
            </Button>
          }
        >
          <Space wrap>
            <Flex vertical gap={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>Start time (s)</Text>
              <Input value={startTime} onChange={e => setStartTime(e.target.value)} style={{ width: 140 }} />
            </Flex>
            <Flex vertical gap={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>Stop time (s)</Text>
              <Input value={stopTime} onChange={e => setStopTime(e.target.value)} style={{ width: 140 }} />
            </Flex>
            <Flex vertical gap={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>Base output directory</Text>
              <Input value={baseDir} onChange={e => setBaseDir(e.target.value)} style={{ width: 200 }} />
            </Flex>
          </Space>
          {config?.iidm_file && (
            <Flex vertical gap={2} style={{ marginTop: 12 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>Network IIDM</Text>
              <Select
                style={{ width: 400 }}
                value={iidmOverride ?? '__original__'}
                onChange={v => setIidmOverride(v === '__original__' ? null : v)}
                options={[
                  {
                    label: config.iidm_file,
                    value: '__original__',
                  },
                  ...iidmFiles
                    .filter(f => f !== config.iidm_file)
                    .map(f => ({ label: f, value: f })),
                ]}
              />
            </Flex>
          )}
          {!paramsValid && <Alert type="error" description="Stop time must be greater than start time." style={{ marginTop: 8 }} />}
          {paramsValid && (
            <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
              Next run will save to: <Text code>{nextOutDir}</Text>
            </Text>
          )}
        </Card>
      )}

      {/* Run / Cancel button */}
      {selectedJobs && (
        <Space style={{ marginBottom: 16 }}>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            disabled={!exe || !paramsValid || running}
            loading={running}
            onClick={handleRun}
          >
            Run simulation
          </Button>
          {running && (
            <Button icon={<StopOutlined />} onClick={handleCancel} danger>
              Cancel
            </Button>
          )}
        </Space>
      )}

      {error && <Alert type="error" description={error} style={{ marginBottom: 12 }} />}

      {/* Live output */}
      {(running || outputLines.length > 0) && (
        <Card title={running ? 'Simulation output (live)' : 'Simulation output'} style={{ marginBottom: 16 }}>
          <pre
            ref={outputRef}
            style={{
              fontSize: 11, maxHeight: 300, overflow: 'auto',
              background: '#1a1a1a', color: '#d4d4d4',
              padding: 8, borderRadius: 4, margin: 0,
            }}
          >
            {outputLines.join('') || '(waiting for output…)'}
          </pre>
        </Card>
      )}

      {/* Run history */}
      {jobRuns.length > 0 && (
        <>
          <Divider>Run history</Divider>
          {jobRuns.map(run => (
            <RunItem
              key={run.run_id}
              run={run}
              onDelete={handleDelete}
              onRename={handleRename}
              onStream={openSSE}
              streaming={streamingRunId === run.run_id}
            />
          ))}
        </>
      )}

      {/* Curves */}
      {selectedJobs && jobRuns.some(r => r.returncode !== null) && (
        <CurvesBuilder runs={jobRuns} jobsFile={selectedJobs} />
      )}
    </div>
  )
}
