//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// Types and pure helpers of the Simulation Settings page (a .jobs document as a tree).

export const DYN_NS = 'http://www.rte-france.com/dynawo'

export interface AttributeSpec {
  name: string
  type: 'string' | 'number' | 'integer' | 'boolean' | 'enum'
  required: boolean
  default?: string
  values?: string[]
}

export interface ElementSpec {
  name: string
  min: number
  max: number | null
  ordered: boolean
  attributes: AttributeSpec[]
  children: ElementSpec[]
  unique: { element: string; attribute: string }[]
}

export interface ElementHint { label?: string; help?: string; create?: boolean }
export interface AttributeHint { label?: string; help?: string; unit?: string; default?: string; values?: string[] }
export interface Hints {
  essentials?: EssentialsGroup[]
  elements: Record<string, ElementHint>
  attributes: Record<string, AttributeHint>
}

export interface FileRef { element: string; attribute: string; ftype: string | null }

export interface Schema {
  dynawo: string
  xsd: string
  root: ElementSpec
  file_refs: FileRef[]
  hints: Hints
}

export interface ElementNode {
  tag: string
  ns?: string | null
  attrs: Record<string, string>
  text?: string
  children: TreeNode[]
}
export interface CommentNode { comment: string }
export interface PiNode { pi: string; text: string }
export type TreeNode = ElementNode | CommentNode | PiNode

export interface JobsDocument {
  nsmap?: Record<string, string>
  prolog?: TreeNode[]
  root: ElementNode
  epilog?: TreeNode[]
}

export interface Issue {
  level: 'error' | 'warning'
  path: number[]
  attribute?: string
  message: string
}

export type Path = number[]

export function isElement(node: TreeNode): node is ElementNode {
  return 'tag' in node
}

/** True for an element of Dynawo's namespace. */
export function isDynElement(node: TreeNode): node is ElementNode {
  return isElement(node) && (node.ns === undefined || node.ns === DYN_NS)
}

export function getNode(root: ElementNode, path: Path): ElementNode {
  let node = root
  for (const i of path) node = node.children[i] as ElementNode
  return node
}

/** Copy of the tree with the element at `path` replaced by `fn(element)`. */
export function updateNode(root: ElementNode, path: Path, fn: (node: ElementNode) => ElementNode): ElementNode {
  if (!path.length) return fn(root)
  const [head, ...rest] = path
  const children = root.children.slice()
  children[head] = updateNode(children[head] as ElementNode, rest, fn)
  return { ...root, children }
}

export function setAttribute(root: ElementNode, path: Path, name: string, value: string | undefined): ElementNode {
  return updateNode(root, path, node => {
    const attrs = { ...node.attrs }
    if (value === undefined) delete attrs[name]
    else attrs[name] = value
    return { ...node, attrs }
  })
}

export function removeChild(root: ElementNode, parentPath: Path, index: number): ElementNode {
  return updateNode(root, parentPath, node => ({ ...node, children: node.children.filter((_, i) => i !== index) }))
}

/** Where a new `tag` child goes: after its schema predecessors when ordered, else at the end. */
export function insertIndex(parent: ElementNode, spec: ElementSpec | undefined, tag: string): number {
  if (!spec?.ordered) return parent.children.length
  const order = spec.children.map(c => c.name)
  const rank = order.indexOf(tag)
  let index = 0
  parent.children.forEach((child, i) => {
    if (isDynElement(child) && order.indexOf(child.tag) !== -1 && order.indexOf(child.tag) <= rank) index = i + 1
  })
  return index
}

export function insertChild(root: ElementNode, parentPath: Path, spec: ElementSpec | undefined,
                            child: ElementNode): { root: ElementNode; index: number } {
  const index = insertIndex(getNode(root, parentPath), spec, child.tag)
  const updated = updateNode(root, parentPath, node => {
    const children = node.children.slice()
    children.splice(index, 0, child)
    return { ...node, children }
  })
  return { root: updated, index }
}

/** Swap a child with its previous (-1) or next (+1) sibling of the same tag. */
export function moveChild(root: ElementNode, parentPath: Path, index: number, delta: -1 | 1): ElementNode {
  return updateNode(root, parentPath, node => {
    const tag = (node.children[index] as ElementNode).tag
    let other = index + delta
    while (other >= 0 && other < node.children.length) {
      const c = node.children[other]
      if (isElement(c) && c.tag === tag) break
      other += delta
    }
    if (other < 0 || other >= node.children.length) return node
    const children = node.children.slice()
    ;[children[index], children[other]] = [children[other], children[index]]
    return { ...node, children }
  })
}

export function countChildren(node: ElementNode, tag: string): number {
  return node.children.filter(c => isDynElement(c) && c.tag === tag).length
}

/** Child elements the schema still allows under `node`. */
export function addableChildren(node: ElementNode, spec: ElementSpec | undefined): ElementSpec[] {
  if (!spec) return []
  return spec.children.filter(c => c.max === null || countChildren(node, c.name) < c.max)
}

/** Whether a `tag` child of `node` can be removed without going under its minimum. */
export function canRemove(node: ElementNode, spec: ElementSpec | undefined, tag: string): boolean {
  const child = spec?.children.find(c => c.name === tag)
  return !child || countChildren(node, tag) > child.min
}

export function childSpec(spec: ElementSpec | undefined, node: TreeNode): ElementSpec | undefined {
  return spec && isDynElement(node) ? spec.children.find(c => c.name === node.tag) : undefined
}

/** File type expected by an attribute: a type, null for any file, undefined if not a file. */
export function fileRefType(fileRefs: FileRef[], element: string, attribute: string): string | null | undefined {
  const ref = fileRefs.find(r => r.element === element && r.attribute === attribute)
  return ref ? ref.ftype : undefined
}

/** The parFile sibling of a parId, when that parFile is a .par reference. */
export function parSetSource(fileRefs: FileRef[], element: string, attribute: string): string | undefined {
  return attribute === 'parId' && fileRefType(fileRefs, element, 'parFile') === 'par' ? 'parFile' : undefined
}

export function issuesAt(issues: Issue[], path: Path): Issue[] {
  return issues.filter(i => i.path.length === path.length && i.path.every((v, k) => v === path[k]))
}

function dirname(path: string): string {
  const i = path.lastIndexOf('/')
  return i === -1 ? '' : path.slice(0, i)
}

function normalise(parts: string[]): string[] {
  const out: string[] = []
  for (const p of parts) {
    if (p === '' || p === '.') continue
    if (p === '..' && out.length && out[out.length - 1] !== '..') out.pop()
    else out.push(p)
  }
  return out
}

/** Session path of a reference written in a jobs file (references are relative to its folder). */
export function sessionPath(jobsFile: string, ref: string): string {
  return normalise([...dirname(jobsFile).split('/'), ...ref.split('/')]).join('/')
}

/** Reference to write in a jobs file for a session file. */
export function referenceTo(jobsFile: string, sessionFile: string): string {
  const from = normalise(dirname(jobsFile).split('/'))
  const to = normalise(sessionFile.split('/'))
  let common = 0
  while (common < from.length && common < to.length - 1 && from[common] === to[common]) common++
  return [...Array(from.length - common).fill('..'), ...to.slice(common)].join('/')
}

/** A name not in `taken`: `base`, else base2, base3… */
export function uniqueName(base: string, taken: string[]): string {
  if (!taken.includes(base)) return base
  let n = 2
  while (taken.includes(`${base}${n}`)) n++
  return `${base}${n}`
}

export function jobNames(root: ElementNode): string[] {
  return root.children.filter(c => isDynElement(c) && c.tag === 'job').map(c => (c as ElementNode).attrs.name ?? '')
}

/** Indexes of the <job> children of the root. */
export function jobIndexes(root: ElementNode): number[] {
  return root.children.flatMap((c, i) => (isDynElement(c) && c.tag === 'job' ? [i] : []))
}

export interface EssentialsGroup { title: string; fields: string[]; toggles?: string; exclude?: string[] }

/** A field reference such as "modeler/dynModels@dydFile[]": element names under <job>, attribute, list flag. */
export interface FieldRef { elements: string[]; attribute: string; list: boolean }

export function parseFieldRef(ref: string): FieldRef {
  const list = ref.endsWith('[]')
  const [path, attribute] = (list ? ref.slice(0, -2) : ref).split('@')
  return { elements: path.split('/').filter(Boolean), attribute, list }
}

/** Spec of the element reached from `spec` through `elements`, or undefined. */
export function specAt(spec: ElementSpec | undefined, elements: string[]): ElementSpec | undefined {
  let current = spec
  for (const name of elements) current = current?.children.find(c => c.name === name)
  return current
}

/** Index of the first `tag` element child, or -1. */
export function childIndex(node: ElementNode, tag: string): number {
  return node.children.findIndex(c => isDynElement(c) && c.tag === tag)
}

/** Path of the first element reached through `elements` from `path`, or null if one is missing. */
export function findPath(root: ElementNode, path: Path, elements: string[]): Path | null {
  let current = path
  for (const name of elements) {
    const index = childIndex(getNode(root, current), name)
    if (index === -1) return null
    current = [...current, index]
  }
  return current
}

/** Indexes of all `tag` element children. */
export function childIndexes(node: ElementNode, tag: string): number[] {
  return node.children.flatMap((c, i) => (isDynElement(c) && c.tag === tag ? [i] : []))
}

export interface SessionFile { name: string; ftype: string | null }

export function elementLabel(schema: Schema, tag: string): string {
  return schema.hints.elements[tag]?.label ?? tag
}

export function attributeLabel(schema: Schema, tag: string, name: string): string {
  return schema.hints.attributes[`${tag}@${name}`]?.label ?? name
}

/** Form.Item status and message of the issues on one attribute. */
export function fieldStatus(issues: Issue[], path: Path, name: string) {
  const own = issuesAt(issues, path).filter(i => i.attribute === name)
  return {
    validateStatus: own.some(i => i.level === 'error') ? 'error' as const : own.length ? 'warning' as const : undefined,
    help: own.length ? own.map(i => i.message).join(' · ') : undefined,
  }
}

/** Session files offered for a file attribute, as references relative to the jobs file. */
export function fileOptions(files: SessionFile[], jobsFile: string, ftype: string | null) {
  return files
    .filter(f => ftype === null || f.ftype === ftype)
    .map(f => ({ value: referenceTo(jobsFile, f.name), label: f.name }))
}
