//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { Collapse, Flex, Input, InputNumber, Select, Space, Switch, Tooltip, Typography } from 'antd'

const { Text } = Typography

// A form for a pypowsybl parameter table — Load Flow's provider parameters,
// network import parameters — typed and driven by the specs the server sends.
// Specs sharing a category are grouped in a collapsible panel.

export interface PowsyblParamSpec {
  name: string
  category: string
  description: string
  type: 'BOOLEAN' | 'STRING' | 'STRING_LIST' | 'DOUBLE' | 'INTEGER'
  default: unknown
  possible_values: string[]
}

export default function PowsyblParamsForm({
  specs, value, onChange,
}: {
  specs: PowsyblParamSpec[]
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
}) {
  const set = (name: string, v: unknown) => onChange({ ...value, [name]: v })

  const renderControl = (spec: PowsyblParamSpec) => {
    const val = value[spec.name] ?? spec.default
    switch (spec.type) {
      case 'BOOLEAN':
        return <Switch size="small" checked={!!val} onChange={v => set(spec.name, v)} />
      case 'STRING_LIST':
        return (
          <Select
            size="small" mode={spec.possible_values.length ? 'multiple' : 'tags'}
            value={(val as string[]) ?? []}
            onChange={v => set(spec.name, v)}
            style={{ width: 260 }}
            options={spec.possible_values.map(v => ({ label: v, value: v }))}
          />
        )
      case 'DOUBLE':
      case 'INTEGER':
        return (
          <InputNumber
            size="small" value={val as number | null}
            step={spec.type === 'INTEGER' ? 1 : undefined}
            precision={spec.type === 'INTEGER' ? 0 : undefined}
            onChange={v => set(spec.name, v)}
            style={{ width: 140 }}
          />
        )
      default: // STRING
        return spec.possible_values.length
          ? (
            <Select
              size="small" value={val as string} onChange={v => set(spec.name, v)}
              style={{ width: 200 }}
              options={spec.possible_values.map(v => ({ label: v, value: v }))}
            />
          )
          : (
            <Input
              size="small" value={val as string} onChange={e => set(spec.name, e.target.value)}
              style={{ width: 200 }}
            />
          )
    }
  }

  const row = (spec: PowsyblParamSpec) => (
    <Space align="center" key={spec.name} style={{ marginBottom: 6 }}>
      <Tooltip title={spec.description}>
        <Text style={{ fontSize: 12, color: '#888', minWidth: 200, display: 'inline-block' }}>
          {spec.name}
        </Text>
      </Tooltip>
      {renderControl(spec)}
    </Space>
  )

  const byCategory: Record<string, PowsyblParamSpec[]> = {}
  for (const s of specs) (byCategory[s.category || '_'] ??= []).push(s)
  const categories = Object.keys(byCategory)

  if (categories.length === 1 && categories[0] === '_') {
    return <Flex vertical>{specs.map(row)}</Flex>
  }

  return (
    <Collapse
      size="small"
      items={categories.sort().map(cat => ({
        key: cat,
        label: cat === '_' ? 'General' : cat,
        children: <Flex vertical>{byCategory[cat].map(row)}</Flex>,
      }))}
    />
  )
}
