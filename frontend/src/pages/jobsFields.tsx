//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Attribute field of the Simulation Settings page, and what the form components share.

import { AutoComplete, Input, Segmented, Select, Switch, Typography } from 'antd'
import {
  fileOptions, fileRefType, parSetSource, sessionPath,
  type AttributeSpec, type ElementNode, type ElementSpec, type Issue, type Path, type Schema, type SessionFile,
} from './jobsTree'

const { Text } = Typography

/** What the form components share. */
export interface Ctx {
  schema: Schema
  issues: Issue[]
  jobsFile: string
  files: SessionFile[]
  parSets: Record<string, string[]>
  loadParSets: (file: string) => void
  onAttr: (path: Path, name: string, value: string | undefined) => void
  onAdd: (path: Path, schemaPath: string, spec: ElementSpec | undefined, child: ElementSpec) => void
  onRemove: (path: Path, index: number) => void
  onMove: (path: Path, index: number, delta: -1 | 1) => void
}

// Enumerations up to this size are shown as a row of buttons.
const SEGMENTED_MAX = 5

export function AttributeField({ ctx, node, path, name, spec, width = 360 }: {
  ctx: Ctx; node: ElementNode; path: Path; name: string; spec: AttributeSpec | undefined; width?: number
}) {
  const value = node.attrs[name]
  const hint = ctx.schema.hints.attributes[`${node.tag}@${name}`] ?? {}
  const set = (v: string | undefined) => ctx.onAttr(path, name, v)
  const ftype = fileRefType(ctx.schema.file_refs, node.tag, name)
  const parFileAttr = parSetSource(ctx.schema.file_refs, node.tag, name)
  const style = { width }

  if (ftype !== undefined) {
    return <AutoComplete style={style} value={value} options={fileOptions(ctx.files, ctx.jobsFile, ftype)} onChange={set}
      filterOption={(input, o) => (o?.label ?? '').toLowerCase().includes(input.toLowerCase())} />
  }
  if (parFileAttr && node.attrs[parFileAttr]) {
    const parFile = sessionPath(ctx.jobsFile, node.attrs[parFileAttr])
    return <AutoComplete style={style} value={value} onChange={set}
      onFocus={() => ctx.loadParSets(parFile)} options={(ctx.parSets[parFile] ?? []).map(s => ({ value: s }))} />
  }
  if (spec?.type === 'boolean') {
    return <Switch checked={value === 'true' || value === '1'} onChange={on => set(on ? 'true' : 'false')} />
  }
  if (spec?.type === 'enum') {
    const values = spec.values ?? []
    if (values.length <= SEGMENTED_MAX) {
      return <Segmented value={value} options={values} onChange={v => set(String(v))} />
    }
    return <Select style={{ width: Math.min(width, 200) }} value={value} allowClear={!spec.required}
      onChange={v => set(v ?? undefined)} options={values.map(v => ({ value: v }))} />
  }
  if (hint.values) {
    return <AutoComplete style={style} value={value} onChange={set} options={hint.values.map(v => ({ value: v }))} />
  }
  return <Input style={style} value={value} onChange={e => set(e.target.value)}
    inputMode={spec?.type === 'number' || spec?.type === 'integer' ? 'decimal' : undefined}
    suffix={hint.unit ? <Text type="secondary">{hint.unit}</Text> : undefined} />
}
