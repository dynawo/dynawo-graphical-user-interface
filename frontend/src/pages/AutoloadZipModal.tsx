//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useState } from 'react'
import { Alert, Button, Modal, Space, Typography, Upload as AntUpload } from 'antd'
import { FileZipOutlined, InboxOutlined, PlayCircleOutlined } from '@ant-design/icons'
import client from '../api/client'

const { Text, Paragraph } = Typography
const { Dragger } = AntUpload

interface Props {
  open: boolean
  onClose: () => void
  onDone: () => void
}

export default function AutoloadZipModal({ open, onClose, onDone }: Props) {
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<{ loaded: string[]; missing: string[]; warnings: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reset = () => { setZipFile(null); setResult(null); setError(null) }
  const handleClose = () => { reset(); onClose() }

  const handleRun = async () => {
    if (!zipFile) return
    setRunning(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', zipFile)
      const res = await client.post<{ loaded: string[]; missing: string[]; warnings: string[] }>(
        '/autoload/run/zip', form
      )
      setResult(res.data)
      onDone()
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Auto-load failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal
      title={<Space><FileZipOutlined /> Auto-load from ZIP archive</Space>}
      open={open}
      onCancel={handleClose}
      footer={null}
      width={600}
    >
      {!result ? (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Create a ZIP archive that preserves the directory structure of your
            case, then upload it here. The archive must contain exactly one{' '}
            <Text code>.jobs</Text> file. All files referenced by the jobs and
            DYD files must be included at their correct relative paths.
          </Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Example: if your jobs file at <Text code>case1/case1.jobs</Text> references{' '}
            <Text code>../grid/network.iidm</Text>, the ZIP should contain both{' '}
            <Text code>case1/case1.jobs</Text> and <Text code>grid/network.iidm</Text>.
          </Paragraph>

          <Dragger
            accept=".zip"
            showUploadList={false}
            beforeUpload={file => { setZipFile(file as unknown as File); return false }}
            style={{ padding: '8px 0' }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            {zipFile
              ? <p className="ant-upload-text"><FileZipOutlined /> {zipFile.name}</p>
              : <p className="ant-upload-text">Click or drag a <Text code>.zip</Text> file here</p>
            }
          </Dragger>

          {error && <Alert type="error" description={error} />}

          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={running}
            disabled={!zipFile}
            onClick={handleRun}
          >
            Load files
          </Button>
        </Space>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          {result.loaded.length > 0 && (
            <Alert type="success" description={`Loaded: ${result.loaded.join(', ')}`} />
          )}
          {result.missing.length > 0 && (
            <Alert type="warning" description={`Not found: ${result.missing.join(', ')}`} />
          )}
          {result.warnings.map((w, i) => (
            <Alert key={i} type="info" description={w} />
          ))}
          <Button onClick={handleClose}>Close</Button>
        </Space>
      )}
    </Modal>
  )
}
