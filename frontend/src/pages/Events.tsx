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
  Alert, Button, Card, Checkbox, Input, Popconfirm, Radio, Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, SaveOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail, isTransient, retryTransient } from '../api/errors'

const { Title, Text } = Typography

type Kind = 'network' | 'dynamic'

interface CatalogueEvent { id: string; scope: Kind; family_label: string; label: string; lib: string; equipment_type: string; parametrisable: boolean }
interface Family         { scope: Kind; label: string; description: string }
interface CatalogueResp  { dynawo_available: boolean; events: CatalogueEvent[]; families: Family[] }

// One selectable object. `kind` is what the user picked it by — an IIDM id or a
// .dyd model id — and it decides which family of events applies, so the same
// line can appear twice, once per kind.
interface Target      { kind: Kind; id: string; equipment_type: string; iidm_type: string; lib: string | null; static_id: string; event_ids: string[] }
interface TargetsResp { targets: Target[]; total: number; truncated: boolean; network_loaded: boolean; network_file: string | null; dyd_model_count: number; excluded_modelled: number; dynawo_available: boolean }

interface Field { name: string; value_type: string; default: string | null }

// One <connectPattern> resolved against the two sides: the fragment searched
// for, every variable containing it, and the closest pair — a proposal the user
// confirms, not a decision.
interface Connection {
  pattern_var1: string; pattern_var2: string
  var1_matches: string[]; var2_matches: string[]
  var1: string | null; var2: string | null
  resolved: boolean; ambiguous: boolean
}

interface FormResp {
  event: { id: string; label: string; scope: Kind; lib: string; equipment_type: string; automatic: boolean; destination: string }
  connect_to: string
  target: { id: string; kind: Kind; lib: string | null }
  descriptor_available: boolean
  descriptor_found: boolean
  network_model_found: boolean | null
  fields: Field[]
  // Values the event imposes (a connection is EventConnectedStatus with
  // event_open=false): shown so the user knows, never offered for editing.
  fixed: { name: string; value: string }[]
  // Values the catalogue would impose but that designate no single parameter of
  // the installed Dynawo — those parameters are in `fields`, for the user to set.
  unresolved_fixed: { pattern: string; value: string }[]
  connections: Connection[]
  variables: { event: string[]; target: string[] }
}

// A wire as the user will have it written: either the proposal, or his own pick.
interface Wire { var1: string | null; var2: string | null }

// An event already composed and waiting: the .dyd and .par are written with
// all of them at once, so they queue up here first.
interface StagedConnection { var1: string; id2: string; var2: string }
interface StagedEvent {
  entry_id: string; event_id: string; label: string; target_id: string; kind: Kind
  model_id: string; lib: string
  parameters: { name: string; value: string; type: string }[]
  connections: StagedConnection[]
}
interface WriteInfo { suggested_filename: string; jobs_files: string[]; staged_count: number }
// A .dyd of the session holding events — recognised by its models' libraries,
// so a file written on an earlier visit (or by hand) is offered here too.
interface EventFile { dyd_file: string; event_count: number; skipped: number; other_models: number; jobs_files: string[] }
interface LoadResp { source: string; par_file: string; other_models: number; loaded: number; skipped: { model_id: string; reason: string }[]; events: StagedEvent[] }
interface WriteResp { dyd_file: string; par_file: string; events: number; jobs_file: string | null; jobs_patched: number; model_ids: string[] }

interface FileEntry { name: string; ftype: string }

const KIND_LABEL: Record<Kind, string> = { network: 'Network object', dynamic: 'Dynamic model' }
const PICK_LABEL: Record<Kind, string> = { network: 'Pick by IIDM id', dynamic: 'Pick by dynamic model id' }
// Sentinel for the "new file" entry of the select, kept apart from real filenames.
const NEW_FILE = '__new_events_file__'

function FixedParameters({ form }: { form: FormResp }) {
  const { fixed, unresolved_fixed: unresolved } = form
  if (!fixed.length && !unresolved.length) return null
  return (
    <Space direction="vertical" size={2}>
      {fixed.length > 0 && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          Set by this event: {fixed.map((f, i) => (
            <span key={f.name}>{i > 0 && ', '}<Text code style={{ fontSize: 12 }}>{f.name} = {f.value}</Text></span>
          ))}
        </Text>
      )}
      {unresolved.map(u => (
        <Text key={u.pattern} type="warning" style={{ fontSize: 12 }}>
          This event normally sets the parameter matching “{u.pattern}” to {u.value}, but {form.event.lib} in the
          installed Dynawo has no single parameter matching it — set it above yourself.
        </Text>
      ))}
    </Space>
  )
}

// Matching variables first — they are already ranked closest-first by the
// backend — then the rest of the side's variables, so an override starts from
// the plausible names without hiding the others.
function variableOptions(matches: string[], all: string[]) {
  const rest = all.filter(v => !matches.includes(v))
  return [
    ...(matches.length ? [{ label: 'Matching the pattern', options: matches.map(v => ({ value: v, label: v })) }] : []),
    ...(rest.length ? [{ label: 'Other variables', options: rest.map(v => ({ value: v, label: v })) }] : []),
  ]
}

// The .par is never named separately: it is written with the .dyd and only the
// two of them reference each other.
function parFileFor(dydName: string): string {
  const trimmed = dydName.trim() || 'events.dyd'
  return trimmed.replace(/\.dyd$/, '') + '.par'
}

// Every parameter of an event library is named event_<something>, so the prefix
// carries nothing here and only makes the line harder to read at a glance. The
// full names stay one hover — and one click into the editor — away.
function parameterSummary(e: StagedEvent): string {
  return e.parameters.map(p => `${p.name.replace(/^event_/, '')} ${p.value}`).join(' · ')
}

function proposedWires(connections: Connection[]): Wire[] {
  return connections.map(c => ({ var1: c.var1, var2: c.var2 }))
}

function defaultValues(fields: Field[]): Record<string, string> {
  const vals: Record<string, string> = {}
  for (const f of fields) vals[f.name] = f.default ?? (f.value_type === 'BOOL' ? 'false' : '')
  return vals
}

// Module-level cache: react-router unmounts a route as soon as the user leaves
// it, so without this every page change would discard the event being composed
// and the filename typed for the write. The staged events themselves live on
// the session, server-side — this only keeps the work in progress.
interface EventsCache {
  kind: Kind
  jobsFile: string | null
  search: string
  targetId: string | null
  eventId: string | null
  form: FormResp | null
  values: Record<string, string>
  autoConnect: boolean
  wires: Wire[]
  currentFile: string | null
  isNew: boolean
  dirty: boolean
}

const eventsCache: EventsCache = {
  kind: 'network',
  jobsFile: null,
  search: '',
  targetId: null,
  eventId: null,
  form: null,
  values: {},
  autoConnect: true,
  wires: [],
  currentFile: null,
  isNew: false,
  dirty: false,
}

// ── Editing a staged event ──────────────────────────────────────────────────
// What can change is what the page chose after the object and the event: the
// parameter values, the wiring, and the id the blackBoxModel will take. The
// event and its object are not editable — changing those makes it a different
// event, which is what removing it and composing another is for.
interface StagedEditorProps {
  staged: StagedEvent
  form: FormResp | null
  values: Record<string, string>
  setValues: React.Dispatch<React.SetStateAction<Record<string, string>>>
  wires: Wire[]
  setWires: React.Dispatch<React.SetStateAction<Wire[]>>
  modelId: string
  setModelId: (v: string) => void
  saving: boolean
  onApply: () => void
  onCancel: () => void
}

function StagedEditor({
  staged, form, values, setValues, wires, setWires, modelId, setModelId, saving, onApply, onCancel,
}: StagedEditorProps) {
  if (!form) return <Text type="secondary">Reading {staged.lib}'s descriptor…</Text>

  const complete = form.fields.every(f => (values[f.name] ?? '').trim() !== '')
    && wires.length > 0 && wires.every(w => w.var1 && w.var2)

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Space direction="vertical" size={2}>
        <Text type="secondary" style={{ fontSize: 12 }}>Model id in the .dyd (names its parameter set too)</Text>
        <Input style={{ width: 340 }} value={modelId} onChange={e => setModelId(e.target.value)} />
      </Space>

      <Table
        size="small"
        pagination={false}
        dataSource={form.fields}
        rowKey="name"
        columns={[
          { title: 'Parameter', key: 'name', render: (_: unknown, f: Field) => <Text code>{f.name}</Text> },
          { title: 'Type', dataIndex: 'value_type', key: 'type', width: 80, render: (t: string) => <Tag>{t}</Tag> },
          {
            title: 'Value', key: 'value', width: 220,
            render: (_: unknown, f: Field) => {
              const val = values[f.name] ?? ''
              if (f.value_type === 'BOOL') {
                return (
                  <Checkbox
                    checked={val.toLowerCase() === 'true'}
                    onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.checked ? 'true' : 'false' }))}
                  />
                )
              }
              return <Input value={val} onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.value }))} />
            },
          },
        ]}
      />

      <FixedParameters form={form} />

      {form.connections.map((c, i) => {
        const wire = wires[i] ?? { var1: null, var2: null }
        const setWire = (side: 'var1' | 'var2', v: string) =>
          setWires(prev => prev.map((w, j) => (j === i ? { ...w, [side]: v } : w)))
        return (
          <Space key={i} wrap align="end">
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>{form.event.lib} (the event)</Text>
              <Select showSearch style={{ width: 280 }} value={wire.var1}
                      onChange={v => setWire('var1', v)}
                      options={variableOptions(c.var1_matches, form.variables.event)} />
            </Space>
            <Text type="secondary" style={{ paddingBottom: 6 }}>↔</Text>
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>{form.connect_to} (the object)</Text>
              <Select showSearch style={{ width: 280 }} value={wire.var2}
                      onChange={v => setWire('var2', v)}
                      options={variableOptions(c.var2_matches, form.variables.target)} />
            </Space>
          </Space>
        )
      })}

      <Space>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!complete} onClick={onApply}>
          Apply changes
        </Button>
        <Button onClick={onCancel}>Cancel</Button>
      </Space>
    </Space>
  )
}

export default function Events() {
  const [jobsFiles, setJobsFiles] = useState<string[]>([])
  const [jobsFile, setJobsFile] = useState<string | null>(() => eventsCache.jobsFile)

  const [catalogue, setCatalogue] = useState<CatalogueEvent[]>([])
  const [families, setFamilies] = useState<Family[]>([])

  const [kind, setKind] = useState<Kind>(() => eventsCache.kind)
  const [search, setSearch] = useState(() => eventsCache.search)
  const [targets, setTargets] = useState<TargetsResp | null>(null)
  const [loadingTargets, setLoadingTargets] = useState(false)

  const [targetId, setTargetId] = useState<string | null>(() => eventsCache.targetId)
  const [eventId, setEventId] = useState<string | null>(() => eventsCache.eventId)
  const [form, setForm] = useState<FormResp | null>(() => eventsCache.form)
  const [values, setValues] = useState<Record<string, string>>(() => eventsCache.values)
  // Ticked whenever the catalogue allows it; unticking hands the wiring back to
  // the user, which is also what an event with <automatic>false</automatic> does.
  const [autoConnect, setAutoConnect] = useState(() => eventsCache.autoConnect)
  const [wires, setWires] = useState<Wire[]>(() => eventsCache.wires)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [staged, setStaged] = useState<StagedEvent[]>([])
  const [sessionFiles, setSessionFiles] = useState<string[]>([])
  const [eventFiles, setEventFiles] = useState<EventFile[]>([])
  // The staged event open for editing, with the descriptor of its own event —
  // the parameter types and the variables of both sides come from there, so an
  // edit is checked against exactly what staging was checked against.
  const [editing, setEditing] = useState<string | null>(null)
  const [editForm, setEditForm] = useState<FormResp | null>(null)
  const [editValues, setEditValues] = useState<Record<string, string>>({})
  const [editWires, setEditWires] = useState<Wire[]>([])
  const [editModelId, setEditModelId] = useState('')
  const [savingEdit, setSavingEdit] = useState(false)
  // The events file being edited. The list below always holds its content, so
  // there is never a set of events belonging to nothing.
  const [currentFile, setCurrentFile] = useState<string | null>(() => eventsCache.currentFile)
  // A file named but not written yet — it exists only as the list until saved.
  const [isNew, setIsNew] = useState(() => eventsCache.isNew)
  const [dirty, setDirty] = useState(() => eventsCache.dirty)
  const [writing, setWriting] = useState(false)

  useEffect(() => { eventsCache.kind = kind }, [kind])
  useEffect(() => { eventsCache.jobsFile = jobsFile }, [jobsFile])
  useEffect(() => { eventsCache.search = search }, [search])
  useEffect(() => { eventsCache.targetId = targetId }, [targetId])
  useEffect(() => { eventsCache.eventId = eventId }, [eventId])
  useEffect(() => { eventsCache.form = form }, [form])
  useEffect(() => { eventsCache.values = values }, [values])
  useEffect(() => { eventsCache.autoConnect = autoConnect }, [autoConnect])
  useEffect(() => { eventsCache.wires = wires }, [wires])
  useEffect(() => { eventsCache.currentFile = currentFile }, [currentFile])
  useEffect(() => { eventsCache.isNew = isNew }, [isNew])
  useEffect(() => { eventsCache.dirty = dirty }, [dirty])

  const eventsById = useMemo(
    () => Object.fromEntries(catalogue.map(e => [e.id, e])),
    [catalogue],
  )
  const target = targets?.targets.find(t => t.id === targetId && t.kind === kind) ?? null

  const refreshStaged = useCallback(async (job?: string | null) => {
    const [list, files] = await Promise.all([
      client.get<{ events: StagedEvent[] }>('/events/staged'),
      client.get<{ files: EventFile[] }>('/events/files', { params: job ? { jobs_file: job } : {} }),
    ])
    setStaged(list.data.events)
    setEventFiles(files.data.files)
    return files.data.files
  }, [])

  useEffect(() => {
    retryTransient(() => client.get<FileEntry[]>('/files/'))
      .then(res => {
        const jobs = res.data.filter(f => f.ftype === 'jobs').map(f => f.name)
        setJobsFiles(jobs)
        // One job in the session is the job: leaving it unselected only meant
        // saving an events file that no job declares.
        if (jobs.length === 1) setJobsFile(prev => prev ?? jobs[0])
        // Every name, so the page can tell a write that would replace a file
        // from one that creates it.
        setSessionFiles(res.data.map(f => f.name))
      })
      .catch(() => setJobsFiles([]))
    retryTransient(() => client.get<CatalogueResp>('/events/catalogue'))
      .then(res => {
        setCatalogue(res.data.events)
        setFamilies(res.data.families)
        // The catalogue decides which families exist at all: one holding no
        // event must not be offered, and must not stay selected either.
        if (res.data.families.length && !res.data.families.some(f => f.scope === 'network'))
          setKind(res.data.families[0].scope)
      })
      .catch(err => setError(errorDetail(err, 'Could not read the event catalogue')))
    // Chained rather than called straight from the effect body: what it sets
    // belongs to the response, not to the render that scheduled it.
    Promise.resolve().then(() => refreshStaged())
      .catch(err => setError(errorDetail(err, 'Could not read back the events staged so far')))
  }, [refreshStaged])

  // Targets are searched server-side: a large network holds tens of thousands
  // of ids, far more than a Select should ever receive at once.
  const debounce = useRef<number | undefined>(undefined)
  const fetchTargets = useCallback((q: string) => {
    setLoadingTargets(true)
    retryTransient(() => client.get<TargetsResp>('/events/targets', {
      params: { kind, q, ...(jobsFile ? { jobs_file: jobsFile } : {}) },
    }))
      .then(res => setTargets(res.data))
      .catch(err => setError(errorDetail(
        err,
        isTransient(err)
          ? 'The server did not answer — it may still be starting. Try again in a moment.'
          : 'Could not list the objects',
      )))
      .finally(() => setLoadingTargets(false))
  }, [kind, jobsFile])

  useEffect(() => {
    window.clearTimeout(debounce.current)
    debounce.current = window.setTimeout(() => fetchTargets(search), 250)
    return () => window.clearTimeout(debounce.current)
  }, [search, fetchTargets])

  // Picking another object, kind or job invalidates what was chosen below it:
  // the events offered are those of the new selection, not of the old one.
  const selectTarget = (id: string | null) => { setTargetId(id); setEventId(null); setForm(null) }
  const selectKind = (k: Kind) => { setKind(k); selectTarget(null) }
  const selectJobsFile = (f: string | null) => { setJobsFile(f); selectTarget(null) }

  // The form is fetched on the event being picked rather than watched through
  // an effect: it is the descriptor of that one event applied to that one
  // object, and nothing else can invalidate it.
  const selectEvent = (id: string) => {
    setEventId(id)
    if (!targetId) return
    setError(null)
    client.get<FormResp>('/events/form', {
      params: { event_id: id, target_id: targetId, kind, ...(jobsFile ? { jobs_file: jobsFile } : {}) },
    })
      .then(res => {
        setForm(res.data)
        setValues(defaultValues(res.data.fields))
        setAutoConnect(res.data.event.automatic)
        setWires(proposedWires(res.data.connections))
      })
      .catch(err => { setForm(null); setError(err.response?.data?.detail ?? 'Could not build the parameter form') })
  }

  // Composing an event ends here: it joins the list, and the object stays
  // selected so applying several events to the same one is one click each.
  const addStagedEvent = async () => {
    if (!form || !eventId || !targetId) return
    setError(null)
    try {
      const res = await client.post<StagedEvent>('/events/staged', {
        event_id: eventId,
        target_id: targetId,
        kind,
        parameters: values,
        connections: wires.map(w => ({ var1: w.var1 ?? '', var2: w.var2 ?? '' })),
        ...(jobsFile ? { jobs_file: jobsFile } : {}),
      })
      await refreshStaged(jobsFile)
      setDirty(true)
      setNotice(`${res.data.label} on ${res.data.target_id} added.`)
      setEventId(null)
      setForm(null)
    } catch (err) {
      setError(errorDetail(err, 'Could not add the event'))
    }
  }

  const openEditor = async (e: StagedEvent) => {
    setError(null)
    if (editing === e.entry_id) { setEditing(null); return }
    setEditing(e.entry_id)
    setEditModelId(e.model_id)
    setEditValues(Object.fromEntries(e.parameters.map(p => [p.name, p.value])))
    setEditWires(e.connections.map(c => ({ var1: c.var1, var2: c.var2 })))
    setEditForm(null)
    try {
      const res = await client.get<FormResp>('/events/form', {
        params: { event_id: e.event_id, target_id: e.target_id, kind: e.kind, ...(jobsFile ? { jobs_file: jobsFile } : {}) },
      })
      setEditForm(res.data)
    } catch (err) {
      setError(errorDetail(err, 'Could not read the descriptor of that event'))
    }
  }

  const applyEdit = async (entryId: string) => {
    setSavingEdit(true)
    setError(null)
    try {
      const res = await client.put<StagedEvent>(`/events/staged/${entryId}`, {
        parameters: editValues,
        connections: editWires.map(w => ({ var1: w.var1 ?? '', var2: w.var2 ?? '' })),
        model_id: editModelId,
        ...(jobsFile ? { jobs_file: jobsFile } : {}),
      })
      await refreshStaged(jobsFile)
      setDirty(true)
      setEditing(null)
      setNotice(`${res.data.model_id} updated.`)
    } catch (err) {
      setError(errorDetail(err, 'Could not update the event'))
    } finally {
      setSavingEdit(false)
    }
  }

  const removeStagedEvent = async (entryId: string) => {
    setError(null)
    try {
      await client.delete(`/events/staged/${entryId}`)
      if (editing === entryId) setEditing(null)
      await refreshStaged(jobsFile)
      setDirty(true)
    } catch (err) {
      setError(errorDetail(err, 'Could not remove that event from the list'))
    }
  }

  // Opening a file: the list becomes its content, so what is on screen is what
  // the job runs. Anything composed but not saved is gone — the confirmation in
  // front of this is what makes that the user's decision.
  const openFile = async (dydFile: string) => {
    setError(null)
    try {
      const res = await client.post<LoadResp>('/events/load', { dyd_file: dydFile })
      await refreshStaged(jobsFile)
      setCurrentFile(res.data.source)
      setIsNew(false)
      setDirty(false)
      setEditing(null)
      setNotice(
        `${res.data.loaded} event(s) opened from ${res.data.source}.`
        + (res.data.skipped.length ? ` ${res.data.skipped.length} model(s) left out: ${res.data.skipped[0].reason}.` : '')
      )
    } catch (err) {
      setError(errorDetail(err, 'Could not open that file'))
    }
  }

  // A file that does not exist yet: named now, written on the first save.
  const startNewFile = async () => {
    setError(null)
    try {
      const info = await client.get<WriteInfo>('/events/write-info', {
        params: jobsFile ? { jobs_file: jobsFile } : {},
      })
      await client.delete('/events/staged')
      await refreshStaged(jobsFile)
      setCurrentFile(info.data.suggested_filename)
      setIsNew(true)
      setDirty(false)
      setEditing(null)
      setNotice(null)
    } catch (err) {
      setError(errorDetail(err, 'Could not start a new events file'))
    }
  }

  const saveFile = async () => {
    if (!currentFile) return
    setWriting(true)
    setError(null)
    try {
      const res = await client.post<WriteResp>('/events/write', {
        dyd_filename: currentFile,
        jobs_file: jobsFile,
        // Saving a file that is open replaces it; a new name must not silently
        // land on a file of the session that happens to be called that.
        overwrite: !isNew,
      })
      setCurrentFile(res.data.dyd_file)
      setIsNew(false)
      setDirty(false)
      const files = await client.get<FileEntry[]>('/files/')
      setSessionFiles(files.data.map(f => f.name))
      await refreshStaged(jobsFile)
      setNotice(
        `${res.data.events} event(s) saved in ${res.data.dyd_file} and ${res.data.par_file}.`
        + (res.data.jobs_patched ? ` Declared in ${res.data.jobs_file}.` : '')
      )
    } catch (err) {
      setError(errorDetail(err, 'Could not save the file'))
    } finally {
      setWriting(false)
    }
  }

  const clearStaged = async () => {
    setError(null)
    try {
      await client.delete('/events/staged')
      setEditing(null)
      await refreshStaged(jobsFile)
      setDirty(true)
    } catch (err) {
      setError(errorDetail(err, 'Could not clear the list'))
    }
  }

  // Taking a set of events out of a job, and optionally deleting its files —
  // the other end of the write: what was declared can be undeclared.
  const undeclare = async (dydFile: string, deleteFiles: boolean) => {
    setError(null)
    try {
      const res = await client.post<{ jobs_updated: string[]; deleted: string[] }>(
        '/events/undeclare', { dyd_file: dydFile, delete_files: deleteFiles },
      )
      const files = await client.get<FileEntry[]>('/files/')
      setSessionFiles(files.data.map(f => f.name))
      if (currentFile === dydFile && deleteFiles) { setCurrentFile(null); setDirty(false) }
      await refreshStaged(jobsFile)
      setNotice(
        res.data.jobs_updated.length
          ? `${dydFile} taken out of ${res.data.jobs_updated.join(', ')}.`
              + (res.data.deleted.length ? ` ${res.data.deleted.join(' and ')} deleted.` : '')
          : res.data.deleted.length
            ? `${res.data.deleted.join(' and ')} deleted — no job declared them.`
            : `No job declared ${dydFile}.`,
      )
    } catch (err) {
      setError(errorDetail(err, 'Could not take those events out of the job'))
    }
  }

  // A file already saved can still be missing from the job — it was saved with
  // no job selected, or taken out of it since. Saving again is what declares it,
  // so the button must be reachable even when nothing else changed.
  const openEntry = eventFiles.find(f => f.dyd_file === currentFile)
  const needsDeclaring = !!currentFile && !isNew && !!jobsFile
    && !!openEntry && !openEntry.jobs_files.includes(jobsFile)

  // A written file whose events have all been removed. If it holds nothing else,
  // there is no such thing as an events file with no event — an empty .dyd is
  // indistinguishable from any other and could not be found again — so what is
  // offered is deleting it. If it also declares models of the network, saving
  // simply takes the events out and leaves the rest.
  const emptied = !!currentFile && !isNew && staged.length === 0
  const emptiedFileOnlyHeldEvents = emptied && (openEntry?.other_models ?? 0) === 0

  const wiresComplete = wires.length > 0 && wires.every(w => w.var1 && w.var2)
  const valuesComplete = !!form && form.fields.every(f => (values[f.name] ?? '').trim() !== '')

  const family = families.find(f => f.scope === kind)
  const noTargets = targets && targets.targets.length === 0 && !loadingTargets

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Events</Title>

      {error && <Alert type="error" description={error} style={{ marginBottom: 16 }} closable onClose={() => setError(null)} />}
      {notice && <Alert type="success" description={notice} style={{ marginBottom: 16 }} closable onClose={() => setNotice(null)} />}

      {/* ── The file being edited, and the job it belongs to ── */}
      <Card style={{ marginBottom: 16 }}>
        <Space wrap align="end" size="middle">
          {jobsFiles.length > 0 && (
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>Job</Text>
              <Select
                style={{ width: 260 }}
                value={jobsFile}
                onChange={selectJobsFile}
                options={jobsFiles.map(f => ({ value: f, label: f }))}
                placeholder="every .dyd of the session"
                allowClear
              />
            </Space>
          )}

          <Space direction="vertical" size={2}>
            <Text type="secondary" style={{ fontSize: 12 }}>Events file</Text>
            <Select
              style={{ width: 360 }}
              value={currentFile}
              placeholder="choose a file, or start a new one"
              onChange={v => {
                if (v === NEW_FILE) { startNewFile(); return }
                if (v !== currentFile) openFile(v)
              }}
              options={[
                ...eventFiles.map(f => ({
                  value: f.dyd_file,
                  label: (
                    <Space>
                      <Text>{f.dyd_file}</Text>
                      <Tag>{f.event_count}</Tag>
                      {f.other_models > 0 && (
                        <Text type="secondary" style={{ fontSize: 12 }}>+ {f.other_models} model(s)</Text>
                      )}
                      {f.jobs_files.length === 0 && (
                        <Text type="secondary" style={{ fontSize: 12 }}>not in any job</Text>
                      )}
                    </Space>
                  ),
                })),
                ...(currentFile && isNew
                  ? [{ value: currentFile, label: <Space><Text>{currentFile}</Text><Tag color="blue">new</Tag></Space> }]
                  : []),
                { value: NEW_FILE, label: <Text type="secondary"><PlusOutlined /> New events file…</Text> },
              ]}
            />
          </Space>

          {currentFile && isNew && (
            <Space direction="vertical" size={2}>
              <Text type="secondary" style={{ fontSize: 12 }}>Name it</Text>
              <Input
                style={{ width: 240 }}
                value={currentFile}
                onChange={e => setCurrentFile(e.target.value)}
                onBlur={() => { if (!currentFile?.trim()) setCurrentFile('events.dyd') }}
                status={sessionFiles.includes(currentFile.trim()) ? 'warning' : undefined}
              />
              {sessionFiles.includes(currentFile.trim()) && (
                <Text type="warning" style={{ fontSize: 12 }}>
                  {currentFile.trim()} is already in the session — pick another name, or open it above.
                </Text>
              )}
            </Space>
          )}

          {currentFile && !isNew && (
            <Space>
              <Popconfirm
                title={`Take ${currentFile} out of the job?`}
                description="The file stays in the session and can be declared again by saving it."
                okText="Take out"
                cancelText="Cancel"
                onConfirm={() => undeclare(currentFile, false)}
              >
                <Button size="small">Take out of the job</Button>
              </Popconfirm>
              <Popconfirm
                title={`Delete ${currentFile} and ${parFileFor(currentFile)}?`}
                description="They are also taken out of every job declaring them."
                okText="Delete"
                okButtonProps={{ danger: true }}
                cancelText="Cancel"
                onConfirm={() => undeclare(currentFile, true)}
              >
                <Button size="small" danger type="text" icon={<DeleteOutlined />} />
              </Popconfirm>
            </Space>
          )}
        </Space>
      </Card>

      {/* ── 1. What the event is applied to ── */}
      <Card title="1 · Object" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {families.length > 1 && (
            <Radio.Group value={kind} onChange={e => selectKind(e.target.value)} optionType="button" buttonStyle="solid">
              {families.map(f => (
                <Radio.Button key={f.scope} value={f.scope}>{PICK_LABEL[f.scope]}</Radio.Button>
              ))}
            </Radio.Group>
          )}
          {family && <Text type="secondary">{family.description}</Text>}

          <Select
            showSearch
            style={{ width: '100%' }}
            placeholder={kind === 'network' ? 'Search an IIDM id…' : 'Search a .dyd model id…'}
            value={targetId}
            loading={loadingTargets}
            onSearch={setSearch}
            onChange={selectTarget}
            filterOption={false}
            notFoundContent={loadingTargets ? 'Searching…' : 'No object with an available event'}
            options={(targets?.targets ?? []).map(t => ({
              value: t.id,
              label: (
                <Space>
                  <Text>{t.id}</Text>
                  {t.equipment_type && <Tag>{t.equipment_type}</Tag>}
                  {t.lib && <Tag color="purple">{t.lib}</Tag>}
                  {t.kind === 'dynamic' && t.static_id &&
                    <Text type="secondary" style={{ fontSize: 12 }}>staticId {t.static_id}</Text>}
                </Space>
              ),
            }))}
          />

          {targets?.truncated && (
            <Text type="secondary">
              {targets.total} objects match — refine the search to see the rest.
            </Text>
          )}
          {kind === 'network' && (targets?.excluded_modelled ?? 0) > 0 && (
            <Text type="secondary">
              {targets?.excluded_modelled} object(s) represented by a dynamic model are not listed: the network
              model does not simulate them, so a network event would not act on them.
            </Text>
          )}
          {noTargets && kind === 'network' && !targets?.network_loaded && (
            <Alert type="info" showIcon description="No IIDM file in the session — upload one to pick objects by their static id." />
          )}
          {noTargets && kind === 'dynamic' && targets?.dyd_model_count === 0 && (
            <Alert type="info" showIcon description="No .dyd file in the session — upload one to pick objects by their dynamic model id." />
          )}
        </Space>
      </Card>

      {/* ── 2. Which event ── */}
      <Card title="2 · Event" style={{ marginBottom: 16 }}>
        {!target ? (
          <Text type="secondary">Pick an object first.</Text>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Select
              style={{ width: '100%' }}
              placeholder={`Events available for this ${KIND_LABEL[kind].toLowerCase()}`}
              value={eventId}
              onChange={selectEvent}
              options={target.event_ids.map(id => ({
                value: id,
                label: (
                  <Space>
                    <Text>{eventsById[id]?.label ?? id}</Text>
                    <Tag color="purple">{eventsById[id]?.lib}</Tag>
                  </Space>
                ),
              }))}
            />
            <Text type="secondary">
              Offered because the {kind === 'network' ? 'object is a ' : 'model exposes the ports these events need — '}
              {kind === 'network' ? <Tag>{target.equipment_type}</Tag> : <Text code>{target.lib}</Text>}
            </Text>
          </Space>
        )}
      </Card>

      {/* ── 3. Parameters, read from the event library's ddb descriptor ── */}
      <Card title="3 · Parameters">
        {!form ? (
          <Text type="secondary">Pick an event first.</Text>
        ) : !form.descriptor_available ? (
          <Alert
            type="warning"
            showIcon
            description="No Dynawo executable is configured, so the descriptor of the event library cannot be read. Set one on the Dynawo Version page to get the parameter list."
          />
        ) : !form.descriptor_found ? (
          <Alert
            type="warning"
            showIcon
            description={`The configured Dynawo install has no ddb/${form.event.lib}.desc.xml — this event is not available in that version.`}
          />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Table
              size="small"
              pagination={false}
              dataSource={form.fields}
              rowKey="name"
              columns={[
                {
                  title: 'Parameter', key: 'name',
                  render: (_: unknown, f: Field) => <Text code>{f.name}</Text>,
                },
                {
                  title: 'Type', dataIndex: 'value_type', key: 'type', width: 80,
                  render: (t: string) => <Tag>{t}</Tag>,
                },
                {
                  title: 'Value', key: 'value', width: 220,
                  render: (_: unknown, f: Field) => {
                    const val = values[f.name] ?? ''
                    if (f.value_type === 'BOOL') {
                      return (
                        <Checkbox
                          checked={val.toLowerCase() === 'true'}
                          onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.checked ? 'true' : 'false' }))}
                        />
                      )
                    }
                    return (
                      <Input
                        value={val}
                        onChange={e => setValues(prev => ({ ...prev, [f.name]: e.target.value }))}
                      />
                    )
                  },
                },
              ]}
            />

            <FixedParameters form={form} />

          </Space>
        )}
      </Card>

      {/* ── 4. Connection: proposed from the catalogue's patterns, or picked by hand ── */}
      <Card title="4 · Connection" style={{ marginTop: 16 }}>
        {!form ? (
          <Text type="secondary">Pick an event first.</Text>
        ) : form.connections.length === 0 ? (
          <Text type="secondary">
            This event declares no connection pattern — the variables to wire are left to you.
          </Text>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {form.event.automatic ? (
              <Checkbox
                checked={autoConnect}
                onChange={e => {
                  setAutoConnect(e.target.checked)
                  if (e.target.checked) setWires(proposedWires(form.connections))
                }}
              >
                Connect automatically, from the pattern declared for this event
              </Checkbox>
            ) : (
              <Text type="secondary">
                This event is not declared automatic: choose the two variables to connect.
              </Text>
            )}

            {form.connections.map((c, i) => {
              const wire = wires[i] ?? { var1: null, var2: null }
              const setWire = (side: 'var1' | 'var2', v: string) =>
                setWires(prev => prev.map((w, j) => (j === i ? { ...w, [side]: v } : w)))
              const editable = !form.event.automatic || !autoConnect

              return (
                <Card key={i} size="small" type="inner" title={
                  <Space size={4}>
                    <Text type="secondary" style={{ fontWeight: 400 }}>pattern</Text>
                    <Text code>{c.pattern_var1}</Text>
                    <Text type="secondary" style={{ fontWeight: 400 }}>↔</Text>
                    <Text code>{c.pattern_var2}</Text>
                  </Space>
                }>
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    {!c.resolved && (
                      <Alert
                        type="warning"
                        showIcon
                        description={
                          `No variable contains ${!c.var1_matches.length ? `"${c.pattern_var1}" on ${form.event.lib}` : `"${c.pattern_var2}" on ${form.connect_to}`}`
                          + ' — pick the variables yourself.'
                        }
                      />
                    )}
                    {c.ambiguous && c.resolved && autoConnect && form.event.automatic && (
                      <Text type="secondary">
                        {Math.max(c.var1_matches.length, c.var2_matches.length)} variables contain the pattern;
                        the closest is proposed — untick the box above to choose another.
                      </Text>
                    )}

                    <Space wrap align="center">
                      <Space direction="vertical" size={2}>
                        <Text type="secondary" style={{ fontSize: 12 }}>{form.event.lib} (the event)</Text>
                        <Select
                          showSearch
                          style={{ width: 300 }}
                          value={wire.var1}
                          disabled={!editable}
                          placeholder="event variable"
                          onChange={v => setWire('var1', v)}
                          options={variableOptions(c.var1_matches, form.variables.event)}
                        />
                      </Space>
                      <Text type="secondary" style={{ paddingTop: 18 }}>↔</Text>
                      <Space direction="vertical" size={2}>
                        <Text type="secondary" style={{ fontSize: 12 }}>{form.connect_to} (the object)</Text>
                        <Select
                          showSearch
                          style={{ width: 300 }}
                          value={wire.var2}
                          disabled={!editable}
                          placeholder="object variable"
                          onChange={v => setWire('var2', v)}
                          options={variableOptions(c.var2_matches, form.variables.target)}
                        />
                      </Space>
                    </Space>
                  </Space>
                </Card>
              )
            })}

            <Space>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                disabled={!currentFile || !wiresComplete || !valuesComplete}
                onClick={addStagedEvent}
              >
                {currentFile ? <>Add to {currentFile}</> : 'Add to the file'}
              </Button>
              {!currentFile && <Text type="secondary">Choose an events file at the top of the page first.</Text>}
              {currentFile && !valuesComplete && <Text type="secondary">Every parameter needs a value.</Text>}
              {currentFile && valuesComplete && !wiresComplete && <Text type="secondary">Both sides of the connection must name a variable.</Text>}
            </Space>
          </Space>
        )}
      </Card>

      {/* ── The file itself: the list above is what it holds ── */}
      <Card
        title={currentFile
          ? <Space size={8}>
              <Text>Events in</Text>
              <Text code>{currentFile}</Text>
              {isNew && <Tag color="blue">not written yet</Tag>}
              {dirty && !isNew && <Tag color="orange">unsaved changes</Tag>}
              {needsDeclaring && <Tag color="orange">not in {jobsFile}</Tag>}
            </Space>
          : 'Events'}
        style={{ marginTop: 16 }}
        extra={currentFile && (
          <Space>
            {staged.length > 0 && (
              <Popconfirm title="Remove every event from this file?" okText="Remove all" cancelText="Cancel" onConfirm={clearStaged}>
                <Button size="small" danger type="text">Remove all</Button>
              </Popconfirm>
            )}
            {emptiedFileOnlyHeldEvents ? (
              <Popconfirm
                title={`Delete ${currentFile} and ${parFileFor(currentFile)}?`}
                description="Its events are all removed, so the file has nothing left to declare. It is also taken out of every job."
                okText="Delete the file"
                okButtonProps={{ danger: true }}
                cancelText="Cancel"
                onConfirm={() => undeclare(currentFile, true)}
              >
                <Button danger type="primary" icon={<DeleteOutlined />} loading={writing}>
                  Delete the file
                </Button>
              </Popconfirm>
            ) : (
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={writing}
                disabled={(staged.length === 0 && !emptied) || (!dirty && !isNew && !needsDeclaring)}
                onClick={saveFile}
              >
                {isNew ? 'Create the file' : (!dirty && needsDeclaring) ? `Declare in ${jobsFile}` : 'Save'}
              </Button>
            )}
          </Space>
        )}
      >
        {!currentFile ? (
          <Text type="secondary">Choose an events file at the top of the page, or start a new one.</Text>
        ) : staged.length === 0 ? (
          <Text type="secondary">
            {!emptied
              ? 'No event in this file yet — compose one above and add it.'
              : emptiedFileOnlyHeldEvents
                ? `Every event of ${currentFile} has been removed. Delete the file to drop it from the job, or add an event back.`
                : `Every event has been removed. ${currentFile} also declares ${openEntry?.other_models} model(s) of the network, so saving takes the events out and keeps the rest of the file.`}
          </Text>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Table
              size="small"
              pagination={false}
              dataSource={staged}
              rowKey="entry_id"
              columns={[
                {
                  title: 'Event', key: 'event',
                  render: (_: unknown, e: StagedEvent) => (
                    <Space direction="vertical" size={0}>
                      <Text>{e.label}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>{e.lib}</Text>
                    </Space>
                  ),
                },
                {
                  title: 'On', key: 'target', width: 200,
                  render: (_: unknown, e: StagedEvent) => (
                    <Space direction="vertical" size={0}>
                      <Text>{e.target_id}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {e.kind === 'network' ? 'network model' : 'dynamic model'}
                      </Text>
                    </Space>
                  ),
                },
                {
                  title: 'Parameters', key: 'params',
                  render: (_: unknown, e: StagedEvent) => (
                    <Tooltip title={
                      <Space direction="vertical" size={0}>
                        {e.parameters.map(p => <Text key={p.name} style={{ color: '#fff', fontSize: 12 }}>{p.name} = {p.value}</Text>)}
                        {e.connections.map((c, i) => <Text key={i} style={{ color: '#fff', fontSize: 12 }}>{c.var1} ↔ {c.id2} · {c.var2}</Text>)}
                      </Space>
                    }>
                      <Text type="secondary" style={{ fontSize: 12 }}>{parameterSummary(e)}</Text>
                    </Tooltip>
                  ),
                },
                {
                  title: 'Model id', key: 'model_id', width: 220,
                  render: (_: unknown, e: StagedEvent) => (
                    <Text code style={{ fontSize: 12 }}>{e.model_id}</Text>
                  ),
                },
                {
                  title: '', key: 'actions', width: 90,
                  render: (_: unknown, e: StagedEvent) => (
                    <Space size={0}>
                      <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEditor(e)} />
                      <Button size="small" type="text" danger icon={<DeleteOutlined />}
                              onClick={() => removeStagedEvent(e.entry_id)} />
                    </Space>
                  ),
                },
              ]}
              expandable={{
                expandedRowKeys: editing ? [editing] : [],
                showExpandColumn: false,
                expandedRowRender: (e: StagedEvent) => (
                  <StagedEditor
                    staged={e}
                    form={editForm}
                    values={editValues}
                    setValues={setEditValues}
                    wires={editWires}
                    setWires={setEditWires}
                    modelId={editModelId}
                    setModelId={setEditModelId}
                    saving={savingEdit}
                    onApply={() => applyEdit(e.entry_id)}
                    onCancel={() => setEditing(null)}
                  />
                ),
              }}
            />

            <Text type="secondary">
              Saving rewrites <Text code>{currentFile}</Text> and <Text code>{parFileFor(currentFile)}</Text> from this list
              {jobsFile ? <>, and declares them in {jobsFile} if they are not already.</> : <>. No job is selected, so nothing declares them yet.</>}
            </Text>
          </Space>
        )}
      </Card>

    </div>
  )
}
