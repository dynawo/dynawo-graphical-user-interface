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
import { Alert, Button, Card, Space, Tag, Typography } from 'antd'
import { SafetyCertificateOutlined, VerticalAlignTopOutlined } from '@ant-design/icons'
import client from '../api/client'
import { errorDetail } from '../api/errors'

const { Text } = Typography

// Compatibility of a job's files with the selected Dynawo, without running it,
// and their upgrade with Dynawo's update tool (backend/compatibility.py).

interface Context {
  dynawo_version: string
  update_tool: boolean
  update_tool_missing: string | null
  update_tool_path: string | null
  update_tool_latest: string | null
}
interface Issue { level: 'error' | 'warning' | 'info'; message: string; count: number; examples: string[] }
interface CheckResult {
  models: number
  libraries: number
  parameter_sets: number
  issues: Issue[]
  // What applying all of Dynawo's updates up to the selected version changes.
  migration: { changed: string[]; tickets: string[] } | null
  versions: 'checked' | 'no_tool' | 'error'
  versions_detail?: string
}
interface UpgradeResult { jobs_file: string; tickets: string[]; dynawo_version: string }

const LEVEL_COLOR = { error: 'red', warning: 'orange', info: 'default' } as const

// onUpgraded: the parent selects the new job, which remounts this card; it shows
// the result again through `notice`.
export default function CompatibilityCheck({ jobsFile, notice, onUpgraded }: {
  jobsFile: string
  notice?: string | null
  onUpgraded: (jobsFile: string, notice: string) => void
}) {
  const [context, setContext] = useState<Context | null>(null)
  const [result, setResult] = useState<CheckResult | null>(null)
  const [busy, setBusy] = useState<'check' | 'upgrade' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    client.get<Context>('/compatibility/context', { params: { jobs_file: jobsFile } })
      .then(res => setContext(res.data))
      .catch(err => setError(errorDetail(err, 'Could not read the compatibility context')))
  }, [jobsFile])

  const check = () => {
    setBusy('check'); setError(null); setResult(null)
    client.post<CheckResult>('/compatibility/check', { jobs_file: jobsFile })
      .then(res => setResult(res.data))
      .catch(err => setError(errorDetail(err, 'The check failed')))
      .finally(() => setBusy(null))
  }
  const upgrade = () => {
    setBusy('upgrade'); setError(null)
    client.post<UpgradeResult>('/compatibility/upgrade', { jobs_file: jobsFile })
      .then(res => onUpgraded(res.data.jobs_file,
        `Upgraded to Dynawo ${res.data.dynawo_version} (all updates, ${res.data.tickets.length} ticket(s)): new job `
        + `${res.data.jobs_file}, now selected. The original files are unchanged.`))
      .catch(err => setError(errorDetail(err, 'The upgrade failed')))
      .finally(() => setBusy(null))
  }

  if (!context) return error ? <Alert type="error" showIcon description={error} style={{ marginBottom: 16 }} /> : null
  const target = context.dynawo_version
  const migration = result?.migration
  const needsUpgrade = !!migration?.changed.length
  const problems = result?.issues.filter(i => i.level !== 'info').length ?? 0
  const allClear = result?.versions === 'checked' && !problems && !needsUpgrade

  return (
    <Card size="small" title={`Compatibility with Dynawo ${target}`} style={{ marginBottom: 16 }}>
      <Space orientation="vertical" style={{ width: '100%' }} size={10}>
        <Space wrap>
          <Button icon={<SafetyCertificateOutlined />} loading={busy === 'check'} onClick={check}>Check</Button>
          <Button icon={<VerticalAlignTopOutlined />} loading={busy === 'upgrade'} onClick={upgrade}
            title={`Applies all of Dynawo's updates up to ${target}; files already in that format are not changed`}>
            Upgrade to Dynawo {target}
          </Button>
        </Space>
        {context.update_tool ? (
          <Text type="secondary">
            Update tool: <Text code>{context.update_tool_path}</Text>, scripts up to {context.update_tool_latest}. It must be
            at least as recent as the selected Dynawo.
          </Text>
        ) : <Text type="secondary">Upgrade not available: {context.update_tool_missing}.</Text>}
        {notice && <Alert type="success" showIcon description={notice} />}
        {error && <Alert type="error" showIcon description={error} />}

        {result && (<>
          <Text type="secondary">
            Checked against the model database of Dynawo {target}: {result.models} model(s), {result.libraries} library(ies),{' '}
            {result.parameter_sets} parameter set(s).
          </Text>
          {result.issues.map((issue, i) => (
            <div key={i}>
              <Tag color={LEVEL_COLOR[issue.level]}>{issue.level}</Tag>
              <Text>{issue.count > 1 ? `${issue.count} × ` : ''}{issue.message}</Text>
              {issue.examples.length > 0 && <Text type="secondary"> — e.g. {issue.examples.join(', ')}</Text>}
            </div>
          ))}
          {migration && (needsUpgrade ? (
            <Alert type="warning" showIcon description={<>
              Applying Dynawo's updates up to {target} changes {migration.changed.join(', ')} ({migration.tickets.length}
              {' '}ticket(s)): the files are not in Dynawo {target} format. Upgrade them before running.
            </>} />
          ) : <Alert type="success" showIcon description={`Applying Dynawo's updates up to ${target} changes nothing: the files are in Dynawo ${target} format.`} />)}
          {result.versions === 'no_tool' && (
            <Alert type="warning" showIcon description={`The changes between Dynawo versions could not be checked: ${result.versions_detail}. The check against the model database alone cannot see every incompatibility (e.g. variables of C++ models).`} />
          )}
          {result.versions === 'error' && <Alert type="error" showIcon description={`Update tool: ${result.versions_detail}`} />}
          {allClear && <Alert type="success" showIcon description="No incompatibility found." />}
        </>)}
      </Space>
    </Card>
  )
}
