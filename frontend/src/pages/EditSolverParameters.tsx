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
import { useSearchParams } from 'react-router-dom'
import {
  Alert, Button, Card, Checkbox, Collapse, Input, Select,
  Space, Table, Tag, Typography,
} from 'antd'
import { RedoOutlined, RollbackOutlined, SaveOutlined } from '@ant-design/icons'
import client from '../api/client'
import { valuesEqual } from './parValues'

const { Title, Text } = Typography

interface SolverInfo { jobs_file: string; lib: string; parFile: string; parId: string }
interface Par { name: string; type: string; value: string }
interface Ref { name: string; origData: string; origName: string }
interface Change { name: string; old_value: string; new_value: string }
interface LogEntry { timestamp: string; par_file: string; set_id: string; changes: Change[] }

interface SolverDetail extends SolverInfo {
  pars: Par[]
  refs: Ref[]
}

export default function EditSolverParameters() {
  // The job whose solver is edited: from the link (?jobs=), else the first one.
  const [searchParams] = useSearchParams()
  const [jobs, setJobs] = useState<string[]>([])
  const [jobsFile, setJobsFile] = useState<string | null>(searchParams.get('jobs'))
  const params = jobsFile ? { jobs_file: jobsFile } : undefined
  const [detail, setDetail] = useState<SolverDetail | null>(null)
  const [formVals, setFormVals] = useState<Record<string, string>>({})
  const [changelog, setChangelog] = useState<LogEntry[]>([])
  const [isModified, setIsModified] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [notFound, setNotFound] = useState<string | null>(null)

  const fetchDetail = async () => {
    try {
      setNotFound(null)
      const res = await client.get<SolverDetail>('/solver/parameters', { params })
      setDetail(res.data)
      const initial: Record<string, string> = {}
      for (const p of res.data.pars) initial[p.name] = p.value
      setFormVals(initial)
    } catch (err: any) {
      setNotFound(err.response?.data?.detail ?? 'Could not load solver parameters')
    }
  }

  const fetchChangelog = async () => {
    try {
      const res = await client.get<LogEntry[]>('/solver/changelog')
      setChangelog(res.data)
    } catch {
      setChangelog([])
    }
  }

  const fetchModified = async () => {
    try {
      const res = await client.get<{ modified: boolean }>('/solver/is-modified', { params })
      setIsModified(res.data.modified)
    } catch {
      setIsModified(false)
    }
  }

  useEffect(() => {
    client.get<string[]>('/solver/jobs').then(res => {
      setJobs(res.data)
      if (!jobsFile || !res.data.includes(jobsFile)) setJobsFile(res.data[0] ?? null)
      if (!res.data.length) setNotFound('No .jobs file with a solver parameter set in the session')
    }).catch(() => setJobs([]))
    fetchChangelog()
  }, [])
  useEffect(() => { if (jobsFile) { fetchDetail(); fetchModified() } }, [jobsFile])

  const changed = detail
    ? detail.pars.some(p => !valuesEqual(p.value, formVals[p.name] ?? p.value, p.type))
    : false

  const handleApply = async () => {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await client.put<{ changed: number }>('/solver/parameters', { values: formVals }, { params })
      await fetchDetail()
      await fetchChangelog()
      await fetchModified()
      setSuccess(`${res.data.changed} parameter(s) updated.`)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  const handleRestore = async () => {
    await client.post('/solver/restore', null, { params })
    await fetchDetail()
    await fetchChangelog()
    await fetchModified()
    setSuccess('Solver par file restored to original.')
  }

  const handleClearLog = async () => {
    await client.delete('/solver/changelog')
    await fetchChangelog()
  }

  const handleRevert = async (entry: LogEntry) => {
    const res = await client.post<{ ok: boolean; warned: boolean }>(
      `/solver/changelog/revert/${encodeURIComponent(entry.timestamp)}`
    )
    await fetchDetail()
    await fetchChangelog()
    await fetchModified()
    if (res.data.warned)
      setSuccess('Reverted — note: later entries modified the same parameters, the log may be inconsistent.')
    else
      setSuccess(`Reverted changes from ${entry.timestamp}.`)
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Edit Solver Parameters</Title>

      {notFound && (
        <Alert type="warning" description={notFound} />
      )}

      {detail && (
        <>
          {/* ── Info card ── */}
          <Card style={{ marginBottom: 16 }}>
            {jobs.length > 1 && (
              <Space style={{ marginBottom: 8 }}>
                <Text strong>Job</Text>
                <Select value={jobsFile} onChange={setJobsFile} style={{ minWidth: 320 }}
                        options={jobs.map(j => ({ value: j, label: j }))} />
              </Space>
            )}
            <Space wrap>
              <Tag color="purple">{detail.lib}</Tag>
              <Text type="secondary">Par file: <Text code>{detail.parFile}</Text></Text>
              <Text type="secondary">Set ID: <Text code>{detail.parId}</Text></Text>
            </Space>
          </Card>

          {/* ── Restore alert ── */}
          {isModified && (
            <Alert
              type="warning"
              description={
                <Space>
                  <Text>{detail.parFile} has been modified.</Text>
                  <Button size="small" icon={<RedoOutlined />} onClick={handleRestore}>
                    Restore original
                  </Button>
                </Space>
              }
              style={{ marginBottom: 8 }}
            />
          )}

          {/* ── Change log ── */}
          {changelog.length > 0 && (
            <Collapse style={{ marginBottom: 16 }} items={[{
              key: 'log',
              label: `Change log (${changelog.length} operation(s))`,
              extra: <Button size="small" onClick={e => { e.stopPropagation(); handleClearLog() }}>Clear</Button>,
              children: changelog.slice().reverse().map((entry, i) => (
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
            }]} />
          )}

          {/* ── Parameter editor ── */}
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
                    render: (name: string) => <Text code>{name}</Text>,
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

            {detail.pars.length === 0 && detail.refs.length === 0 && (
              <Text type="secondary">No parameters found for set ID `{detail.parId}`.</Text>
            )}
          </Card>
        </>
      )}
    </div>
  )
}
