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
import {
  Alert, Button, Card, Input, Progress, Select, Space,
  Table, Tag, Typography,
} from 'antd'
import {
  CheckCircleOutlined, DeleteOutlined, DownloadOutlined, FolderOpenOutlined,
  SyncOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import client from '../api/client'
import DirectoryPicker from './DirectoryPicker'

const { Title, Text } = Typography

interface DownloadedVersion {
  os_key: string
  version: string
  exe: string | null
}

interface LocalExecutable {
  exe: string
}

interface ProgressEvent {
  fraction: number
  text: string
  done: boolean
  error?: string
}

export default function DynawoVersion() {
  const [versions, setVersions] = useState<Record<string, Record<string, { url: string }>>>({})
  const [downloaded, setDownloaded] = useState<DownloadedVersion[]>([])
  const [localExes, setLocalExes] = useState<LocalExecutable[]>([])
  const [exe, setExe] = useState('')
  const [exeInput, setExeInput] = useState('')
  const [exeError, setExeError] = useState<string | null>(null)
  const [showExePicker, setShowExePicker] = useState(false)

  const [selectedOs, setSelectedOs] = useState('')
  const [selectedVersion, setSelectedVersion] = useState('')

  const [progress, setProgress] = useState<ProgressEvent | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [updatingVersion, setUpdatingVersion] = useState<{ os_key: string; version: string } | null>(null)
  const sseRef = useRef<EventSource | null>(null)

  const fetchAll = async () => {
    const [vRes, dRes, lRes, eRes] = await Promise.all([
      client.get('/dynawo/versions'),
      client.get<DownloadedVersion[]>('/dynawo/downloaded'),
      client.get<LocalExecutable[]>('/dynawo/local'),
      client.get<{ exe: string }>('/dynawo/executable'),
    ])
    setVersions(vRes.data)
    setDownloaded(dRes.data)
    setLocalExes(lRes.data)
    setExe(eRes.data.exe)
    setExeInput(eRes.data.exe)
    if (!selectedOs && Object.keys(vRes.data).length) {
      const firstOs = Object.keys(vRes.data)[0]
      setSelectedOs(firstOs)
      setSelectedVersion(Object.keys(vRes.data[firstOs])[0] ?? '')
    }
  }

  useEffect(() => { fetchAll() }, [])

  const handleSetExe = async (value: string = exeInput) => {
    setExeError(null)
    try {
      await client.post<{ exe: string }>('/dynawo/executable', { exe: value })
      setExeInput(value)
      await fetchAll()
    } catch (err: any) {
      setExeError(err.response?.data?.detail ?? 'Error setting executable')
    }
  }

  const handleUse = async (os_key: string, version: string) => {
    const res = await client.post<{ exe: string }>(`/dynawo/use/${os_key}/${version}`)
    setExe(res.data.exe)
    setExeInput(res.data.exe)
  }

  const handleRemove = async (os_key: string, version: string) => {
    await client.delete(`/dynawo/remove/${os_key}/${version}`)
    await fetchAll()
  }

  const handleUseLocal = async (exe: string) => {
    setExeError(null)
    try {
      const res = await client.post<{ exe: string }>('/dynawo/executable', { exe })
      setExe(res.data.exe)
      setExeInput(res.data.exe)
    } catch (err: any) {
      setExeError(err.response?.data?.detail ?? 'Error setting executable')
    }
  }

  const handleRemoveLocal = async (exe: string) => {
    await client.delete('/dynawo/local', { data: { exe } })
    await fetchAll()
  }

  const startSse = (onDone: (err?: string) => void) => {
    setDownloading(true)
    const es = new EventSource('/api/dynawo/download/progress', { withCredentials: true })
    sseRef.current = es
    es.onmessage = async (e) => {
      const data: ProgressEvent = JSON.parse(e.data)
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

  const handleUpdate = async (os_key: string, version: string) => {
    setDownloadError(null)
    setProgress(null)
    setUpdatingVersion({ os_key, version })
    try {
      await client.delete(`/dynawo/remove/${os_key}/${version}`)
      await client.post('/dynawo/download', { os_key, version })
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
      await client.post('/dynawo/download', { os_key: selectedOs, version: selectedVersion })
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
    { title: 'OS', dataIndex: 'os_key', key: 'os_key', width: 80 },
    { title: 'Version', dataIndex: 'version', key: 'version', width: 120 },
    {
      title: 'Status', key: 'status', width: 160,
      render: (_: unknown, d: DownloadedVersion) => {
        const isUpdating = updatingVersion?.os_key === d.os_key && updatingVersion?.version === d.version
        if (isUpdating) return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2, width: '100%' }}>
            <Tag icon={<SyncOutlined spin />} color="processing">Updating…</Tag>
            {progress && (
              <Progress
                size="small"
                percent={Math.round(progress.fraction * 100)}
                status="active"
                format={() => progress.text}
              />
            )}
          </div>
        )
        return exe && d.exe && exe === d.exe
          ? <Tag icon={<CheckCircleOutlined />} color="success">In use</Tag>
          : <Tag color="default">Available</Tag>
      },
    },
    {
      title: '', key: 'actions',
      render: (_: unknown, d: DownloadedVersion) => {
        const isUpdating = updatingVersion?.os_key === d.os_key && updatingVersion?.version === d.version
        return (
          <Space>
            {d.exe && (!exe || exe !== d.exe) && (
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

  const localColumns = [
    {
      title: 'Path', dataIndex: 'exe', key: 'exe',
      render: (e: string) => <Text code>{e}</Text>,
    },
    {
      title: 'Status', key: 'status', width: 120,
      render: (_: unknown, d: LocalExecutable) => (
        exe && exe === d.exe
          ? <Tag icon={<CheckCircleOutlined />} color="success">In use</Tag>
          : <Tag color="default">Available</Tag>
      ),
    },
    {
      title: '', key: 'actions', width: 160,
      render: (_: unknown, d: LocalExecutable) => (
        <Space>
          {(!exe || exe !== d.exe) && (
            <Button size="small" icon={<ThunderboltOutlined />}
              onClick={() => handleUseLocal(d.exe)}>
              Use
            </Button>
          )}
          <Button size="small" danger icon={<DeleteOutlined />}
            onClick={() => handleRemoveLocal(d.exe)}>
            Remove
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 800 }}>
      <Title level={3}>Dynawo Version</Title>

      {/* ── Executable path ── */}
      <Card title="Dynawo Executable" style={{ marginBottom: 24 }}>
        {exe && (
          <Alert
            type="success"
            description={<Text code>{exe}</Text>}
            style={{ marginBottom: 12 }}
          />
        )}
        <Space.Compact style={{ width: '100%' }}>
          <Input
            value={exeInput}
            onChange={e => { setExeInput(e.target.value); setShowExePicker(false) }}
            placeholder="/path/to/dynawo.sh"
            onPressEnter={() => handleSetExe()}
          />
          <Button icon={<FolderOpenOutlined />} onClick={() => setShowExePicker(v => !v)}>Browse</Button>
          <Button type="primary" onClick={() => handleSetExe()}>Set</Button>
          {exe && <Button onClick={() => handleSetExe('')}>Clear</Button>}
        </Space.Compact>
        {showExePicker && (
          <div style={{ marginTop: 8 }}>
            <DirectoryPicker
              initialPath={exeInput || undefined}
              fileFilter={name => name.endsWith('.sh')}
              onSelectFile={path => { setExeInput(path); setShowExePicker(false) }}
              onCancel={() => setShowExePicker(false)}
            />
          </div>
        )}
        {exeError && <Alert type="error" description={exeError} style={{ marginTop: 8 }} />}
      </Card>

      {/* ── Saved local executables ── */}
      {localExes.length > 0 && (
        <Card title="Saved local executables" style={{ marginBottom: 24 }}>
          <Table
            dataSource={localExes}
            columns={localColumns}
            rowKey={d => d.exe}
            size="small"
            pagination={false}
          />
        </Card>
      )}

      {/* ── Downloaded versions ── */}
      {downloaded.length > 0 && (
        <Card title="Downloaded versions" style={{ marginBottom: 24 }}>
          <Table
            dataSource={downloaded}
            columns={downloadedColumns}
            rowKey={d => `${d.os_key}-${d.version}`}
            size="small"
            pagination={false}
          />
        </Card>
      )}

      {/* ── Download new version ── */}
      <Card title="Download a version">
        <Space style={{ marginBottom: 12 }}>
          <Select
            options={osOptions}
            value={selectedOs}
            onChange={v => {
              setSelectedOs(v)
              setSelectedVersion(Object.keys(versions[v] ?? {})[0] ?? '')
            }}
            style={{ width: 120 }}
          />
          <Select
            options={versionOptions}
            value={selectedVersion}
            onChange={setSelectedVersion}
            style={{ width: 160 }}
          />
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            onClick={handleDownload}
            disabled={downloading || alreadyDownloaded || !selectedUrl}
            loading={downloading}
          >
            {alreadyDownloaded ? 'Already downloaded' : 'Download'}
          </Button>
        </Space>

        {selectedUrl && (
          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 12 }}>
            URL: {selectedUrl}
          </Text>
        )}

        {downloading && progress && (
          <Progress
            percent={Math.round(progress.fraction * 100)}
            status="active"
            format={() => progress.text}
          />
        )}
        {downloadError && <Alert type="error" description={downloadError} style={{ marginTop: 8 }} />}
      </Card>
    </div>
  )
}
