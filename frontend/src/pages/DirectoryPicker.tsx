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
import { Button, Spin, Typography, theme } from 'antd'
import { FileOutlined, FolderFilled, LeftOutlined, SelectOutlined } from '@ant-design/icons'
import client from '../api/client'

const { Text } = Typography

interface BrowseResult {
  current: string
  parent:  string | null
  dirs:    string[]
  files:   string[]
}

interface Props {
  initialPath?: string
  onSelect?: (path: string) => void          // called with folder path; shows "Select this folder" button
  onSelectFile?: (path: string) => void      // called with full file path when a file is clicked
  fileFilter?: (name: string) => boolean     // which files are selectable (default: all)
  onCancel: () => void
}

export default function DirectoryPicker({ initialPath, onSelect, onSelectFile, fileFilter, onCancel }: Props) {
  const { token } = theme.useToken()
  const [data, setData]       = useState<BrowseResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  const navigate = async (path: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await client.get<BrowseResult>(
        `/autoload/browse?path=${encodeURIComponent(path)}`
      )
      setData(res.data)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? 'Cannot open directory')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { navigate(initialPath ?? '') }, [initialPath])

  // Breadcrumb segments from current path
  const breadcrumbs = data
    ? data.current.split('/').filter(Boolean).reduce<{ label: string; path: string }[]>(
        (acc, seg) => {
          const prev = acc.length ? acc[acc.length - 1].path : ''
          acc.push({ label: seg, path: `${prev}/${seg}` })
          return acc
        },
        [{ label: '/', path: '/' }]
      )
    : []

  return (
    <div style={{
      border: `1px solid ${token.colorBorderSecondary}`,
      borderRadius: token.borderRadius,
      overflow: 'hidden',
    }}>
      {/* ── Toolbar ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 10px',
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
        background: token.colorBgLayout,
        flexWrap: 'wrap',
      }}>
        <Button
          size="small"
          icon={<LeftOutlined />}
          disabled={!data?.parent || loading}
          onClick={() => data?.parent && navigate(data.parent)}
        />
        {/* Breadcrumb */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', flex: 1 }}>
          {breadcrumbs.map((bc, i) => (
            <span key={bc.path} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              {i > 0 && <Text type="secondary" style={{ fontSize: 11 }}>/</Text>}
              <Button
                type="link"
                size="small"
                style={{ padding: '0 2px', height: 'auto', fontSize: 12 }}
                onClick={() => navigate(bc.path)}
              >
                {bc.label}
              </Button>
            </span>
          ))}
        </div>
      </div>

      {/* ── Directory listing ── */}
      <div style={{ maxHeight: 220, overflowY: 'auto', padding: '4px 0' }}>
        {loading && (
          <div style={{ padding: 16, textAlign: 'center' }}>
            <Spin size="small" />
          </div>
        )}
        {error && (
          <div style={{ padding: '8px 12px' }}>
            <Text type="danger" style={{ fontSize: 12 }}>{error}</Text>
          </div>
        )}
        {!loading && data && data.dirs.length === 0 && data.files.length === 0 && (
          <div style={{ padding: '8px 12px' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>Empty directory</Text>
          </div>
        )}
        {!loading && data && (
          <>
            {data.dirs.map(dir => (
              <div
                key={dir}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '4px 12px', cursor: 'pointer',
                  fontSize: 13,
                }}
                onMouseEnter={e => (e.currentTarget.style.background = token.colorBgTextHover)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => navigate(`${data.current}/${dir}`)}
              >
                <FolderFilled style={{ color: '#faad14', fontSize: 14 }} />
                <Text style={{ fontSize: 13 }}>{dir}</Text>
              </div>
            ))}
            {data.files.map(file => {
              const selectable = !!onSelectFile && (!fileFilter || fileFilter(file))
              return (
                <div
                  key={file}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '4px 12px', fontSize: 13,
                    opacity: selectable ? 1 : 0.4,
                    cursor: selectable ? 'pointer' : 'default',
                  }}
                  onMouseEnter={selectable ? e => (e.currentTarget.style.background = token.colorBgTextHover) : undefined}
                  onMouseLeave={selectable ? e => (e.currentTarget.style.background = 'transparent') : undefined}
                  onClick={selectable ? () => onSelectFile(`${data.current}/${file}`) : undefined}
                >
                  <FileOutlined style={{ fontSize: 14, color: selectable ? token.colorPrimary : undefined }} />
                  <Text style={{ fontSize: 13 }}>{file}</Text>
                </div>
              )
            })}
          </>
        )}
      </div>

      {/* ── Footer ── */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '6px 10px',
        borderTop: `1px solid ${token.colorBorderSecondary}`,
        background: token.colorBgLayout,
      }}>
        <Text type="secondary" style={{ fontSize: 11 }}>{data?.current ?? ''}</Text>
        <div style={{ display: 'flex', gap: 8 }}>
          <Button size="small" onClick={onCancel}>Cancel</Button>
          {onSelect && (
            <Button
              size="small"
              type="primary"
              icon={<SelectOutlined />}
              disabled={!data}
              onClick={() => data && onSelect(data.current)}
            >
              Select this folder
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
