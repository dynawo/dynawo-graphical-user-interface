//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { describe, expect, it, vi } from 'vitest'
import { errorDetail, errorStatus, isTransient, retryTransient } from './errors'

const httpError = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } })

describe('errorDetail', () => {
  it("reads FastAPI's detail", () => {
    expect(errorDetail(httpError(404, 'case.par not found'), 'fallback')).toBe('case.par not found')
  })

  it('falls back when there is no readable detail', () => {
    expect(errorDetail(httpError(422, [{ msg: 'field required' }]), 'fallback')).toBe('fallback')
    expect(errorDetail(new Error('network'), 'fallback')).toBe('fallback')
    expect(errorDetail(undefined, 'fallback')).toBe('fallback')
  })
})

describe('isTransient', () => {
  it.each([502, 503, 504])('a %s from the proxy may pass', status => {
    expect(isTransient(httpError(status))).toBe(true)
  })

  it('a request that got no response at all may pass', () => {
    expect(errorStatus(new Error('ECONNREFUSED'))).toBeUndefined()
    expect(isTransient(new Error('ECONNREFUSED'))).toBe(true)
  })

  it.each([400, 404, 500])('a %s is a real answer', status => {
    expect(isTransient(httpError(status))).toBe(false)
  })
})

describe('retryTransient', () => {
  it('does not retry what succeeds', async () => {
    const fn = vi.fn().mockResolvedValue('ok')

    await expect(retryTransient(fn, 0)).resolves.toBe('ok')
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('retries once after a transient failure', async () => {
    const fn = vi.fn().mockRejectedValueOnce(httpError(503)).mockResolvedValue('ok')

    await expect(retryTransient(fn, 0)).resolves.toBe('ok')
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('does not retry a real error', async () => {
    const fn = vi.fn().mockRejectedValue(httpError(404, 'not found'))

    await expect(retryTransient(fn, 0)).rejects.toEqual(httpError(404, 'not found'))
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('gives up after the second attempt', async () => {
    const fn = vi.fn().mockRejectedValue(httpError(503))

    await expect(retryTransient(fn, 0)).rejects.toEqual(httpError(503))
    expect(fn).toHaveBeenCalledTimes(2)
  })
})
