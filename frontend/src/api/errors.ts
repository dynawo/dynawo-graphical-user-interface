//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

// A rejected request arrives as `unknown`. These read the two parts of it the
// pages react to — FastAPI's `detail` and the status code — without typing the
// error as `any`, which would also hide a typo in either path.
interface ApiError {
  response?: {
    status?: number
    data?: { detail?: string }
  }
}

export function errorDetail(err: unknown, fallback: string): string {
  const detail = (err as ApiError)?.response?.data?.detail
  return typeof detail === 'string' ? detail : fallback
}

export function errorStatus(err: unknown): number | undefined {
  return (err as ApiError)?.response?.status
}

// A request that never reached the API — the backend restarting under --reload,
// or the dev proxy with nothing behind it. There is no response to read a
// `detail` from, and the right answer is usually to try again rather than to
// tell the user something is wrong with their session.
export function isTransient(err: unknown): boolean {
  const status = errorStatus(err)
  return status === undefined || status === 502 || status === 503 || status === 504
}

/** Run `fn`, retrying once after `delayMs` if it fails in a way that may pass. */
export async function retryTransient<T>(fn: () => Promise<T>, delayMs = 700): Promise<T> {
  try {
    return await fn()
  } catch (err) {
    if (!isTransient(err)) throw err
    await new Promise(resolve => setTimeout(resolve, delayMs))
    return fn()
  }
}
