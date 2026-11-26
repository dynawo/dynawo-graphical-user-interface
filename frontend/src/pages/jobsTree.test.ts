//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { describe, expect, it } from 'vitest'
import {
  addableChildren, canRemove, childIndexes, diffTree, hasChangesUnder, isChanged, findPath, getNode, parseFieldRef, specAt, insertChild, issuesAt, jobIndexes, moveChild,
  parSetSource, referenceTo, removeChild, sessionPath, setAttribute, uniqueName,
  type ElementNode, type ElementSpec,
} from './jobsTree'

const spec = (name: string, min = 1, max: number | null = 1, children: ElementSpec[] = [], ordered = true): ElementSpec =>
  ({ name, min, max, ordered, attributes: [], children, unique: [] })

const modelerSpec = spec('modeler', 1, 1, [
  spec('network', 0, 1), spec('dynModels', 1, null), spec('precompiledModels'), spec('modelicaModels'),
])

const el = (tag: string, children: ElementNode['children'] = [], attrs: Record<string, string> = {}): ElementNode =>
  ({ tag, attrs, children })

const modeler = () => el('modeler', [
  el('dynModels', [], { dydFile: 'a.dyd' }),
  { comment: ' b ' },
  el('dynModels', [], { dydFile: 'b.dyd' }),
  el('precompiledModels'),
  el('modelicaModels'),
])

describe('tree edits', () => {
  it('sets and removes an attribute without touching the original', () => {
    const root = modeler()
    const updated = setAttribute(root, [0], 'dydFile', 'c.dyd')
    expect(getNode(updated, [0]).attrs.dydFile).toBe('c.dyd')
    expect(root.children[0]).toEqual(el('dynModels', [], { dydFile: 'a.dyd' }))
    expect(getNode(setAttribute(root, [0], 'dydFile', undefined), [0]).attrs).toEqual({})
  })

  it('inserts a child in schema order', () => {
    const { root, index } = insertChild(modeler(), [], modelerSpec, el('network'))
    expect(index).toBe(0)
    expect((root.children[0] as ElementNode).tag).toBe('network')
    const more = insertChild(modeler(), [], modelerSpec, el('dynModels'))
    expect(more.index).toBe(3)
  })

  it('appends a child when the order is free', () => {
    const { index } = insertChild(modeler(), [], { ...modelerSpec, ordered: false }, el('network'))
    expect(index).toBe(5)
  })

  it('moves a child past siblings of other kinds', () => {
    const moved = moveChild(modeler(), [], 0, 1)
    expect(getNode(moved, [0]).attrs.dydFile).toBe('b.dyd')
    expect(getNode(moved, [2]).attrs.dydFile).toBe('a.dyd')
    expect(moveChild(modeler(), [], 0, -1)).toEqual(modeler())
  })

  it('removes a child', () => {
    expect(removeChild(modeler(), [], 1).children).toHaveLength(4)
  })
})

describe('what the schema allows', () => {
  it('offers the children still allowed', () => {
    expect(addableChildren(modeler(), modelerSpec).map(s => s.name)).toEqual(['network', 'dynModels'])
  })

  it('keeps the required minimum', () => {
    expect(canRemove(modeler(), modelerSpec, 'dynModels')).toBe(true)
    expect(canRemove(modeler(), modelerSpec, 'precompiledModels')).toBe(false)
    expect(canRemove(modeler(), modelerSpec, 'unknown')).toBe(true)
  })
})

describe('helpers', () => {
  it('finds issues by path', () => {
    const issues = [{ level: 'error' as const, path: [0, 1], message: 'x' }, { level: 'warning' as const, path: [0], message: 'y' }]
    expect(issuesAt(issues, [0, 1]).map(i => i.message)).toEqual(['x'])
  })

  it('gives the set picker to a parId next to a par file', () => {
    const refs = [{ element: 'solver', attribute: 'parFile', ftype: 'par' }]
    expect(parSetSource(refs, 'solver', 'parId')).toBe('parFile')
    expect(parSetSource(refs, 'network', 'parId')).toBeUndefined()
  })

  it('resolves references against the jobs folder', () => {
    expect(sessionPath('case.jobs', 'a.par')).toBe('a.par')
    expect(sessionPath('sub/case.jobs', '../a.par')).toBe('a.par')
    expect(sessionPath('sub/case.jobs', 'par/a.par')).toBe('sub/par/a.par')
  })

  it('writes references relative to the jobs folder', () => {
    expect(referenceTo('case.jobs', 'par/a.par')).toBe('par/a.par')
    expect(referenceTo('sub/case.jobs', 'a.par')).toBe('../a.par')
    expect(referenceTo('sub/case.jobs', 'sub/a.par')).toBe('a.par')
    expect(referenceTo('a/b/case.jobs', 'a/c/x.dyd')).toBe('../c/x.dyd')
  })

  it('makes unique names', () => {
    expect(uniqueName('job', [])).toBe('job')
    expect(uniqueName('job', ['job', 'job2'])).toBe('job3')
  })

  it('lists the jobs of a file', () => {
    expect(jobIndexes(el('jobs', [{ comment: '' }, el('job'), el('job')]))).toEqual([1, 2])
  })
})

describe('field references', () => {
  it('parses a reference', () => {
    expect(parseFieldRef('simulation@stopTime')).toEqual({ elements: ['simulation'], attribute: 'stopTime', list: false })
    expect(parseFieldRef('modeler/dynModels@dydFile[]')).toEqual({ elements: ['modeler', 'dynModels'], attribute: 'dydFile', list: true })
  })

  it('finds the element of a reference, or null when one is missing', () => {
    const job = el('job', [el('solver'), modeler()])
    expect(findPath(job, [], ['modeler', 'dynModels'])).toEqual([1, 0])
    expect(findPath(job, [], ['modeler', 'network'])).toBeNull()
    expect(childIndexes(getNode(job, [1]), 'dynModels')).toEqual([0, 2])
  })

  it('finds the spec of a reference', () => {
    const job = spec('job', 1, null, [modelerSpec])
    expect(specAt(job, ['modeler', 'network'])?.name).toBe('network')
    expect(specAt(job, ['modeler', 'nothing'])).toBeUndefined()
  })
})

describe('diffTree', () => {
  const saved = () => el('job', [el('solver', [], { lib: 'IDA' }), modeler()])

  it('finds nothing in an unchanged tree', () => {
    expect(diffTree(saved(), saved()).count).toBe(0)
  })

  it('marks a changed attribute', () => {
    const edited = setAttribute(saved(), [0], 'lib', 'SIM')
    const diff = diffTree(saved(), edited)
    expect(diff.count).toBe(1)
    expect(isChanged(diff, [0], 'lib')).toBe(true)
    expect(hasChangesUnder(diff, [0])).toBe(true)
    expect(hasChangesUnder(diff, [1])).toBe(false)
  })

  it('counts a removed attribute', () => {
    expect(diffTree(saved(), setAttribute(saved(), [0], 'lib', undefined)).count).toBe(1)
  })

  it('marks an added element without marking its siblings', () => {
    const { root } = insertChild(saved(), [1], modelerSpec, el('network'))
    const diff = diffTree(saved(), root)
    expect(diff.count).toBe(1)
    expect(isChanged(diff, [1, 0])).toBe(true)
    expect(diff.changedAttrs.size).toBe(0)
  })

  it('counts a removed element and its content', () => {
    expect(diffTree(saved(), removeChild(saved(), [], 1)).count).toBe(5)  // modeler + 4 children
  })
})
