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
import { Alert, Button, Input, Modal, Space, Table, Tag, Typography } from 'antd'
import { FolderOpenOutlined, PlayCircleOutlined } from '@ant-design/icons'
import client from '../api/client'
import DirectoryPicker from './DirectoryPicker'

const { Text } = Typography

interface PreviewEntry {
  role: string
  raw: string
  resolved: string
  exists: boolean
}

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

const ROLE_LABEL: Record<string, string> = {
  solver_par:  'Solver PAR',
  iidm:        'Network (IIDM)',
  network_par: 'Network PAR',
  dyd:         'DYD',
  crv:         'Curves (CRV)',
  dyd_par:     'Model PAR',
}

export default function AutoloadFilesystemModal({ open, onClose, onDone }: Props) {
  const [baseDir, setBaseDir] = useState('')
  const [showPicker, setShowPicker] = useState(false)
  const [preview, setPreview] = useState<PreviewEntry[] | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<{ loaded: string[]; missing: string[]; warnings: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    client.get<{ suggestion: string | null }>('/autoload/suggest-base-dir')
      .then(res => { if (res.data.suggestion) setBaseDir(res.data.suggestion) })
      .catch(() => {})
  }, [open])

  const reset = () => {
    setShowPicker(false)
    setPreview(null)
    setResult(null)
    setError(null)
  }

  const handleClose = () => { reset(); onClose() }

  const handlePreview = async () => {
    setPreviewing(true)
    setError(null)
    setPreview(null)
    try {
      const res = await client.get<{ files: PreviewEntry[] }>(
        `/autoload/preview?base_dir=${encodeURIComponent(baseDir)}`
      )
      setPreview(res.data.files)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Preview failed')
    } finally {
      setPreviewing(false)
    }
  }

  const handleRun = async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await client.post<{ loaded: string[]; missing: string[]; warnings: string[] }>(
        '/autoload/run/filesystem', { base_dir: baseDir }
      )
      setResult(res.data)
      onDone()
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Auto-load failed')
    } finally {
      setRunning(false)
    }
  }

  const allFound = preview !== null && preview.every(e => e.exists)

  return (
    <Modal
      title={<Space><FolderOpenOutlined /> Auto-load from filesystem</Space>}
      open={open}
      onCancel={handleClose}
      footer={null}
      width={700}
    >
      {!result ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Text type="secondary">
            Enter the directory on the server where the referenced files live
            (the folder containing the original <Text code>.jobs</Text> file).
          </Text>

          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder="/projects/case1"
              value={baseDir}
              onChange={e => { setBaseDir(e.target.value); setPreview(null); setShowPicker(false) }}
              onPressEnter={handlePreview}
            />
            <Button
              icon={<FolderOpenOutlined />}
              onClick={() => setShowPicker(v => !v)}
            >
              Browse
            </Button>
            <Button loading={previewing} onClick={handlePreview} disabled={!baseDir.trim()}>
              Preview
            </Button>
          </Space.Compact>

          {showPicker && (
            <DirectoryPicker
              initialPath={baseDir || undefined}
              onSelect={path => { setBaseDir(path); setShowPicker(false); setPreview(null) }}
              onCancel={() => setShowPicker(false)}
            />
          )}

          {error && <Alert type="error" description={error} />}

          {preview && (
            <>
              <Table
                size="small"
                pagination={false}
                dataSource={preview}
                rowKey={(r, i) => `${r.role}-${i}`}
                columns={[
                  {
                    title: 'Role', dataIndex: 'role', width: 120,
                    render: (r: string) => <Tag>{ROLE_LABEL[r] ?? r}</Tag>,
                  },
                  { title: 'Path in jobs/dyd', dataIndex: 'raw' },
                  {
                    title: '', dataIndex: 'exists', width: 60, align: 'center' as const,
                    render: (exists: boolean) => exists
                      ? <Text type="success">✓</Text>
                      : <Text type="danger">✗</Text>,
                  },
                ]}
              />
              {!allFound && (
                <Alert type="warning" description="Some files were not found. They will be skipped." />
              )}
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={running}
                onClick={handleRun}
                disabled={preview.every(e => !e.exists)}
              >
                Load files
              </Button>
            </>
          )}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {result.loaded.length > 0 && (
            <Alert
              type="success"
              description={`Loaded: ${result.loaded.join(', ')}`}
            />
          )}
          {result.missing.length > 0 && (
            <Alert
              type="warning"
              description={`Not found: ${result.missing.join(', ')}`}
            />
          )}
          {result.warnings.map((w, i) => (
            <Alert key={i} type="info" description={w} />
          ))}
          <Button onClick={handleClose}>Close</Button>
        </div>
      )}
    </Modal>
  )
}
