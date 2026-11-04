//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { Divider, Steps, Typography } from 'antd'
import {
  ApartmentOutlined,
  CloudUploadOutlined,
  ClusterOutlined,
  EditOutlined,
  LineChartOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  WarningOutlined,
} from '@ant-design/icons'

const { Title, Paragraph, Text } = Typography

// The pages in workflow order, with the sidebar's icons.
const STEPS = [
  {
    title: 'Dynawo Version',
    icon: <SettingOutlined />,
    description: 'Set the Dynawo executable, or download a Dynawo release.',
  },
  {
    title: 'Upload Files',
    icon: <CloudUploadOutlined />,
    description: 'Load a case (.jobs, .dyd, .par, .iidm, .crv…) or a network alone; the files a job refers to '
      + 'can be loaded from a folder or a ZIP archive.',
  },
  {
    title: 'Network View',
    icon: <ApartmentOutlined />,
    description: 'Explore the network (area and single-line diagrams, with the dynamic models) and compare '
      + 'two states of it.',
  },
  {
    title: 'Load Flow / DynaFlow',
    icon: <ThunderboltOutlined />,
    description: 'Compute a starting point with OpenLoadFlow or DynaFlow, and run an N-1 security analysis.',
  },
  {
    title: 'Dynamic Association',
    icon: <ClusterOutlined />,
    description: 'Put dynamic models and automation systems on the network elements with rules, and generate '
      + 'the Dynawo job with PowSyBl; rebuild rules from a .dyd, save them, export a Python script.',
  },
  {
    title: 'Edit Parameters',
    icon: <EditOutlined />,
    description: 'Edit the parameters of the dynamic models and of the network, fill new sets, read values '
      + 'from the network (IIDM references).',
  },
  {
    title: 'Edit Solver Parameters',
    icon: <ToolOutlined />,
    description: 'Tune the solver settings of a job.',
  },
  {
    title: 'Edit Curves',
    icon: <LineChartOutlined />,
    description: 'Choose the model variables Dynawo exports to curves.csv.',
  },
  {
    title: 'Events',
    icon: <WarningOutlined />,
    description: 'Add events, such as disconnections and connections, to the simulation.',
  },
  {
    title: 'Run Simulation',
    icon: <PlayCircleOutlined />,
    description: 'Check a job against the selected Dynawo and upgrade it if needed, then run it and compare '
      + 'the curves of several runs.',
  },
]

export default function About() {
  return (
    <div style={{ maxWidth: 640 }}>
      <img src="/Dynawo-Logo-Color.png" alt="Dynawo logo" style={{ width: 300, marginBottom: 24 }} />
      <Title level={2}>Dynawo GUI</Title>
      <Paragraph type="secondary">
        A graphical interface for setting up, running, and analysing Dynawo power-system simulations.
      </Paragraph>
      <Paragraph>
        Start from an existing Dynawo case, or from a network alone: <Text strong>Dynamic Association</Text> then
        builds its dynamic models. Optional pages can be skipped.
      </Paragraph>
      <Divider />
      <Title level={4}>Workflow</Title>
      <Steps orientation="vertical" items={STEPS.map(step => ({ ...step, status: 'process' as const }))} />
      <Divider />
      <Paragraph type="secondary">
        Each browser tab has its own session, which expires after one hour of inactivity.{' '}
        <Text strong>Download Files</Text>, in the sidebar, gives all the files of the session and the run outputs as
        a ZIP; <Text strong>Clear Cache</Text> deletes the session and starts a new one.
      </Paragraph>
    </div>
  )
}
