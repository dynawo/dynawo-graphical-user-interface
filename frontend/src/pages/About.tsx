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
  EditOutlined,
  LineChartOutlined,
  PlayCircleOutlined,
  SettingOutlined,
  ToolOutlined,
} from '@ant-design/icons'

const { Title, Paragraph } = Typography

export default function About() {
  return (
    <div style={{ maxWidth: 640 }}>
      <img src="/Dynawo-Logo-Color.png" alt="Dynawo logo" style={{ width: 300, marginBottom: 24 }} />
      <Title level={2}>Dynawo GUI</Title>
      <Paragraph type="secondary">
        A graphical interface for setting up, running, and analysing Dynawo power-system simulations.
      </Paragraph>
      <Divider />
      <Title level={4}>Workflow</Title>
      <Steps
        orientation="vertical"
        items={[
          {
            title: 'Dynawo Version',
            icon: <SettingOutlined />,
            description: 'Configure or download the Dynawo executable.',
            status: 'process',
          },
          {
            title: 'Upload Files',
            icon: <CloudUploadOutlined />,
            description: 'Load your .iidm, .dyd, .par, and .jobs files.',
            status: 'process',
          },
          {
            title: 'Network View',
            icon: <ApartmentOutlined />,
            description: 'Explore the network diagram and voltage levels.',
            status: 'process',
          },
          {
            title: 'Edit Parameters',
            icon: <EditOutlined />,
            description: 'Adjust dynamic model parameters before running.',
            status: 'process',
          },
          {
            title: 'Edit Solver Parameters',
            icon: <ToolOutlined />,
            description: 'Tune the numerical solver settings.',
            status: 'process',
          },
          {
            title: 'Edit Curves',
            icon: <LineChartOutlined />,
            description: 'Select which model variables Dynawo will export to curves.csv.',
            status: 'process',
          },
          {
            title: 'Run Simulation',
            icon: <PlayCircleOutlined />,
            description: 'Launch the simulation and compare output curves across runs.',
            status: 'process',
          },
        ]}
      />
    </div>
  )
}
