//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Alert, Badge, Button, Checkbox, Collapse, Flex, Input, Modal, Popconfirm, Select, Space, Tag, Tooltip, Typography,
} from 'antd'
import { CaretRightOutlined, CheckSquareOutlined, CopyOutlined, DeleteOutlined, PlusOutlined, RedoOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import { List, type RowComponentProps } from 'react-window'
import client from '../api/client'

const { Title, Text } = Typography

interface CurveEntry  { variable: string; active: boolean; extra?: boolean }
interface CurveGroup  { model: string; lib: string; curves: CurveEntry[] }
interface ListResp    { crv_file: string; jobs_file: string | null; modified: boolean; groups: CurveGroup[]; dyd_models: Record<string, string> }
interface CurveChange { model: string; variable: string; action: 'added' | 'removed' }
interface LogEntry    { id: string; timestamp: string; crv_file: string; changes: CurveChange[] }

// One editable target per jobs file: the .crv that job's <curves inputFile="…">
// points at. Two jobs may share one .crv (shared_with says so), and a job may
// link none at all (crv_file null) — the page then offers to create it.
interface Target      { jobs_file: string; crv_file: string | null; crv_ref: string | null; curve_count: number; shared_with: string[] }
interface TargetsResp { targets: Target[]; orphan_crv: string | null }
interface ApplyResult { jobs_file: string | null; crv_file: string | null; added: number; removed: number; skipped: string[]; note?: string | null }
interface ApplyResp   { changed: number; results: ApplyResult[] }

// Every curves endpoint acts on the .crv of one job; omitting the parameter
// falls back to the session's single .crv (a session with one job, as before).
function scoped(jobsFile: string | null) {
  return jobsFile ? { params: { jobs_file: jobsFile } } : undefined
}

// How many curves the job's .crv currently defines — spelled out, since the
// number sits next to a filename where a bare "(12)" reads like a version.
function countLabel(n: number): string {
  if (n === 0) return 'no curves yet'
  return `${n} curve${n === 1 ? '' : 's'}`
}

// The apply response carries one row per .crv file written: the edited job first,
// then every other job the same additions and removals were replayed on.
function describeApply(res: ApplyResp): string {
  const [primary, ...others] = res.results
  const parts = [`${res.changed} curve(s) updated in ${primary?.crv_file ?? 'the curves file'}.`]
  for (const r of others) {
    const where = r.jobs_file ?? r.crv_file ?? 'another job'
    if (!r.crv_file) parts.push(`${where}: no curves file linked — skipped.`)
    else if (r.added || r.removed) parts.push(`${r.crv_file}: +${r.added} / −${r.removed}.`)
    else if (r.note) parts.push(`${r.crv_file}: ${r.note}.`)
    if (r.skipped?.length) {
      const shown = r.skipped.slice(0, 3).join(', ')
      parts.push(`${r.skipped.length} curve(s) not applicable to ${where} (model absent from its .dyd): ${shown}${r.skipped.length > 3 ? '…' : ''}.`)
    }
  }
  return parts.join(' ')
}

interface CatalogueEntry { lib: string; variables: string[]; parameters: string[] }
interface CatalogueResp  { available: boolean; catalogue: Record<string, CatalogueEntry> }
interface InitInfo       { suggested_filename: string; has_jobs: boolean }

function selKey(model: string, variable: string) {
  return `${model}::${variable}`
}

// Module-level cache: survives the component unmount/remount that happens every
// time the user navigates away from and back to this page (react-router does not
// keep inactive routes mounted), so switching pages doesn't force a full re-fetch
// and doesn't discard in-progress (unsaved) checkbox edits.
interface EditCurvesCache {
  loaded: boolean
  // The jobs file the cached state was loaded for — hydrating it under another
  // scope would show one job's curves as if they were another's.
  scope: string | null
  targets: Target[]
  groups: CurveGroup[]
  serverSel: Record<string, boolean>
  crvFile: string | null
  modified: boolean
  selection: Record<string, boolean>
  changelog: LogEntry[]
  noCrv: boolean
  catalogue: Record<string, CatalogueEntry>
  catalogueAvailable: boolean
  dydModels: Record<string, string>
}

const editCurvesCache: EditCurvesCache = {
  loaded: false,
  scope: null,
  targets: [],
  groups: [],
  serverSel: {},
  crvFile: null,
  modified: false,
  selection: {},
  changelog: [],
  noCrv: false,
  catalogue: {},
  catalogueAvailable: false,
  dydModels: {},
}

// ── Virtualized model list ──────────────────────────────────────────────────
// A big network's DYD can have one blackBoxModel per static id (tens of thousands).
// Rendering every model as an always-mounted Collapse.Panel makes the page slow to
// mount. react-window only mounts rows currently scrolled into view (~20 at a time)
// plus the single expanded row, so the list stays fast regardless of network size
// while still letting the user browse every model, not just ones matching a search.
const HEADER_HEIGHT = 44
const SELECT_ALL_HEIGHT = 34
const VARIABLE_ROW_HEIGHT = 30
const ADD_ROW_HEIGHT = 74
const CONTENT_PADDING = 12

// A .crv <curve> variable= is resolved by Dynawo against the model's variables first
// and against its parameters as a fallback, so a descriptor's parameters are valid curve
// targets too — they just yield a constant curve. Both are offered, kept apart so the user
// can tell a state variable from a parameter.
interface AvailableToAdd { variables: string[]; parameters: string[] }
const NOTHING_TO_ADD: AvailableToAdd = { variables: [], parameters: [] }

function getAvailableToAdd(
  g: CurveGroup,
  catalogue: Record<string, CatalogueEntry>,
  catalogueAvailable: boolean,
): AvailableToAdd {
  const entry = catalogueAvailable ? catalogue[g.model] : undefined
  if (!entry) return NOTHING_TO_ADD
  const existing = new Set(g.curves.map(c => c.variable))
  return {
    variables:  (entry.variables  ?? []).filter(v => !existing.has(v)),
    parameters: (entry.parameters ?? []).filter(v => !existing.has(v)),
  }
}

function estimateRowHeight(g: CurveGroup, expanded: boolean, hasAddRow: boolean): number {
  if (!expanded) return HEADER_HEIGHT
  return HEADER_HEIGHT + SELECT_ALL_HEIGHT + g.curves.length * VARIABLE_ROW_HEIGHT +
    (hasAddRow ? ADD_ROW_HEIGHT : 0) + CONTENT_PADDING
}

// Same-lib models always share the same variable set (it comes from the lib's
// descriptor, not the model instance), so propagating a variable to every sibling
// model is always valid — no catalogue lookup needed for the targets.
function missingSiblingCount(siblingGroups: CurveGroup[], variable: string): number {
  return siblingGroups.filter(o => !o.curves.some(c => c.variable === variable)).length
}

interface ModelRowProps {
  filteredGroups: CurveGroup[]
  allGroups: CurveGroup[]
  activeKey: string | null
  setActiveKey: (k: string | null) => void
  selection: Record<string, boolean>
  setSelection: React.Dispatch<React.SetStateAction<Record<string, boolean>>>
  serverSel: Record<string, boolean>
  catalogue: Record<string, CatalogueEntry>
  catalogueAvailable: boolean
  pickerVals: Record<string, string | undefined>
  handleAddVariable: (model: string, variable: string) => void
  handleSelectModelFromCatalogue: (model: string, kind: 'variables' | 'parameters') => void
  handlePropagateVariable: (model: string, variable: string) => void
}

function ModelRow({
  index, style, ariaAttributes,
  filteredGroups, allGroups, activeKey, setActiveKey, selection, setSelection, serverSel,
  catalogue, catalogueAvailable, pickerVals,
  handleAddVariable, handleSelectModelFromCatalogue, handlePropagateVariable,
}: RowComponentProps<ModelRowProps>) {
  const g = filteredGroups[index]
  if (!g) return null
  const expanded = g.model === activeKey
  const activeCount = g.curves.filter(c => selection[selKey(g.model, c.variable)]).length
  const allChecked = g.curves.length > 0 && activeCount === g.curves.length
  const noneChecked = activeCount === 0
  const availableToAdd = getAvailableToAdd(g, catalogue, catalogueAvailable)
  const hasAddRow = availableToAdd.variables.length + availableToAdd.parameters.length > 0
  // Only the expanded row needs this, and only one row is ever expanded.
  const paramNames = expanded ? new Set(catalogue[g.model]?.parameters ?? []) : new Set<string>()
  const siblingGroups = expanded && g.lib ? allGroups.filter(o => o.lib === g.lib && o.model !== g.model) : []

  const toggleAll = () => {
    setSelection(prev => {
      const next = { ...prev }
      for (const c of g.curves) next[selKey(g.model, c.variable)] = !allChecked
      return next
    })
  }

  return (
    <div style={style} {...ariaAttributes}>
      <div
        onClick={() => setActiveKey(expanded ? null : g.model)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8, height: HEADER_HEIGHT,
          cursor: 'pointer', borderBottom: '1px solid rgba(0,0,0,0.06)', padding: '0 8px',
        }}
      >
        <CaretRightOutlined
          rotate={expanded ? 90 : 0}
          style={{ fontSize: 11, transition: 'transform .15s', color: 'rgba(0,0,0,0.45)' }}
        />
        <Text strong>{g.model}</Text>
        {g.lib && <Tag color="blue">{g.lib}</Tag>}
        <Badge
          count={`${activeCount} / ${g.curves.length}`}
          style={{
            backgroundColor: activeCount > 0 ? '#52c41a' : '#d9d9d9',
            color: activeCount > 0 ? '#fff' : '#888',
            fontWeight: 'normal',
          }}
        />
      </div>
      {expanded && (
        <Flex vertical gap={6} style={{ padding: '6px 8px 6px 28px' }}>
          <div style={{ paddingBottom: 6, borderBottom: '1px solid rgba(0,0,0,0.06)' }}>
            <Checkbox
              indeterminate={!allChecked && !noneChecked}
              checked={allChecked}
              onChange={toggleAll}
            >
              <Text type="secondary" style={{ fontSize: 12 }}>Select all</Text>
            </Checkbox>
          </div>
          {g.curves.map(c => {
            const key         = selKey(g.model, c.variable)
            const checked     = selection[key] ?? false
            const wasOnServer = serverSel[key] ?? false
            const isDirty     = checked !== wasOnServer
            const propagateCount = missingSiblingCount(siblingGroups, c.variable)
            return (
              <div key={c.variable} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <Checkbox
                  checked={checked}
                  onChange={e => setSelection(prev => ({ ...prev, [key]: e.target.checked }))}
                />
                <Text code style={{ color: isDirty ? '#faad14' : undefined }}>
                  {c.variable}
                </Text>
                {paramNames.has(c.variable) && (
                  <Tooltip title="Model parameter — constant over the simulation">
                    <Tag color="purple" style={{ fontSize: 11 }}>param</Tag>
                  </Tooltip>
                )}
                {c.extra && !isDirty && (
                  <Tag color="cyan" style={{ fontSize: 11 }}>new</Tag>
                )}
                {isDirty && (
                  <Tag color={checked ? 'green' : 'red'} style={{ fontSize: 11 }}>
                    {checked ? 'will add' : 'will remove'}
                  </Tag>
                )}
                {propagateCount > 0 && (
                  <Popconfirm
                    title={`Add "${c.variable}" to ${propagateCount} other model${propagateCount > 1 ? 's' : ''} using library "${g.lib}"?`}
                    onConfirm={() => handlePropagateVariable(g.model, c.variable)}
                  >
                    <Tooltip title={`Add to ${propagateCount} other ${g.lib} model${propagateCount > 1 ? 's' : ''}`}>
                      <Button type="text" size="small" icon={<CopyOutlined />} />
                    </Tooltip>
                  </Popconfirm>
                )}
              </div>
            )
          })}
          {hasAddRow && (
            <Flex vertical gap={6} style={{ paddingTop: 6, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
              <Select
                showSearch={{ optionFilterProp: 'label' }}
                placeholder={<><PlusOutlined style={{ marginRight: 4 }} />Add variable or parameter from catalogue…</>}
                size="small"
                value={pickerVals[g.model]}
                onChange={(v: string) => handleAddVariable(g.model, v)}
                options={[
                  ...(availableToAdd.variables.length
                    ? [{ label: 'Variables', options: availableToAdd.variables.map(v => ({ label: v, value: v })) }]
                    : []),
                  ...(availableToAdd.parameters.length
                    ? [{ label: 'Parameters', options: availableToAdd.parameters.map(v => ({ label: v, value: v })) }]
                    : []),
                ]}
              />
              <Flex gap={8}>
                {availableToAdd.variables.length > 0 && (
                  <Button
                    size="small"
                    icon={<CheckSquareOutlined />}
                    onClick={() => handleSelectModelFromCatalogue(g.model, 'variables')}
                  >
                    Add all variables ({availableToAdd.variables.length})
                  </Button>
                )}
                {availableToAdd.parameters.length > 0 && (
                  <Button
                    size="small"
                    icon={<CheckSquareOutlined />}
                    onClick={() => handleSelectModelFromCatalogue(g.model, 'parameters')}
                  >
                    Add all parameters ({availableToAdd.parameters.length})
                  </Button>
                )}
              </Flex>
            </Flex>
          )}
        </Flex>
      )}
    </div>
  )
}

export default function EditCurves() {
  const [groups, setGroups]           = useState<CurveGroup[]>(() => editCurvesCache.groups)
  const [serverSel, setServerSel]     = useState<Record<string, boolean>>(() => editCurvesCache.serverSel)
  const [crvFile, setCrvFile]         = useState<string | null>(() => editCurvesCache.crvFile)
  const [modified, setModified]       = useState(() => editCurvesCache.modified)
  const [selection, setSelection]     = useState<Record<string, boolean>>(() => editCurvesCache.selection)
  const [changelog, setChangelog]     = useState<LogEntry[]>(() => editCurvesCache.changelog)
  const [saving, setSaving]           = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [success, setSuccess]         = useState<string | null>(null)
  const [noCrv, setNoCrv]            = useState(() => editCurvesCache.noCrv)
  const [search, setSearch]           = useState('')
  const [catalogue, setCatalogue]     = useState<Record<string, CatalogueEntry>>(() => editCurvesCache.catalogue)
  const [catalogueAvailable, setCatalogueAvailable] = useState(() => editCurvesCache.catalogueAvailable)
  const [dydModels, setDydModels]     = useState<Record<string, string>>(() => editCurvesCache.dydModels)
  const [pickerVals, setPickerVals]   = useState<Record<string, string | undefined>>({})
  const [initInfo, setInitInfo]       = useState<InitInfo | null>(null)
  const [newCrvName, setNewCrvName]   = useState('')
  const [creating, setCreating]       = useState(false)
  const [manualModel, setManualModel] = useState('')
  const [manualVar, setManualVar]     = useState('')
  const [targets, setTargets]         = useState<Target[]>(() => editCurvesCache.targets)
  const [scope, setScope]             = useState<string | null>(() => editCurvesCache.scope)
  const [targetsLoaded, setTargetsLoaded] = useState(false)
  const [applyAll, setApplyAll]       = useState(false)
  const [linkAllJobs, setLinkAllJobs] = useState(false)
  const [modal, modalCtx]             = Modal.useModal()

  const fetchTargets = async (): Promise<Target[]> => {
    try {
      const res = await client.get<TargetsResp>('/curves/targets')
      setTargets(res.data.targets)
      return res.data.targets
    } catch {
      return []
    }
  }

  const fetchList = async (jobsFile: string | null) => {
    try {
      const res = await client.get<ListResp>('/curves/list', scoped(jobsFile))
      setCrvFile(res.data.crv_file)
      setModified(res.data.modified)
      setGroups(res.data.groups)
      setDydModels(res.data.dyd_models)
      setNoCrv(false)
      const init: Record<string, boolean> = {}
      for (const g of res.data.groups)
        for (const c of g.curves)
          init[selKey(g.model, c.variable)] = c.active
      setServerSel(init)
      setSelection(init)
    } catch (e: any) {
      if (e.response?.status === 404) setNoCrv(true)
    }
  }

  const fetchChangelog = async (jobsFile: string | null) => {
    try {
      const res = await client.get<LogEntry[]>('/curves/changelog', scoped(jobsFile))
      setChangelog(res.data)
    } catch {}
  }

  const fetchCatalogue = async (jobsFile: string | null) => {
    try {
      const res = await client.get<CatalogueResp>('/curves/catalogue', scoped(jobsFile))
      // Only upgrade available; never downgrade — a transient false must not clear catalogue
      if (res.data.available) {
        setCatalogueAvailable(true)
        setCatalogue(res.data.catalogue)
      }
    } catch {}
  }

  const fetchInitInfo = async (jobsFile: string | null) => {
    try {
      const res = await client.get<InitInfo>('/curves/init-info', scoped(jobsFile))
      setInitInfo(res.data)
      setNewCrvName(res.data.suggested_filename)
    } catch {
      setInitInfo({ suggested_filename: 'curves.crv', has_jobs: false })
      setNewCrvName('curves.crv')
    }
  }

  // Keep the module-level cache in sync so a remount (page switch) can hydrate from it.
  useEffect(() => { editCurvesCache.groups = groups }, [groups])
  useEffect(() => { editCurvesCache.serverSel = serverSel }, [serverSel])
  useEffect(() => { editCurvesCache.crvFile = crvFile }, [crvFile])
  useEffect(() => { editCurvesCache.modified = modified }, [modified])
  useEffect(() => { editCurvesCache.selection = selection }, [selection])
  useEffect(() => { editCurvesCache.changelog = changelog }, [changelog])
  useEffect(() => { editCurvesCache.noCrv = noCrv }, [noCrv])
  useEffect(() => { editCurvesCache.catalogue = catalogue }, [catalogue])
  useEffect(() => { editCurvesCache.catalogueAvailable = catalogueAvailable }, [catalogueAvailable])
  useEffect(() => { editCurvesCache.dydModels = dydModels }, [dydModels])
  useEffect(() => { editCurvesCache.targets = targets }, [targets])

  // Which jobs the session holds, and which .crv each one drives. Cheap enough to
  // re-read on every visit — a job may have gained or lost its <curves> element
  // (Upload, autoload, or a create from this very page) since the last one.
  useEffect(() => {
    fetchTargets()
      .then(list => setScope(prev =>
        prev && list.some(t => t.jobs_file === prev) ? prev : (list[0]?.jobs_file ?? null)))
      .finally(() => setTargetsLoaded(true))
  }, [])

  // Load the scoped .crv. Skip the refetch on remount only when a .crv was actually loaded
  // *for this same job* — that's the case with in-progress edits worth protecting. When the
  // last known state was "no .crv" there's nothing to lose, and re-checking lets the page pick
  // up a .crv uploaded since the last visit (e.g. via the Upload page) instead of getting stuck
  // showing a stale "no .crv" screen forever. Any later scope change always re-fetches.
  const hydrated = useRef(false)
  useEffect(() => {
    if (!targetsLoaded) return
    if (!hydrated.current && editCurvesCache.loaded && !editCurvesCache.noCrv && editCurvesCache.scope === scope) {
      hydrated.current = true
      return
    }
    hydrated.current = true
    editCurvesCache.loaded = true
    editCurvesCache.scope = scope
    fetchList(scope); fetchChangelog(scope); fetchCatalogue(scope)
  }, [scope, targetsLoaded])

  // Re-fetch catalogue whenever the editor becomes active (noCrv: true → false).
  // This is belt-and-suspenders: if the fetchCatalogue inside handleCreate returned
  // available:false (transient backend state), this effect fires after the transition
  // and gets a fresh result with the correct data.
  const prevNoCrvRef = useRef<boolean | null>(null)
  useEffect(() => {
    const prev = prevNoCrvRef.current
    prevNoCrvRef.current = noCrv
    if (prev === true && !noCrv) fetchCatalogue(scope)
  }, [noCrv, scope])

  useEffect(() => { if (noCrv) fetchInitInfo(scope) }, [noCrv, scope])

  // Augment server groups with every other DYD model that has no curves yet, so the user can
  // browse and add curves (or propagate one to sibling-lib models) across the whole network, not
  // just models that already have curves. Independent of catalogue availability — the dyd_models
  // map (static id → lib) comes straight from the DYD file. Safe to do unconditionally (even for
  // huge networks) because the list below is virtualized — see "Virtualized model list" above.
  const displayGroups = useMemo<CurveGroup[]>(() => {
    const inGroups = new Set(groups.map(g => g.model))
    const synthetic = Object.entries(dydModels)
      .filter(([id]) => !inGroups.has(id))
      .map(([id, lib]) => ({ model: id, lib, curves: [] as CurveEntry[] }))
    return [...groups, ...synthetic]
  }, [groups, dydModels])

  const [activeKey, setActiveKey] = useState<string | null>(null)

  const q = search.trim().toLowerCase()
  const filteredGroups = useMemo<CurveGroup[]>(() => {
    if (!q) return displayGroups
    return displayGroups.filter(g => g.model.toLowerCase().includes(q) || g.lib.toLowerCase().includes(q))
  }, [displayGroups, q])

  // dirty = selection differs from what the server has (serverSel never changes without a fetchList)
  const dirty = Object.keys(selection).some(k => selection[k] !== (serverSel[k] ?? false))

  const target = targets.find(t => t.jobs_file === scope) ?? null
  const sharedWith = target?.shared_with ?? []

  // Switching job reloads the editor from that job's .crv, so unapplied ticks are lost.
  const requestScope = (next: string) => {
    if (next === scope) return
    if (!dirty) { setScope(next); return }
    modal.confirm({
      title: 'Discard unsaved curve changes?',
      content: `Changes to ${crvFile ?? 'the current curves file'} have not been applied yet and will be lost.`,
      okText: 'Discard and switch',
      okButtonProps: { danger: true },
      cancelText: 'Stay',
      onOk: () => setScope(next),
    })
  }

  const jobSelector = targets.length > 1 ? (
    <Flex align="center" gap={8} wrap style={{ marginBottom: 12 }}>
      <Text strong>Job</Text>
      <Select
        value={scope ?? undefined}
        style={{ minWidth: 340 }}
        onChange={requestScope}
        options={targets.map(t => ({
          value: t.jobs_file,
          label: t.crv_file
            ? `${t.jobs_file} → ${t.crv_file} (${countLabel(t.curve_count)})`
            : `${t.jobs_file} → no curves file`,
        }))}
      />
      {sharedWith.length > 0 && (
        <Tooltip title={`${crvFile} is also the curves file of ${sharedWith.join(', ')} — editing it here changes those jobs too.`}>
          <Tag color="orange">shared with {sharedWith.length} other job{sharedWith.length > 1 ? 's' : ''}</Tag>
        </Tooltip>
      )}
    </Flex>
  ) : null

  const handleAddVariable = (model: string, variable: string) => {
    setGroups(prev => {
      if (prev.some(g => g.model === model)) {
        return prev.map(g =>
          g.model !== model ? g : {
            ...g,
            curves: [...g.curves, { variable, active: true, extra: true }],
          }
        )
      }
      // Synthetic group (model only in catalogue, not yet in groups) — promote it
      return [...prev, { model, lib: catalogue[model]?.lib ?? dydModels[model] ?? '', curves: [{ variable, active: true, extra: true }] }]
    })
    setSelection(prev => ({ ...prev, [selKey(model, variable)]: true }))
    setPickerVals(prev => ({ ...prev, [model]: undefined }))
  }

  const handleApply = async () => {
    setSaving(true); setError(null); setSuccess(null)
    try {
      const curves = Object.entries(selection)
        .filter(([, active]) => active)
        .map(([k]) => {
          const sep = k.indexOf('::')
          return { model: k.slice(0, sep), variable: k.slice(sep + 2) }
        })
      const res = await client.put<ApplyResp>('/curves/apply', {
        curves,
        jobs_file: scope,
        apply_to_all: applyAll,
      })
      await fetchList(scope)
      await fetchChangelog(scope)
      await fetchTargets()
      setSuccess(describeApply(res.data))
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Apply failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = async () => {
    try {
      await client.post('/curves/restore', null, scoped(scope))
      await fetchList(scope)
      await fetchChangelog(scope)
      await fetchTargets()
      setSuccess(`${crvFile ?? 'Curves file'} restored to original.`)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Restore failed')
    }
  }

  const handleCreate = async () => {
    const name = newCrvName.trim() || 'curves.crv'
    setCreating(true); setError(null)
    try {
      // Link the new file into the selected job only, unless the user asked for every job.
      await client.post('/curves/init', {
        crv_filename: name,
        jobs_file: linkAllJobs ? null : scope,
      })
      await fetchTargets()

      // Fetch list and catalogue in parallel, then apply ALL state updates in one
      // synchronous block so React batches them into a single render.  This is the
      // only reliable way to guarantee that displayGroups has catalogue data at the
      // exact moment noCrv flips to false.
      const [listRes, catRes] = await Promise.all([
        client.get<ListResp>('/curves/list', scoped(scope)),
        client.get<CatalogueResp>('/curves/catalogue', scoped(scope)).catch(() => null),
      ])

      // ── all synchronous from here → single React batch ──────────────────────
      if (catRes?.data.available) {
        setCatalogueAvailable(true)
        setCatalogue(catRes.data.catalogue)
      }
      const ld = listRes.data
      setCrvFile(ld.crv_file)
      setModified(ld.modified)
      setGroups(ld.groups)
      setDydModels(ld.dyd_models)
      const init: Record<string, boolean> = {}
      for (const g of ld.groups)
        for (const c of g.curves)
          init[selKey(g.model, c.variable)] = c.active
      setServerSel(init)
      setSelection(init)
      setNoCrv(false)
      // ────────────────────────────────────────────────────────────────────────

      await fetchChangelog(scope)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Failed to create curves file')
    } finally {
      setCreating(false)
    }
  }

  const handleSelectModelFromCatalogue = (model: string, kind: 'variables' | 'parameters') => {
    const entry = catalogue[model]
    if (!entry) return
    const names = entry[kind] ?? []
    if (names.length === 0) return
    setGroups(prev => {
      const idx = prev.findIndex(g => g.model === model)
      if (idx >= 0) {
        const existingVars = new Set(prev[idx].curves.map(c => c.variable))
        const toAdd = names.filter(v => !existingVars.has(v))
        if (toAdd.length === 0) return prev
        const next = [...prev]
        next[idx] = { ...next[idx], curves: [...next[idx].curves, ...toAdd.map(v => ({ variable: v, active: true, extra: true }))] }
        return next
      }
      return [...prev, { model, lib: entry.lib, curves: names.map(v => ({ variable: v, active: true, extra: true })) }]
    })
    setSelection(prev => {
      const next = { ...prev }
      for (const v of names) next[selKey(model, v)] = true
      return next
    })
  }

  // Adds `variable` to every other model sharing `model`'s lib that doesn't already have it.
  // Safe unconditionally: models sharing a lib always share the same variable set (it comes
  // from the lib's descriptor, not the model instance), so no catalogue lookup is needed here.
  const handlePropagateVariable = (model: string, variable: string) => {
    const lib = displayGroups.find(g => g.model === model)?.lib
    if (!lib) return
    const targetModels = displayGroups
      .filter(g => g.lib === lib && g.model !== model && !g.curves.some(c => c.variable === variable))
      .map(g => g.model)
    if (targetModels.length === 0) return

    setGroups(prev => {
      const next = [...prev]
      for (const t of targetModels) {
        const idx = next.findIndex(g => g.model === t)
        if (idx >= 0) {
          next[idx] = { ...next[idx], curves: [...next[idx].curves, { variable, active: true, extra: true }] }
        } else {
          next.push({ model: t, lib, curves: [{ variable, active: true, extra: true }] })
        }
      }
      return next
    })
    setSelection(prev => {
      const next = { ...prev }
      for (const t of targetModels) next[selKey(t, variable)] = true
      return next
    })
  }

  // Variables only, deliberately: parameters outnumber variables in most descriptors and
  // are constant over the run, so adding every one across the whole network is never what the
  // user means by "select all". Parameters are added per model (picker / "Add all parameters").
  const handleSelectAllCatalogue = () => {
    setGroups(prev => {
      const next = [...prev]
      for (const [modelId, entry] of Object.entries(catalogue)) {
        const idx = next.findIndex(g => g.model === modelId)
        if (idx >= 0) {
          const existingVars = new Set(next[idx].curves.map(c => c.variable))
          const toAdd = entry.variables.filter(v => !existingVars.has(v))
          if (toAdd.length > 0) {
            next[idx] = {
              ...next[idx],
              curves: [...next[idx].curves, ...toAdd.map(v => ({ variable: v, active: true, extra: true }))],
            }
          }
        } else {
          next.push({ model: modelId, lib: entry.lib, curves: entry.variables.map(v => ({ variable: v, active: true, extra: true })) })
        }
      }
      return next
    })
    setSelection(prev => {
      const next = { ...prev }
      for (const [modelId, entry] of Object.entries(catalogue))
        for (const v of entry.variables)
          next[selKey(modelId, v)] = true
      return next
    })
  }

  const rowProps: ModelRowProps = {
    filteredGroups, allGroups: displayGroups, activeKey, setActiveKey, selection, setSelection, serverSel,
    catalogue, catalogueAvailable, pickerVals,
    handleAddVariable, handleSelectModelFromCatalogue, handlePropagateVariable,
  }

  const rowHeight = useCallback((index: number, cellProps: ModelRowProps) => {
    const g = cellProps.filteredGroups[index]
    if (!g) return HEADER_HEIGHT
    const expanded = g.model === cellProps.activeKey
    const avail = getAvailableToAdd(g, cellProps.catalogue, cellProps.catalogueAvailable)
    const hasAddRow = avail.variables.length + avail.parameters.length > 0
    return estimateRowHeight(g, expanded, hasAddRow)
  }, [])

  const handleManualAdd = () => {
    const model    = manualModel.trim()
    const variable = manualVar.trim()
    if (!model || !variable) return
    setGroups(prev => {
      const existing = prev.find(g => g.model === model)
      if (existing) {
        if (existing.curves.some(c => c.variable === variable)) return prev
        return prev.map(g =>
          g.model !== model ? g : { ...g, curves: [...g.curves, { variable, active: true, extra: true }] }
        )
      }
      return [...prev, { model, lib: catalogue[model]?.lib ?? dydModels[model] ?? '', curves: [{ variable, active: true, extra: true }] }]
    })
    setSelection(prev => ({ ...prev, [selKey(model, variable)]: true }))
    setManualVar('')
  }

  const handleClearLog = async () => {
    await client.delete('/curves/changelog', scoped(scope))
    await fetchChangelog(scope)
  }

  const handleRevert = async (entry: LogEntry) => {
    try {
      const res = await client.post<{ ok: boolean; warned: boolean }>(
        `/curves/changelog/revert/${encodeURIComponent(entry.id)}`
      )
      await fetchList(scope)
      await fetchChangelog(scope)
      await fetchTargets()
      if (res.data.warned)
        setSuccess('Reverted — note: later entries modified the same curves; the log may be inconsistent.')
      else
        setSuccess(`Reverted changes from ${entry.timestamp}.`)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Revert failed')
    }
  }

  if (noCrv) {
    return (
      <div style={{ maxWidth: 600 }}>
        {modalCtx}
        <Title level={3}>Edit Curves</Title>
        {jobSelector}
        {error && (
          <Alert type="error" description={error} style={{ marginBottom: 12 }}
            closable={{ onClose: () => setError(null) }} />
        )}
        <Alert
          type="info"
          description={scope
            ? `${scope} links no .crv file. Create one to start defining its output curves.`
            : 'No .crv file is linked in this session. Create one to start defining output curves.'}
          style={{ marginBottom: 16 }}
        />
        <Text style={{ display: 'block', marginBottom: 6 }}>Filename</Text>
        <Space.Compact style={{ width: '100%' }}>
          <Input
            value={newCrvName}
            onChange={e => setNewCrvName(e.target.value)}
            onPressEnter={handleCreate}
            placeholder="curves.crv"
          />
          <Button type="primary" loading={creating} disabled={!newCrvName.trim()} onClick={handleCreate}>
            Create
          </Button>
        </Space.Compact>
        {targets.length > 1 && (
          <Checkbox
            checked={linkAllJobs}
            onChange={e => setLinkAllJobs(e.target.checked)}
            style={{ marginTop: 10 }}
          >
            <Text style={{ fontSize: 12 }}>
              Link this curves file in every job (they will then share one .crv)
            </Text>
          </Checkbox>
        )}
        {initInfo?.has_jobs && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
            {linkAllJobs || !scope
              ? 'The .jobs file(s) will be updated to reference this curves file.'
              : `${scope} will be updated to reference this curves file.`}
          </Text>
        )}
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 900 }}>
      {modalCtx}
      <Title level={3}>Edit Curves</Title>
      {jobSelector}

      {modified && (
        <Alert
          type="warning"
          description={
            <Space>
              <Text>{crvFile} has been modified.</Text>
              <Button size="small" icon={<RedoOutlined />} onClick={handleRestore}>
                Restore original
              </Button>
            </Space>
          }
          style={{ marginBottom: 8 }}
        />
      )}

      {success && (
        <Alert type="success" description={success} style={{ marginBottom: 8 }}
          closable={{ onClose: () => setSuccess(null) }} />
      )}
      {error && (
        <Alert type="error" description={error} style={{ marginBottom: 8 }}
          closable={{ onClose: () => setError(null) }} />
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        {targets.length > 1 && (
          <Tooltip title="Replay the same additions and removals on every other job's .crv file. Their own extra curves are kept, and a curve whose model isn't in a job's .dyd is skipped there.">
            <Checkbox checked={applyAll} onChange={e => setApplyAll(e.target.checked)}>
              <Text style={{ fontSize: 13 }}>Apply to all jobs</Text>
            </Checkbox>
          </Tooltip>
        )}
        {catalogueAvailable && (
          <Tooltip title="Adds every variable of every model. Parameters are added per model.">
            <Button icon={<CheckSquareOutlined />} onClick={handleSelectAllCatalogue}>
              Select all variables
            </Button>
          </Tooltip>
        )}
        <Button
          type="primary"
          icon={<SaveOutlined />}
          disabled={!dirty}
          loading={saving}
          onClick={handleApply}
        >
          Apply changes
        </Button>
      </div>

      {displayGroups.length > 0 && (
        <Input.Search
          placeholder="Filter by model ID or library…"
          allowClear
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ marginBottom: 8 }}
        />
      )}
      {displayGroups.length > 0
        ? filteredGroups.length > 0
          ? (
            <div style={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: 8 }}>
              <List
                rowComponent={ModelRow}
                rowCount={filteredGroups.length}
                rowHeight={rowHeight}
                rowProps={rowProps}
                style={{ height: 480 }}
              />
            </div>
          )
          : <Alert type="info" description="No models match your search." />
        : null
      }

      {/* ── Manual add ── always visible so the user can add curves from scratch */}
      <div style={{ marginTop: displayGroups.length > 0 ? 12 : 0, padding: '10px 14px', border: '1px dashed rgba(0,0,0,0.15)', borderRadius: 6 }}>
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
          Add a curve
        </Text>
        <Flex gap={8}>
          <Input
            size="small"
            placeholder="Model ID"
            value={manualModel}
            onChange={e => setManualModel(e.target.value)}
            style={{ flex: 1 }}
          />
          <Input
            size="small"
            placeholder="Variable name"
            value={manualVar}
            onChange={e => setManualVar(e.target.value)}
            onPressEnter={handleManualAdd}
            style={{ flex: 1 }}
          />
          <Button
            size="small"
            icon={<PlusOutlined />}
            onClick={handleManualAdd}
            disabled={!manualModel.trim() || !manualVar.trim()}
          >
            Add
          </Button>
        </Flex>
      </div>

      {changelog.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Text strong>Change log</Text>
            <Button size="small" icon={<DeleteOutlined />} onClick={handleClearLog}>
              Clear all
            </Button>
          </div>
          <Collapse items={
            changelog.slice().reverse().map((entry, i) => {
              const removed = entry.changes.filter(c => c.action === 'removed')
              const added   = entry.changes.filter(c => c.action === 'added')
              return {
                key: `${entry.timestamp}-${i}`,
                label: (
                  <Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>{entry.timestamp}</Text>
                    {removed.length > 0 && <Tag color="red">{removed.length} removed</Tag>}
                    {added.length   > 0 && <Tag color="green">{added.length} added</Tag>}
                  </Space>
                ),
                extra: (
                  <Button
                    size="small"
                    icon={<RollbackOutlined />}
                    onClick={e => { e.stopPropagation(); handleRevert(entry) }}
                  >
                    Revert
                  </Button>
                ),
                children: (
                  <Flex vertical gap={6}>
                    {entry.changes.map((c, j) => (
                      <Space key={j}>
                        <Tag color={c.action === 'removed' ? 'red' : 'green'}>{c.action}</Tag>
                        <Text code style={{ fontSize: 11 }}>{c.model}</Text>
                        <Text type="secondary">›</Text>
                        <Text code style={{ fontSize: 11 }}>{c.variable}</Text>
                      </Space>
                    ))}
                  </Flex>
                ),
              }
            })
          } />
        </div>
      )}
    </div>
  )
}
