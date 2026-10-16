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
import { Alert, Button, Checkbox, Collapse, Modal, Space, Spin, Typography } from 'antd'
import { SettingOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail } from '../api/errors'
import PowsyblParamsForm, { type PowsyblParamSpec } from '../components/PowsyblParamsForm'

const { Text } = Typography

export interface NetworkSource {
  name: string
  source_format?: string
  converted_to?: string
  import_error?: string
}

// Mounted per source (keyed by its name) and unmounted on close, so each
// opening starts from that source's state.
interface Props {
  source: NetworkSource
  onClose: () => void
  onDone: () => void
}

// The values differing from pypowsybl's defaults — the only ones sent and saved,
// so a later pypowsybl changing a default is not overridden by a stale copy.
function changedValues(specs: PowsyblParamSpec[], value: Record<string, unknown>) {
  const out: Record<string, unknown> = {}
  for (const spec of specs) {
    if (spec.name in value && JSON.stringify(value[spec.name]) !== JSON.stringify(spec.default)) {
      out[spec.name] = value[spec.name]
    }
  }
  return out
}

export default function NetworkImportModal({ source, onClose, onDone }: Props) {
  const [specs, setSpecs] = useState<PowsyblParamSpec[]>([])
  const [value, setValue] = useState<Record<string, unknown>>({})
  const [save, setSave] = useState(true)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(source.import_error ?? null)

  const format = source.source_format ?? ''

  useEffect(() => {
    client.get<{ specs: PowsyblParamSpec[]; saved: Record<string, unknown> }>(
      '/network-import/parameters', { params: { format } },
    )
      .then(res => { setSpecs(res.data.specs); setValue(res.data.saved ?? {}) })
      .catch(err => setError(errorDetail(err, 'Could not read the import parameters')))
      .finally(() => setLoading(false))
  }, [format])

  const handleImport = async () => {
    setRunning(true)
    setError(null)
    try {
      const res = await client.post<NetworkSource>('/network-import/reimport', {
        filename: source.name, parameters: changedValues(specs, value), save,
      })
      if (res.data.import_error) {
        setError(res.data.import_error)
      } else {
        onDone()
      }
    } catch (err) {
      setError(errorDetail(err, 'Import failed'))
    } finally {
      setRunning(false)
    }
  }

  // Advanced specs are rendered flat inside their own panel, below the main ones.
  const main = specs.filter(s => s.category !== 'Advanced')
  const advanced = specs.filter(s => s.category === 'Advanced').map(s => ({ ...s, category: '' }))

  return (
    <Modal
      title={<Space><SettingOutlined /> Import {source.name} ({format})</Space>}
      open
      onCancel={onClose}
      width={720}
      footer={[
        <Button key="reset" onClick={() => setValue({})} disabled={running}>Reset to defaults</Button>,
        <Button key="cancel" onClick={onClose} disabled={running}>Cancel</Button>,
        <Button key="import" type="primary" loading={running} disabled={loading} onClick={handleImport}>
          {source.converted_to ? `Import again into ${source.converted_to}` : 'Import'}
        </Button>,
      ]}
    >
      {error && <Alert type="error" description={error} style={{ marginBottom: 12 }} />}
      {format === 'CGMES' && (
        <Alert
          type="info"
          style={{ marginBottom: 12 }}
          description={
            <>
              A model referring to the ENTSO-E boundary set needs the boundary profiles
              (<Text code>EQ_BD</Text>, <Text code>TP_BD</Text>) in the zip, next to EQ/SSH/TP/SV.
            </>
          }
        />
      )}
      {loading ? <Spin /> : (
        <>
          {main.length === 0 && advanced.length === 0 && (
            <Text type="secondary">This format has no import parameters.</Text>
          )}
          <PowsyblParamsForm specs={main} value={value} onChange={setValue} />
          {advanced.length > 0 && (
            <Collapse
              size="small"
              style={{ marginTop: 8 }}
              items={[{
                key: 'advanced',
                label: 'Advanced',
                children: <PowsyblParamsForm specs={advanced} value={value} onChange={setValue} />,
              }]}
            />
          )}
          <Checkbox checked={save} onChange={e => setSave(e.target.checked)} style={{ marginTop: 12 }}>
            Use these parameters for the next {format} files uploaded
          </Checkbox>
        </>
      )}
    </Modal>
  )
}
