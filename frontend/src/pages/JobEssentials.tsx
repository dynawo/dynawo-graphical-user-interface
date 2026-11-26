//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Essentials card of the Simulation Settings page: the most used fields of a job, from the hints.

import { Button, Card, Checkbox, Form, Select, Space, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { AttributeField, type Ctx } from './jobsFields'
import {
  CHANGED_STYLE, hasChangesUnder, isChanged, attributeLabel, childIndex, childIndexes, elementLabel, fieldStatus, fileOptions, fileRefType, findPath, getNode, insertChild, parseFieldRef, setAttribute,
  specAt, updateNode,
  type ElementNode, type ElementSpec, type EssentialsGroup, type Path,
} from './jobsTree'

const { Text } = Typography

const NO_FILE = '(no file)'

interface Props {
  ctx: Ctx
  root: ElementNode
  savedRoot: ElementNode | null  // tree as last saved, to mark changed lists
  jobIndex: number
  jobSpec: ElementSpec | undefined
  newNode: (schemaPath: string) => Promise<ElementNode>
  onEdit: (fn: (root: ElementNode) => ElementNode) => void
}

export default function JobEssentials({ ctx, root, savedRoot, jobIndex, jobSpec, newNode, onEdit }: Props) {
  const groups = ctx.schema.hints.essentials ?? []

  /** Values of `attribute` over the `tag` children of the element at `parent`, in a tree. */
  const listValues = (tree: ElementNode, parent: string[], tag: string, attribute: string): string[] => {
    const job = tree.children[jobIndex]
    if (!job || !('tag' in job) || job.tag !== 'job') return []
    const parentPath = findPath(tree, [jobIndex], parent)
    if (!parentPath) return []
    const node = getNode(tree, parentPath)
    return childIndexes(node, tag).map(i => (node.children[i] as ElementNode).attrs[attribute] ?? '')
  }

  /** Create the elements of `elements` that are missing; `newLast` always adds a new last one. */
  const addElement = async (elements: string[], newLast = false, attrs: Record<string, string> = {}) => {
    // New nodes are fetched first, then inserted into the latest tree in one edit.
    const created: Record<number, ElementNode> = {}
    let path: Path | null = [jobIndex]
    for (let k = 0; k < elements.length; k++) {
      const last = k === elements.length - 1
      const index = path ? childIndex(getNode(root, path), elements[k]) : -1
      if (index === -1 || (last && newLast)) {
        created[k] = await newNode(`jobs/job/${elements.slice(0, k + 1).join('/')}`)
        path = null
      } else if (path) {
        path = [...path, index]
      }
    }
    onEdit(latest => {
      let tree = latest
      let current: Path = [jobIndex]
      for (let k = 0; k < elements.length; k++) {
        const last = k === elements.length - 1
        let index = childIndex(getNode(tree, current), elements[k])
        if (index === -1 || (last && newLast)) {
          if (!created[k]) return latest
          const inserted = insertChild(tree, current, specAt(jobSpec, elements.slice(0, k)), created[k])
          tree = inserted.root
          index = inserted.index
        }
        current = [...current, index]
      }
      for (const [name, value] of Object.entries(attrs)) tree = setAttribute(tree, current, name, value)
      return tree
    })
  }

  /** Remove every `tag` child of the element at `elements` whose attribute is in `values` (all if undefined). */
  const removeChildren = (elements: string[], tag: string, attribute?: string, values?: string[]) => {
    onEdit(latest => {
      const parentPath = findPath(latest, [jobIndex], elements)
      if (!parentPath) return latest
      return updateNode(latest, parentPath, node => ({
        ...node,
        children: node.children.filter(c => !('tag' in c && c.tag === tag
          && (values === undefined || values.includes(c.attrs[attribute ?? ''] ?? '')))),
      }))
    })
  }

  const field = (ref: string, missingShown: Set<string>) => {
    const { elements, attribute, list } = parseFieldRef(ref)
    const spec = specAt(jobSpec, elements)
    if (!spec || !spec.attributes.some(a => a.name === attribute)) return null
    const label = attributeLabel(ctx.schema, spec.name, attribute)

    if (list) {
      const parent = elements.slice(0, -1)
      const tag = elements[elements.length - 1]
      const parentPath = findPath(root, [jobIndex], parent)
      if (!parentPath) return null
      const values = listValues(root, parent, tag, attribute)
      const ftype = fileRefType(ctx.schema.file_refs, tag, attribute)
      const options = ftype !== undefined ? fileOptions(ctx.files, ctx.jobsFile, ftype) : []
      const changed = savedRoot !== null && values.join('\n') !== listValues(savedRoot, parent, tag, attribute).join('\n')
      return (
        <Form.Item key={ref} label={elementLabel(ctx.schema, tag)}
          style={{ marginBottom: 8, ...(changed ? CHANGED_STYLE : {}) }}>
          <Select mode="tags" style={{ width: 480 }} value={values.map(v => v || NO_FILE)} options={options}
            placeholder="none"
            onChange={(next: string[]) => {
              const wanted = next.map(v => (v === NO_FILE ? '' : v))
              const removed = values.filter(v => !wanted.includes(v))
              if (removed.length) removeChildren(parent, tag, attribute, removed)
              for (const v of wanted.filter(v => !values.includes(v))) addElement(elements, true, { [attribute]: v })
            }} />
        </Form.Item>
      )
    }

    const path = findPath(root, [jobIndex], elements)
    if (!path) {
      // One Add button per missing element, not one per field.
      const key = elements.join('/')
      if (missingShown.has(key)) return null
      missingShown.add(key)
      return (
        <Form.Item key={ref} label={elementLabel(ctx.schema, spec.name)} style={{ marginBottom: 8 }}>
          <Button size="small" type="dashed" icon={<PlusOutlined />} onClick={() => addElement(elements)}>
            Add {elementLabel(ctx.schema, spec.name).toLowerCase()}
          </Button>
        </Form.Item>
      )
    }
    const hint = ctx.schema.hints.attributes[`${spec.name}@${attribute}`] ?? {}
    return (
      <Form.Item key={ref} label={label} extra={hint.help}
        style={{ marginBottom: 8, ...(isChanged(ctx.diff, path, attribute) ? CHANGED_STYLE : {}) }}
        {...fieldStatus(ctx.issues, path, attribute)}>
        <AttributeField ctx={ctx} node={getNode(root, path)} path={path} name={attribute}
          spec={spec.attributes.find(a => a.name === attribute)} />
      </Form.Item>
    )
  }

  const toggles = (group: EssentialsGroup) => {
    const elements = group.toggles!.split('/')
    const parentSpec = specAt(jobSpec, elements)
    if (!parentSpec) return null
    const parentPath = findPath(root, [jobIndex], elements)
    const parentNode = parentPath ? getNode(root, parentPath) : null
    return (
      <Form.Item label="Exported" style={{ marginBottom: 8 }}>
        <Space orientation="vertical" size={6} style={{ width: '100%' }}>
          {parentSpec.children.filter(c => !(group.exclude ?? []).includes(c.name)).map(child => {
            const indexes = parentNode ? childIndexes(parentNode, child.name) : []
            const first = parentPath && indexes.length ? [...parentPath, indexes[0]] : null
            const node = first ? getNode(root, first) : null
            return (
              <Space key={child.name} wrap align="start">
                <Checkbox style={{ width: 180 }} checked={indexes.length > 0}
                  onChange={e => (e.target.checked
                    ? addElement([...elements, child.name])
                    : removeChildren(elements, child.name))}>
                  <span style={first && hasChangesUnder(ctx.diff, first) ? { color: '#1677ff', fontWeight: 500 } : undefined}>
                    {elementLabel(ctx.schema, child.name)}
                  </span>
                  {indexes.length > 1 && <Text type="secondary"> ({indexes.length})</Text>}
                </Checkbox>
                {node && first && child.attributes.filter(a => a.required).map(a => {
                  const status = fieldStatus(ctx.issues, first, a.name)
                  return (
                    <Space key={a.name} size={4}>
                      <Text type={status.validateStatus === 'error' ? 'danger' : status.validateStatus ? 'warning' : 'secondary'}>
                        {attributeLabel(ctx.schema, child.name, a.name)}
                      </Text>
                      <AttributeField ctx={ctx} node={node} path={first} name={a.name} spec={a} width={220} />
                    </Space>
                  )
                })}
              </Space>
            )
          })}
        </Space>
      </Form.Item>
    )
  }

  if (!groups.length) return null
  return (
    <Card size="small" title="Essentials">
      <Form layout="horizontal" labelCol={{ flex: '200px' }} labelWrap colon={false}>
        {groups.map(group => {
          const missingShown = new Set<string>()
          const fields = group.fields.map(ref => field(ref, missingShown)).filter(Boolean)
          const toggleRow = group.toggles ? toggles(group) : null
          if (!fields.length && !toggleRow) return null
          return (
            <div key={group.title} style={{ marginBottom: 8 }}>
              <Text strong style={{ display: 'block', margin: '4px 0 8px' }}>{group.title}</Text>
              {fields}
              {toggleRow}
            </div>
          )
        })}
      </Form>
    </Card>
  )
}
