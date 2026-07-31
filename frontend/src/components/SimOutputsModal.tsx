//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useMemo, useState } from 'react'
import { Modal, Tabs, Table, Spin, Empty, Typography, Input, Space, Button, Checkbox } from 'antd'
import type { TableColumnType } from 'antd'
import type { FilterDropdownProps } from 'antd/es/table/interface'
import { SearchOutlined, CloseOutlined } from '@ant-design/icons'

const { Text } = Typography

function getPrefixSearchColumnProps<T extends object>(
  dataIndex: keyof T,
): Partial<TableColumnType<T>> {
  return {
    filterDropdown: ({ setSelectedKeys, selectedKeys, confirm }: FilterDropdownProps) => (
      <div style={{ padding: 8 }} onKeyDown={e => e.stopPropagation()}>
        <Space.Compact style={{ width: 200 }}>
          <Input
            placeholder="Contains…"
            value={selectedKeys[0]}
            onChange={e => {
              const next = e.target.value
              setSelectedKeys(next ? [next] : [])
              confirm({ closeDropdown: false })
            }}
            autoFocus
          />
          <Button
            icon={<CloseOutlined />}
            disabled={!selectedKeys[0]}
            onClick={() => {
              setSelectedKeys([])
              confirm({ closeDropdown: false })
            }}
          />
        </Space.Compact>
      </div>
    ),
    filterIcon: (filtered: boolean) => <SearchOutlined style={{ color: filtered ? '#1677ff' : undefined }} />,
    onFilter: (value, record) => {
      const cell = record[dataIndex]
      return typeof cell === 'string'
        ? cell.toLowerCase().includes(String(value).toLowerCase())
        : false
    },
  }
}

function getEnumFilterColumnProps<T extends object>(
  data: T[],
  dataIndex: keyof T,
): Partial<TableColumnType<T>> {
  const raw = data.map(row => row[dataIndex]) as unknown[]
  const values = Array.from(new Set(raw.filter((v): v is string => typeof v === 'string'))).sort()

  return {
    filterDropdown: ({ setSelectedKeys, selectedKeys, confirm }: FilterDropdownProps) => (
      <div style={{ padding: 8 }}>
        <Checkbox.Group
          style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
          value={selectedKeys as string[]}
          onChange={checked => {
            setSelectedKeys(checked as string[])
            confirm({ closeDropdown: false })
          }}
          options={values.map(v => ({ label: v, value: v }))}
        />
      </div>
    ),
    onFilter: (value, record) => String(record[dataIndex]) === String(value),
  }
}

const LOG_LEVEL_COLORS: Record<string, string> = {
  ERROR: '#ff6b6b',
  WARN:  '#e6c34d',
  INFO:  '#d4d4d4',
  DEBUG: '#8a8a8a',
}

function logLevel(line: string): string | null {
  const m = /^(ERROR|WARN|INFO|DEBUG)\s*\|/.exec(line)
  return m ? m[1] : null
}

function LogViewer({ text }: { text: string }) {
  const [filter, setFilter] = useState('')
  const lines = useMemo(() => text.split('\n'), [text])
  const filtered = useMemo(() => {
    if (!filter.trim()) return lines
    const needle = filter.toLowerCase()
    return lines.filter(l => l.toLowerCase().includes(needle))
  }, [lines, filter])

  if (text.trim() === '') return <Empty description="(empty log)" />

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Input
          size="small"
          placeholder="Filter log lines… (e.g. WARN, ERROR)"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ width: 280 }}
          allowClear
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          {filtered.length} / {lines.length} lines
        </Text>
      </Space>
      <pre style={{
        fontSize: 11, maxHeight: 460, overflow: 'auto', margin: 0,
        background: '#1a1a1a', padding: 8, borderRadius: 4, lineHeight: 1.6,
      }}>
        {filtered.length === 0
          ? <span style={{ color: '#888' }}>No matching lines</span>
          : filtered.map((line, i) => {
            const level = logLevel(line)
            return (
              <div key={i} style={{ color: level ? LOG_LEVEL_COLORS[level] : '#d4d4d4', whiteSpace: 'pre-wrap' }}>
                {line || ' '}
              </div>
            )
          })}
      </pre>
    </div>
  )
}

export interface TimelineEvent {
  time: number
  model_name: string
  message: string
}

export interface ConstraintItem {
  model_name?: string
  description?: string
  time?: number
  type?: string
  kind?: string
  limit?: number
  value?: number
  side?: string
  acceptable_duration?: number
}

export interface LostEquipmentItem {
  id: string
  type: string
}

interface SimOutputsModalProps {
  open: boolean
  onClose: () => void
  title: string
  fetchTimeline?: () => Promise<TimelineEvent[]>
  fetchConstraints?: () => Promise<ConstraintItem[]>
  fetchLog?: () => Promise<string>
  fetchLostEquipments?: () => Promise<LostEquipmentItem[]>
}

type TabKey = 'timeline' | 'constraints' | 'log' | 'lostEquipments'

const CONSTRAINT_COLUMN_DEFS: { key: keyof ConstraintItem; title: string }[] = [
  { key: 'time', title: 'Time (s)' },
  { key: 'model_name', title: 'Model' },
  { key: 'description', title: 'Description' },
  { key: 'type', title: 'Type' },
  { key: 'kind', title: 'Kind' },
  { key: 'limit', title: 'Limit' },
  { key: 'value', title: 'Value' },
  { key: 'side', title: 'Side' },
  { key: 'acceptable_duration', title: 'Acceptable duration (s)' },
]

function useLazyTab<T>(fetcher: (() => Promise<T>) | undefined) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  const load = () => {
    if (loaded || !fetcher) return
    setLoading(true)
    setError(null)
    fetcher()
      .then(d => setData(d))
      .catch(() => setError('Not available'))
      .finally(() => { setLoading(false); setLoaded(true) })
  }

  return { data, loading, error, load }
}

export default function SimOutputsModal({
  open, onClose, title, fetchTimeline, fetchConstraints, fetchLog, fetchLostEquipments,
}: SimOutputsModalProps) {
  const [activeTab, setActiveTab] = useState<TabKey>('timeline')

  const timeline = useLazyTab(fetchTimeline)
  const constraints = useLazyTab(fetchConstraints)
  const log = useLazyTab(fetchLog)
  const lostEquipments = useLazyTab(fetchLostEquipments)

  const handleTabChange = (key: string) => {
    setActiveTab(key as TabKey)
    if (key === 'timeline') timeline.load()
    if (key === 'constraints') constraints.load()
    if (key === 'log') log.load()
    if (key === 'lostEquipments') lostEquipments.load()
  }

  const handleAfterOpenChange = (isOpen: boolean) => {
    if (isOpen) handleTabChange(activeTab)
  }

  const constraintColumns = CONSTRAINT_COLUMN_DEFS
    .filter(c => (constraints.data ?? []).some(row => row[c.key] !== undefined))
    .map(c => ({
      title: c.title,
      dataIndex: c.key,
      key: c.key,
      render: (v: unknown) => {
        if (v === undefined || v === null) return <Text type="secondary">—</Text>
        if (c.key === 'value' && typeof v === 'number') return v.toFixed(2)
        return String(v)
      },
      ...(c.key === 'model_name' ? getPrefixSearchColumnProps<ConstraintItem>(c.key) : {}),
      ...(c.key === 'type' || c.key === 'kind' ? getEnumFilterColumnProps(constraints.data ?? [], c.key) : {}),
    }))

  return (
    <Modal open={open} onCancel={onClose} title={title} footer={null} width={800}
           afterOpenChange={handleAfterOpenChange}>
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          {
            key: 'timeline',
            label: 'Timeline',
            disabled: !fetchTimeline,
            children: timeline.loading
              ? <Spin />
              : timeline.error
                ? <Empty description={timeline.error} />
                : (timeline.data ?? []).length === 0
                  ? <Empty description="No timeline events" />
                  : (
                    <Table
                      size="small"
                      rowKey={(_, i) => String(i)}
                      dataSource={timeline.data ?? []}
                      pagination={{ pageSize: 20 }}
                      columns={[
                        { title: 'Time (s)', dataIndex: 'time', key: 'time', width: 100, sorter: (a, b) => a.time - b.time, defaultSortOrder: 'ascend' },
                        { title: 'Model', dataIndex: 'model_name', key: 'model_name', ...getPrefixSearchColumnProps<TimelineEvent>('model_name') },
                        { title: 'Message', dataIndex: 'message', key: 'message', ...getPrefixSearchColumnProps<TimelineEvent>('message') },
                      ]}
                    />
                  ),
          },
          {
            key: 'constraints',
            label: 'Constraints',
            disabled: !fetchConstraints,
            children: constraints.loading
              ? <Spin />
              : constraints.error
                ? <Empty description={constraints.error} />
                : (constraints.data ?? []).length === 0
                  ? <Empty description="No constraints" />
                  : (
                    <Table
                      size="small"
                      rowKey={(_, i) => String(i)}
                      dataSource={constraints.data ?? []}
                      pagination={{ pageSize: 20 }}
                      columns={constraintColumns}
                    />
                  ),
          },
          {
            key: 'log',
            label: 'Log',
            disabled: !fetchLog,
            children: log.loading
              ? <Spin />
              : log.error
                ? <Empty description={log.error} />
                : <LogViewer text={log.data ?? ''} />,
          },
          {
            key: 'lostEquipments',
            label: 'Lost equipment',
            disabled: !fetchLostEquipments,
            children: lostEquipments.loading
              ? <Spin />
              : lostEquipments.error
                ? <Empty description={lostEquipments.error} />
                : (lostEquipments.data ?? []).length === 0
                  ? <Empty description="No lost equipment" />
                  : (
                    <Table
                      size="small"
                      rowKey={(_, i) => String(i)}
                      dataSource={lostEquipments.data ?? []}
                      pagination={{ pageSize: 20 }}
                      columns={[
                        { title: 'Id', dataIndex: 'id', key: 'id' },
                        { title: 'Type', dataIndex: 'type', key: 'type' },
                      ]}
                    />
                  ),
          },
        ]}
      />
    </Modal>
  )
}
