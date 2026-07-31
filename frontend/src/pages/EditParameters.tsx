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
  Alert, Button, Card, Checkbox, Collapse, Input, Select,
  Space, Table, Tag, Tooltip, Typography,
} from 'antd'
import { RedoOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import client from '../api/client'

const { Title, Text } = Typography

interface ModelEntry {
  sid: string
  dyn_id: string
  lib: string
  parFile: string
  parId: string
}

interface Par { name: string; type: string; value: string; shared: boolean }
interface Ref { name: string; origData: string; origName: string; shared: boolean }
interface Change { name: string; old_value: string; new_value: string }
interface LogEntry { timestamp: string; dyn_id: string; par_file: string; set_id: string; changes: Change[] }

interface ModelDetail extends ModelEntry {
  pars: Par[]
  refs: Ref[]
  siblings: string[]
  macro_id: string | null
  macro_siblings: string[]
}

function valuesEqual(fileVal: string, widgetVal: string, type: string): boolean {
  try {
    if (type === 'BOOL') return fileVal.trim().toLowerCase() === widgetVal.trim().toLowerCase()
    if (type === 'INT') return parseInt(fileVal) === parseInt(widgetVal)
    return parseFloat(fileVal) === parseFloat(widgetVal)
  } catch {
    return fileVal === widgetVal
  }
}

export default function EditParameters() {
  const [models, setModels] = useState<ModelEntry[]>([])
  const [selectedSid, setSelectedSid] = useState<string | null>(null)
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
      if (res.data.length && !selectedSid) setSelectedSid(res.data[0].sid)
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

  const fetchDetail = async (sid: string) => {
    try {
      const res = await client.get<ModelDetail>(`/parameters/model/${encodeURIComponent(sid)}`)
      setDetail(res.data)
      const initial: Record<string, string> = {}
      for (const p of res.data.pars) initial[p.name] = p.value
      setFormVals(initial)
    } catch (err: any) {
      setDetail(null)
      setFormVals({})
      setError(err.response?.data?.detail ?? `Could not load parameters for ${sid}`)
    }
  }

  useEffect(() => { fetchModels(); fetchChangelog(); fetchModified() }, [])
  useEffect(() => { if (selectedSid) fetchDetail(selectedSid) }, [selectedSid])

  const changed = detail
    ? detail.pars.some(p => !valuesEqual(p.value, formVals[p.name] ?? p.value, p.type))
    : false

  const handleApply = async () => {
    if (!selectedSid) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await client.put<{ changed: number }>(
        `/parameters/model/${encodeURIComponent(selectedSid)}`, { values: formVals })
      await fetchDetail(selectedSid)
      await fetchChangelog()
      await fetchModified()
      setSuccess(`${res.data.changed} parameter(s) updated.`)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = async (parFile: string) => {
    await client.post(`/parameters/restore/${encodeURIComponent(parFile)}`)
    if (selectedSid) await fetchDetail(selectedSid)
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
    if (selectedSid) await fetchDetail(selectedSid)
    await fetchChangelog()
    await fetchModified()
    if (res.data.warned)
      setSuccess('Reverted — note: later entries modified the same parameters, the log may be inconsistent.')
    else
      setSuccess(`Reverted changes from ${entry.timestamp}.`)
  }

  const modelOptions = models.map(m => ({
    label: `${m.dyn_id}  (${m.lib})`,
    value: m.sid,
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
                value={selectedSid}
                onChange={setSelectedSid}
                style={{ width: '100%' }}
                showSearch
                optionFilterProp="label"
              />
              {detail && (
                <Space wrap>
                  <Tag color="blue">{detail.lib}</Tag>
                  <Text type="secondary">Par file: <Text code>{detail.parFile}</Text></Text>
                  <Text type="secondary">Set ID: <Text code>{detail.parId}</Text></Text>
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
                            style={{ width: '100%', borderColor: dirty ? '#faad14' : undefined }}
                            onChange={e => setFormVals(prev => ({ ...prev, [par.name]: e.target.value }))}
                          />
                        )
                      },
                    },
                  ]}
                />
              )}

              {detail.refs.length > 0 && (
                <>
                  <Title level={5} style={{ marginTop: 16 }}>IIDM references (read-only)</Title>
                  <Table
                    size="small"
                    pagination={false}
                    dataSource={detail.refs}
                    rowKey="name"
                    columns={[
                      { title: 'Name', dataIndex: 'name', render: (n: string) => <Text code>{n}</Text> },
                      { title: 'Source', key: 'src', render: (_: unknown, r: Ref) => <Text type="secondary">← {r.origName} ({r.origData})</Text> },
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
