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
  Alert, Badge, Button, Checkbox, Collapse, Flex, Input, Popconfirm, Select, Space, Tag, Tooltip, Typography,
} from 'antd'
import { CaretRightOutlined, CheckSquareOutlined, CopyOutlined, DeleteOutlined, PlusOutlined, RedoOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import { List, type RowComponentProps } from 'react-window'
import client from '../api/client'

const { Title, Text } = Typography

interface CurveEntry  { variable: string; active: boolean; extra?: boolean }
interface CurveGroup  { model: string; lib: string; curves: CurveEntry[] }
interface ListResp    { crv_file: string; modified: boolean; groups: CurveGroup[]; dyd_models: Record<string, string> }
interface CurveChange { model: string; variable: string; action: 'added' | 'removed' }
interface LogEntry    { timestamp: string; crv_file: string; changes: CurveChange[] }

interface CatalogueEntry { lib: string; variables: string[] }
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

export function invalidateEditCurvesCache() {
  editCurvesCache.loaded = false
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
const ADD_ROW_HEIGHT = 48
const CONTENT_PADDING = 12

function getAvailableToAdd(
  g: CurveGroup,
  catalogue: Record<string, CatalogueEntry>,
  catalogueAvailable: boolean,
): string[] {
  return catalogueAvailable
    ? (catalogue[g.model]?.variables ?? []).filter(v => !g.curves.some(c => c.variable === v))
    : []
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
  handleSelectModelFromCatalogue: (model: string) => void
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
          {availableToAdd.length > 0 && (
            <div style={{ paddingTop: 6, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
              <Flex gap={8}>
                <Select
                  showSearch={{ optionFilterProp: 'label' }}
                  placeholder={<><PlusOutlined style={{ marginRight: 4 }} />Add variable from catalogue…</>}
                  size="small"
                  style={{ flex: 1 }}
                  value={pickerVals[g.model]}
                  onChange={(v: string) => handleAddVariable(g.model, v)}
                  options={availableToAdd.map(v => ({ label: v, value: v }))}
                />
                <Button
                  size="small"
                  icon={<CheckSquareOutlined />}
                  onClick={() => handleSelectModelFromCatalogue(g.model)}
                >
                  Add all ({availableToAdd.length})
                </Button>
              </Flex>
            </div>
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

  const fetchList = async () => {
    try {
      const res = await client.get<ListResp>('/curves/list')
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

  const fetchChangelog = async () => {
    try {
      const res = await client.get<LogEntry[]>('/curves/changelog')
      setChangelog(res.data)
    } catch {}
  }

  const fetchCatalogue = async () => {
    try {
      const res = await client.get<CatalogueResp>('/curves/catalogue')
      // Only upgrade available; never downgrade — a transient false must not clear catalogue
      if (res.data.available) {
        setCatalogueAvailable(true)
        setCatalogue(res.data.catalogue)
      }
    } catch {}
  }

  const fetchInitInfo = async () => {
    try {
      const res = await client.get<InitInfo>('/curves/init-info')
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

  // Skip the refetch on remount only when a .crv was actually loaded — that's the case with
  // in-progress edits worth protecting. When the last known state was "no .crv" there's nothing
  // to lose, and re-checking lets the page pick up a .crv uploaded since the last visit (e.g. via
  // the Upload page) instead of getting stuck showing a stale "no .crv" screen forever.
  useEffect(() => {
    if (editCurvesCache.loaded && !editCurvesCache.noCrv) return
    editCurvesCache.loaded = true
    fetchList(); fetchChangelog(); fetchCatalogue()
  }, [])

  // Re-fetch catalogue whenever the editor becomes active (noCrv: true → false).
  // This is belt-and-suspenders: if the fetchCatalogue inside handleCreate returned
  // available:false (transient backend state), this effect fires after the transition
  // and gets a fresh result with the correct data.
  const prevNoCrvRef = useRef<boolean | null>(null)
  useEffect(() => {
    const prev = prevNoCrvRef.current
    prevNoCrvRef.current = noCrv
    if (prev === true && !noCrv) fetchCatalogue()
  }, [noCrv])

  useEffect(() => { if (noCrv) fetchInitInfo() }, [noCrv])

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
      const res = await client.put<{ changed: number }>('/curves/apply', { curves })
      await fetchList()
      await fetchChangelog()
      setSuccess(`${res.data.changed} curve(s) updated.`)
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Apply failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = async () => {
    try {
      await client.post('/curves/restore')
      await fetchList()
      await fetchChangelog()
      setSuccess('Curves file restored to original.')
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Restore failed')
    }
  }

  const handleCreate = async () => {
    const name = newCrvName.trim() || 'curves.crv'
    setCreating(true); setError(null)
    try {
      await client.post('/curves/init', { crv_filename: name })

      // Fetch list and catalogue in parallel, then apply ALL state updates in one
      // synchronous block so React batches them into a single render.  This is the
      // only reliable way to guarantee that displayGroups has catalogue data at the
      // exact moment noCrv flips to false.
      const [listRes, catRes] = await Promise.all([
        client.get<ListResp>('/curves/list'),
        client.get<CatalogueResp>('/curves/catalogue').catch(() => null),
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

      await fetchChangelog()
    } catch (e: any) {
      setError(e.response?.data?.detail ?? 'Failed to create curves file')
    } finally {
      setCreating(false)
    }
  }

  const handleSelectModelFromCatalogue = (model: string) => {
    const entry = catalogue[model]
    if (!entry) return
    setGroups(prev => {
      const idx = prev.findIndex(g => g.model === model)
      if (idx >= 0) {
        const existingVars = new Set(prev[idx].curves.map(c => c.variable))
        const toAdd = entry.variables.filter(v => !existingVars.has(v))
        if (toAdd.length === 0) return prev
        const next = [...prev]
        next[idx] = { ...next[idx], curves: [...next[idx].curves, ...toAdd.map(v => ({ variable: v, active: true, extra: true }))] }
        return next
      }
      return [...prev, { model, lib: entry.lib, curves: entry.variables.map(v => ({ variable: v, active: true, extra: true })) }]
    })
    setSelection(prev => {
      const next = { ...prev }
      for (const v of entry.variables) next[selKey(model, v)] = true
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
    const hasAddRow = getAvailableToAdd(g, cellProps.catalogue, cellProps.catalogueAvailable).length > 0
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
    await client.delete('/curves/changelog')
    await fetchChangelog()
  }

  const handleRevert = async (entry: LogEntry) => {
    try {
      const res = await client.post<{ ok: boolean; warned: boolean }>(
        `/curves/changelog/revert/${encodeURIComponent(entry.timestamp)}`
      )
      await fetchList()
      await fetchChangelog()
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
        <Title level={3}>Edit Curves</Title>
        {error && (
          <Alert type="error" description={error} style={{ marginBottom: 12 }}
            closable={{ onClose: () => setError(null) }} />
        )}
        <Alert
          type="info"
          description="No .crv file is linked in this session. Create one to start defining output curves."
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
        {initInfo?.has_jobs && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
            The .jobs file will be updated to reference this curves file.
          </Text>
        )}
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Edit Curves</Title>

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

      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginBottom: 12 }}>
        {catalogueAvailable && (
          <Button icon={<CheckSquareOutlined />} onClick={handleSelectAllCatalogue}>
            Select all from catalogue
          </Button>
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
