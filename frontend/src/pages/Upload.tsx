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
import { Alert, Button, Card, Space, Statistic, Table, Tag, Typography, Upload as AntUpload } from 'antd'
import {
  DeleteOutlined, FileZipOutlined, FolderOpenOutlined, FolderOutlined, InboxOutlined, ReloadOutlined,
} from '@ant-design/icons'
import client from '../api/client'
import AutoloadFilesystemModal from './AutoloadFilesystemModal'
import AutoloadZipModal from './AutoloadZipModal'
import { invalidateEditCurvesCache } from './EditCurves'

const { Dragger } = AntUpload
const { Title } = Typography

interface FileEntry {
  name: string
  size: number
  ftype: string | null
}

interface NetworkSummary {
  filename: string
  summary: Record<string, number>
}

const FTYPE_COLOR: Record<string, string> = {
  iidm: 'blue', dyd: 'green', par: 'orange', jobs: 'purple', crv: 'cyan',
}

interface UploadItem {
  file: File
  // Path relative to the drop root, e.g. "CombiTables/subdir/file.txt" —
  // preserves the folder structure the user dropped, instead of flattening it.
  path: string
}

interface TreeRow {
  key: string
  title: string
  isFolder: boolean
  size: number
  ftype: string | null
  children?: TreeRow[]
}

function collectFileKeys(node: TreeRow): string[] {
  if (!node.isFolder) return [node.key]
  return (node.children ?? []).flatMap(collectFileKeys)
}

function buildFileTree(files: FileEntry[]): TreeRow[] {
  const root: TreeRow[] = []
  const folders = new Map<string, TreeRow>()

  for (const f of files) {
    const parts = f.name.split('/')
    let siblings = root
    let pathSoFar = ''
    for (let i = 0; i < parts.length - 1; i++) {
      pathSoFar = pathSoFar ? `${pathSoFar}/${parts[i]}` : parts[i]
      let folder = folders.get(pathSoFar)
      if (!folder) {
        folder = { key: pathSoFar, title: parts[i], isFolder: true, size: 0, ftype: null, children: [] }
        folders.set(pathSoFar, folder)
        siblings.push(folder)
      }
      folder.size += f.size
      siblings = folder.children!
    }
    siblings.push({ key: f.name, title: parts[parts.length - 1], isFolder: false, size: f.size, ftype: f.ftype })
  }

  const sortRows = (rows: TreeRow[]) => {
    rows.sort((a, b) => (a.isFolder === b.isFolder ? a.title.localeCompare(b.title) : a.isFolder ? -1 : 1))
    for (const row of rows) if (row.children) sortRows(row.children)
  }
  sortRows(root)
  return root
}

async function readEntryFiles(entry: FileSystemEntry): Promise<UploadItem[]> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject))
    return [{ file, path: entry.fullPath.replace(/^\//, '') }]
  }
  if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    const children: FileSystemEntry[] = []
    // readEntries only returns a batch at a time; keep calling until it's empty.
    let batch: FileSystemEntry[]
    do {
      batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
      children.push(...batch)
    } while (batch.length > 0)
    const nested = await Promise.all(children.map(readEntryFiles))
    return nested.flat()
  }
  return []
}

export default function Upload() {
  const [files, setFiles] = useState<FileEntry[]>([])
  const [network, setNetwork] = useState<NetworkSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [networkLoading, setNetworkLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [replaceTarget, setReplaceTarget] = useState<string | null>(null)
  const replaceInputRef = useRef<HTMLInputElement>(null)
  const [fsModalOpen, setFsModalOpen] = useState(false)
  const [zipModalOpen, setZipModalOpen] = useState(false)

  const fetchFiles = async () => {
    const res = await client.get<FileEntry[]>('/files/')
    setFiles(res.data)
  }

  const fetchNetwork = async () => {
    try {
      const res = await client.get<NetworkSummary>('/network/summary')
      setNetwork(res.data)
    } catch {
      setNetwork(null)
    }
  }

  useEffect(() => { fetchFiles(); fetchNetwork() }, [])

  const uploadFileList = async (items: UploadItem[]) => {
    if (!items.length) return
    setLoading(true)
    setError(null)
    const form = new FormData()
    for (const { file, path } of items) form.append('files', file, path)
    try {
      await client.post('/files/upload', form)
      await fetchFiles()
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Upload failed')
    } finally {
      setLoading(false)
    }
  }

  const handleFolderDrop = (e: React.DragEvent<HTMLDivElement>) => {
    const items = e.dataTransfer?.items
    if (!items || items.length === 0) return
    const entries = Array.from(items)
      .map(it => it.webkitGetAsEntry?.())
      .filter((it): it is FileSystemEntry => !!it)
    // No FileSystemEntry support (old browser): fall back to the Dragger's own handling.
    if (entries.length === 0) return
    e.preventDefault()
    e.stopPropagation()
    Promise.all(entries.map(readEntryFiles)).then(nested => {
      const allItems = nested.flat()
      const seen = new Map<string, UploadItem>()
      const duplicates = new Set<string>()
      for (const item of allItems) {
        if (seen.has(item.path)) duplicates.add(item.path)
        else seen.set(item.path, item)
      }
      if (duplicates.size) {
        setError(`Duplicate paths were found in the dropped folder; only the first of each was kept: ${[...duplicates].join(', ')}`)
      }
      uploadFileList([...seen.values()])
    })
  }

  const handleUnload = async (name: string) => {
    await client.delete(`/files/${encodeURIComponent(name)}`)
    if (network?.filename === name) setNetwork(null)
    if (files.find(f => f.name === name)?.ftype === 'crv') invalidateEditCurvesCache()
    await fetchFiles()
  }

  const handleUnloadFolder = async (node: TreeRow) => {
    const names = collectFileKeys(node)
    await Promise.all(names.map(name => client.delete(`/files/${encodeURIComponent(name)}`)))
    if (network && names.includes(network.filename)) setNetwork(null)
    if (files.some(f => names.includes(f.name) && f.ftype === 'crv')) invalidateEditCurvesCache()
    await fetchFiles()
  }

  const handleReplace = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!replaceTarget || !e.target.files?.[0]) return
    const form = new FormData()
    form.append('file', e.target.files[0])
    await client.put(`/files/${encodeURIComponent(replaceTarget)}`, form)
    if (network?.filename === replaceTarget) setNetwork(null)
    setReplaceTarget(null)
    await fetchFiles()
    e.target.value = ''
  }

  const handleLoadNetwork = async (filename: string) => {
    setNetworkLoading(true)
    setError(null)
    try {
      await client.post(`/network/load?filename=${encodeURIComponent(filename)}`)
      await fetchNetwork()
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Failed to load network')
    } finally {
      setNetworkLoading(false)
    }
  }

  const jobsFile = files.find(f => f.ftype === 'jobs')
  const iidmFiles = files.filter(f => f.ftype === 'iidm')
  const fileTree = buildFileTree(files)

  const columns = [
    {
      title: 'Name', dataIndex: 'title', key: 'title',
      render: (title: string, record: TreeRow) => (
        <Typography.Text strong={!record.isFolder}>
          {record.isFolder ? <FolderOutlined style={{ marginRight: 6 }} /> : null}
          {title}
        </Typography.Text>
      ),
    },
    {
      title: 'Type', dataIndex: 'ftype', key: 'ftype',
      render: (ftype: string | null, record: TreeRow) => {
        if (record.isFolder) return null
        return ftype
          ? <Tag color={FTYPE_COLOR[ftype] ?? 'default'}>{ftype.toUpperCase()}</Tag>
          : <Tag>unknown</Tag>
      },
      width: 90,
    },
    {
      title: 'Size', dataIndex: 'size', key: 'size',
      render: (size: number) => `${(size / 1024).toFixed(1)} kB`,
      width: 90,
    },
    {
      title: '', key: 'actions', width: 160,
      render: (_: unknown, record: TreeRow) => record.isFolder ? (
        <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleUnloadFolder(record)}>
          Unload folder
        </Button>
      ) : (
        <span style={{ display: 'flex', gap: 8 }}>
          <Button size="small" icon={<ReloadOutlined />} onClick={() => {
            setReplaceTarget(record.key)
            replaceInputRef.current?.click()
          }}>Replace</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleUnload(record.key)}>
            Unload
          </Button>
        </span>
      ),
    },
  ]

  return (
    <div style={{ maxWidth: 900 }}>
      <Title level={3}>Upload Dynawo Files</Title>

      {error && (
        <Alert
          type="error"
          description={error}
          action={<Button size="small" onClick={() => setError(null)}>Dismiss</Button>}
          style={{ marginBottom: 16 }}
        />
      )}

      <div onDropCapture={handleFolderDrop}>
        <Dragger
          multiple
          showUploadList={false}
          beforeUpload={(_, fileList) => {
            uploadFileList(fileList.map(f => {
              const file = f as unknown as File
              return { file, path: file.name }
            }))
            return false
          }}
          style={{ marginBottom: 24 }}
          disabled={loading}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">Click or drag Dynawo files or a folder here to upload</p>
          <p className="ant-upload-hint">.jobs, .dyd, .par, .iidm, .crv — multiple files or a whole folder supported</p>
        </Dragger>
      </div>

      <input ref={replaceInputRef} type="file" style={{ display: 'none' }} onChange={handleReplace} />

      {files.length > 0 && (
        <Card title="Uploaded files" style={{ marginBottom: 24 }}>
          <Table
            dataSource={fileTree}
            columns={columns}
            rowKey="key"
            size="small"
            pagination={false}
          />
        </Card>
      )}

      {jobsFile && (
        <Card title="Auto-load referenced files" style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Typography.Text type="secondary">
              Let the app locate, copy, and wire up all files referenced in <Typography.Text code>{jobsFile.name}</Typography.Text>.
            </Typography.Text>
            <Space wrap>
              <Button icon={<FolderOpenOutlined />} onClick={() => setFsModalOpen(true)}>
                From filesystem
              </Button>
              <Button icon={<FileZipOutlined />} onClick={() => setZipModalOpen(true)}>
                From ZIP archive
              </Button>
            </Space>
          </div>
        </Card>
      )}

      {iidmFiles.length > 0 && (
        <Card title="Network">
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            {iidmFiles.map(f => (
              <Button
                key={f.name}
                type={network?.filename === f.name ? 'primary' : 'default'}
                loading={networkLoading}
                onClick={() => handleLoadNetwork(f.name)}
                disabled={network?.filename === f.name}
              >
                {network?.filename === f.name ? `✓ ${f.name}` : `Load ${f.name}`}
              </Button>
            ))}
          </div>
          {network && (
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
              {Object.entries(network.summary).map(([label, value]) => (
                <Statistic key={label} title={label} value={value} />
              ))}
            </div>
          )}
        </Card>
      )}
      <AutoloadFilesystemModal
        open={fsModalOpen}
        onClose={() => setFsModalOpen(false)}
        onDone={() => { setFsModalOpen(false); fetchFiles() }}
      />
      <AutoloadZipModal
        open={zipModalOpen}
        onClose={() => setZipModalOpen(false)}
        onDone={() => { setZipModalOpen(false); fetchFiles() }}
      />
    </div>
  )
}
