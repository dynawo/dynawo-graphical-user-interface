//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useEffect, useState } from 'react'
import {
  Alert, AutoComplete, Button, Card, Checkbox, Collapse, Input, Popover, Select,
  Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import { LinkOutlined, RedoOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail } from '../api/errors'
import { valuesEqual } from './parValues'

const { Title, Text } = Typography

interface ModelEntry {
  dyn_id: string
  /** IIDM element this model is bound to — "" for models with no static reference. */
  static_id: string
  lib: string
  parFile: string
  parId: string
  /** A .dyd model, or the network set of a jobs file. */
  kind: 'model' | 'network'
}

interface Par { name: string; type: string; value: string; shared: boolean }
interface Ref { name: string; type: string; origData: string; origName: string; shared: boolean }
interface Change { name: string; old_value: string; new_value: string }
interface LogEntry { timestamp: string; dyn_id: string; par_file: string; set_id: string; changes: Change[] }

interface ModelDetail extends ModelEntry {
  pars: Par[]
  refs: Ref[]
  siblings: string[]
  macro_id: string | null
  macro_siblings: string[]
  /** IIDM data suggested for a parameter: from the session's .par files, else Dynawo's naming. */
  reference_suggestions: Record<string, string>
  /** IIDM data names known from the session's .par files and Dynawo's naming. */
  orig_names: string[]
}

/** Popover giving a parameter by an IIDM reference instead of a value. */
function ReferenceButton({ par, suggestion, origNames, onUse }: {
  par: Par; suggestion?: string; origNames: string[]; onUse: (origName: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [origName, setOrigName] = useState(suggestion ?? '')
  return (
    <Popover
      open={open}
      onOpenChange={v => { setOpen(v); if (v) setOrigName(suggestion ?? '') }}
      trigger="click"
      title={<>Read <Text code>{par.name}</Text> from the network</>}
      content={
        <Space direction="vertical" style={{ width: 260 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            IIDM data of the model's element (origName), read by Dynawo at initialisation.
          </Text>
          <AutoComplete
            value={origName}
            onChange={setOrigName}
            options={origNames.filter(o => o.toLowerCase().includes(origName.toLowerCase())).map(o => ({ value: o }))}
            placeholder="e.g. p_pu"
            style={{ width: '100%' }}
          />
          <Button type="primary" size="small" disabled={!origName.trim()}
                  onClick={() => { setOpen(false); onUse(origName.trim()) }}>
            Use reference
          </Button>
        </Space>
      }
    >
      <Tooltip title={suggestion ? `Read from the network (suggested: ${suggestion})` : 'Read from the network'}>
        <Button size="small" type="text" icon={<LinkOutlined />} />
      </Tooltip>
    </Popover>
  )
}

export default function EditParameters() {
  const [models, setModels] = useState<ModelEntry[]>([])
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ModelDetail | null>(null)
  const [formVals, setFormVals] = useState<Record<string, string>>({})
  const [changelog, setChangelog] = useState<LogEntry[]>([])
  const [modifiedParFiles, setModifiedParFiles] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const fetchModels = async () => {
    try {
      const res = await client.get<ModelEntry[]>('/parameters/models')
      setModels(res.data)
      if (res.data.length && !selectedModelId) setSelectedModelId(res.data[0].dyn_id)
    } catch {
      setModels([])
    }
  }

  const fetchChangelog = async () => {
    const res = await client.get<LogEntry[]>('/parameters/changelog')
    setChangelog(res.data)
  }

  const fetchModified = async () => {
    const res = await client.get<string[]>('/parameters/modified-files')
    setModifiedParFiles(res.data)
  }

  // `keep`: values typed but not applied yet, kept for the parameters still there.
  const fetchDetail = async (modelId: string, keep: Record<string, string> = {}) => {
    try {
      const res = await client.get<ModelDetail>(`/parameters/model/${encodeURIComponent(modelId)}`)
      setDetail(res.data)
      const initial: Record<string, string> = {}
      for (const p of res.data.pars) initial[p.name] = keep[p.name] ?? p.value
      setFormVals(initial)
    } catch (err: any) {
      setDetail(null)
      setFormVals({})
      setError(err.response?.data?.detail ?? `Could not load parameters for ${modelId}`)
    }
  }

  useEffect(() => { fetchModels(); fetchChangelog(); fetchModified() }, [])
  useEffect(() => { if (selectedModelId) fetchDetail(selectedModelId) }, [selectedModelId])

  const changed = detail
    ? detail.pars.some(p => !valuesEqual(p.value, formVals[p.name] ?? p.value, p.type))
    : false

  const handleApply = async () => {
    if (!selectedModelId) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await client.put<{ changed: number }>(
        `/parameters/model/${encodeURIComponent(selectedModelId)}`, { values: formVals })
      await fetchDetail(selectedModelId)
      await fetchChangelog()
      await fetchModified()
      setSuccess(`${res.data.changed} parameter(s) updated.`)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleReferences = async (toReference: Record<string, string>, toValue: string[] = []) => {
    if (!selectedModelId || !detail) return
    setError(null)
    setSuccess(null)
    // Unsaved values of the other parameters are kept.
    const pending = Object.fromEntries(detail.pars
      .filter(p => !(p.name in toReference) && !valuesEqual(p.value, formVals[p.name] ?? p.value, p.type))
      .map(p => [p.name, formVals[p.name]]))
    try {
      await client.put(`/parameters/model/${encodeURIComponent(selectedModelId)}/references`,
                       { to_reference: toReference, to_value: toValue })
      await fetchDetail(selectedModelId, pending)
      await fetchModified()
      const n = Object.keys(toReference).length
      setSuccess(n ? `${n} parameter(s) now read from the network.` : `${toValue.length} reference(s) turned into value(s) to fill.`)
    } catch (err) {
      setError(errorDetail(err, 'Could not change the references'))
    }
  }

  // Parameters without value that a suggested reference could give.
  const suggested = detail
    ? Object.fromEntries(detail.pars
        .filter(p => !p.shared && !p.value && detail.kind === 'model' && detail.reference_suggestions[p.name])
        .map(p => [p.name, detail.reference_suggestions[p.name]]))
    : {}

  const handleRestore = async (parFile: string) => {
    await client.post(`/parameters/restore/${encodeURIComponent(parFile)}`)
    if (selectedModelId) await fetchDetail(selectedModelId)
    await fetchChangelog()
    await fetchModified()
    setSuccess(`${parFile} restored to original.`)
  }

  const handleClearLog = async (dyn_id: string) => {
    await client.delete(`/parameters/changelog/${encodeURIComponent(dyn_id)}`)
    await fetchChangelog()
  }

  const handleRevert = async (entry: LogEntry) => {
    const res = await client.post<{ ok: boolean; warned: boolean }>(
      `/parameters/changelog/${encodeURIComponent(entry.dyn_id)}/revert/${encodeURIComponent(entry.timestamp)}`
    )
    if (selectedModelId) await fetchDetail(selectedModelId)
    await fetchChangelog()
    await fetchModified()
    if (res.data.warned)
      setSuccess('Reverted — note: later entries modified the same parameters, the log may be inconsistent.')
    else
      setSuccess(`Reverted changes from ${entry.timestamp}.`)
  }

  const modelOptions = models.map(m => ({
    // Static id only shown when it adds something — it is usually identical to the
    // model id, and absent altogether for models with no network reference.
    label: m.static_id && m.static_id !== m.dyn_id
      ? `${m.dyn_id}  (${m.lib})  ·  ${m.static_id}`
      : `${m.dyn_id}  (${m.lib})`,
    value: m.dyn_id,
  }))

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Edit Parameters</Title>

      {models.length === 0 && (
        <Alert type="warning" description="No dynamic models found. Upload a .dyd file first." />
      )}

      {models.length > 0 && (
        <>
          {/* ── Restore & changelog ── */}
          {modifiedParFiles.map(pf => (
            <Alert
              key={pf}
              type="warning"
              description={
                <Space>
                  <Text>{pf} has been modified.</Text>
                  <Button size="small" icon={<RedoOutlined />} onClick={() => handleRestore(pf)}>
                    Restore original
                  </Button>
                </Space>
              }
              style={{ marginBottom: 8 }}
            />
          ))}

          {changelog.length > 0 && (
            <Collapse style={{ marginBottom: 16 }} items={
              [...new Set(changelog.map(e => e.dyn_id))].map(dyn_id => {
                const entries = changelog.filter(e => e.dyn_id === dyn_id)
                return {
                  key: dyn_id,
                  label: `Change log — ${dyn_id} (${entries.length} operation(s))`,
                  extra: <Button size="small" onClick={e => { e.stopPropagation(); handleClearLog(dyn_id) }}>Clear</Button>,
                  children: entries.slice().reverse().map((entry, i) => (
                    <div key={i} style={{ marginBottom: 12 }}>
                      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {entry.timestamp} · set `{entry.set_id}` · {entry.par_file}
                        </Text>
                        <Button
                          size="small"
                          icon={<RollbackOutlined />}
                          onClick={() => handleRevert(entry)}
                        >
                          Revert
                        </Button>
                      </Space>
                      <Table
                        size="small"
                        pagination={false}
                        dataSource={entry.changes}
                        rowKey="name"
                        columns={[
                          { title: 'Parameter', dataIndex: 'name' },
                          { title: 'Old value', dataIndex: 'old_value' },
                          { title: 'New value', dataIndex: 'new_value' },
                        ]}
                        style={{ marginTop: 4 }}
                      />
                    </div>
                  )),
                }
              })
            } />
          )}

          {/* ── Model selector ── */}
          <Card style={{ marginBottom: 16 }}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text strong>Select dynamic model</Text>
              <Select
                options={modelOptions}
                value={selectedModelId}
                onChange={setSelectedModelId}
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
              />
              {detail && (
                <Space wrap>
                  <Tag color="blue">{detail.lib}</Tag>
                  {detail.static_id
                    ? <Text type="secondary">Static ID: <Text code>{detail.static_id}</Text></Text>
                    : <Tooltip title="This model has no staticId, so it does not appear on the network diagram — its parameters are edited from here.">
                        <Tag>no network reference</Tag>
                      </Tooltip>}
                  {detail.parFile
                    ? <>
                        <Text type="secondary">Par file: <Text code>{detail.parFile}</Text></Text>
                        <Text type="secondary">Set ID: <Text code>{detail.parId}</Text></Text>
                      </>
                    : <Tag>no parameter set</Tag>}
                  {detail.siblings.length > 0 && (
                    <Text type="warning">Shared set — also affects: {detail.siblings.join(', ')}</Text>
                  )}
                  {detail.macro_siblings.length > 0 && (
                    <Text type="warning">
                      Shared macro <Text code>{detail.macro_id}</Text> — also affects: {detail.macro_siblings.join(', ')}
                    </Text>
                  )}
                </Space>
              )}
            </Space>
          </Card>

          {/* ── Parameter editor ── */}
          {detail && (
            <Card
              title="Parameters"
              extra={
                <Space>
                  {success && <Text type="success">{success}</Text>}
                  {error && <Text type="danger">{error}</Text>}
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    disabled={!changed}
                    loading={saving}
                    onClick={handleApply}
                  >
                    Apply changes
                  </Button>
                </Space>
              }
            >
              {detail.pars.length === 0 && detail.refs.length === 0 && (
                <Text type="secondary">
                  This model references no parameter set — nothing to edit here.
                </Text>
              )}

              {Object.keys(suggested).length > 0 && (
                <Alert
                  type="info" showIcon style={{ marginBottom: 12 }}
                  description={<>
                    {Object.keys(suggested).length} parameter(s) without value could be read from the network:{' '}
                    {Object.entries(suggested).map(([n, o]) => `${n} ← ${o}`).join(', ')}.
                  </>}
                  action={<Button size="small" icon={<LinkOutlined />} onClick={() => handleReferences(suggested)}>
                    Use these references
                  </Button>}
                />
              )}

              {detail.pars.length > 0 && (
                <Table
                  size="small"
                  pagination={false}
                  dataSource={detail.pars}
                  rowKey="name"
                  columns={[
                    {
                      title: 'Name', dataIndex: 'name', key: 'name',
                      render: (name: string, par: Par) => (
                        <Space size={4}>
                          <Text code>{name}</Text>
                          {par.shared && (
                            <Tooltip
                              title={
                                detail.macro_siblings.length > 0
                                  ? `Shared via macro ${detail.macro_id} — editing this also affects: ${detail.macro_siblings.join(', ')}`
                                  : `Shared via macro ${detail.macro_id}`
                              }
                            >
                              <Tag color="orange" style={{ marginInlineEnd: 0 }}>shared</Tag>
                            </Tooltip>
                          )}
                        </Space>
                      ),
                    },
                    {
                      title: 'Type', dataIndex: 'type', key: 'type', width: 80,
                      render: (t: string) => <Tag>{t}</Tag>,
                    },
                    {
                      title: 'Value', key: 'value', width: 200,
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
                        return (
                          <Input
                            value={val}
                            placeholder={par.value ? undefined : 'to fill'}
                            status={!val ? 'warning' : undefined}
                            style={{ width: '100%', borderColor: dirty ? '#faad14' : undefined }}
                            onChange={e => setFormVals(prev => ({ ...prev, [par.name]: e.target.value }))}
                          />
                        )
                      },
                    },
                    {
                      key: 'reference', width: 48,
                      render: (_: unknown, par: Par) => par.shared || detail.kind !== 'model' ? null : (
                        <ReferenceButton
                          par={par}
                          suggestion={detail.reference_suggestions[par.name]}
                          origNames={detail.orig_names}
                          onUse={origName => handleReferences({ [par.name]: origName })}
                        />
                      ),
                    },
                  ]}
                />
              )}

              {detail.refs.length > 0 && (
                <>
                  <Title level={5} style={{ marginTop: 16 }}>IIDM references</Title>
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={detail.refs}
                    rowKey="name"
                    columns={[
                      { title: 'Name', dataIndex: 'name', render: (n: string) => <Text code>{n}</Text> },
                      { title: 'Source', key: 'src', render: (_: unknown, r: Ref) => <Text type="secondary">← {r.origName} ({r.origData})</Text> },
                      {
                        key: 'value', width: 140,
                        render: (_: unknown, r: Ref) => r.shared ? <Tag color="orange">shared</Tag> : (
                          <Button size="small" type="link" onClick={() => handleReferences({}, [r.name])}>
                            Give a value instead
                          </Button>
                        ),
                      },
                    ]}
                  />
                </>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  )
}
