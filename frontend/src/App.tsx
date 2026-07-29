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
import { BrowserRouter, useLocation, useNavigate, Route, Routes } from 'react-router-dom'
import { ConfigProvider, Layout, Menu, Button, Popconfirm, theme, Tooltip, Typography } from 'antd'
import { ApartmentOutlined, ClearOutlined, CloudUploadOutlined, DownloadOutlined, EditOutlined, InfoCircleOutlined, LineChartOutlined, MoonOutlined, PlayCircleOutlined, SettingOutlined, SunOutlined, ThunderboltOutlined, ToolOutlined } from '@ant-design/icons'
import client from './api/client'

// Module-level promise — created once regardless of StrictMode double-mount.
// Ensures exactly one session-init request is made on app startup.
let _sessionInit: Promise<void> | null = null
function initSession() {
  if (!_sessionInit) _sessionInit = client.get('/files/').then(() => {})
  return _sessionInit
}
import About from './pages/About'
import Upload from './pages/Upload'
import DynawoVersion from './pages/DynawoVersion'
import EditCurves from './pages/EditCurves'
import EditParameters from './pages/EditParameters'
import EditSolverParameters from './pages/EditSolverParameters'
import LoadFlow from './pages/LoadFlow'
import NetworkView from './pages/NetworkView'
import RunSimulation from './pages/RunSimulation'

const { Sider, Content, Header } = Layout

const SHARED_TOKENS = {
  colorPrimary: '#0050B3',
  borderRadius: 6,
  fontFamily: '"Inter", system-ui, -apple-system, sans-serif',
  fontSize: 14,
}

const LIGHT_THEME = {
  algorithm: theme.defaultAlgorithm,
  token: { ...SHARED_TOKENS },
}

const DARK_THEME = {
  algorithm: theme.darkAlgorithm,
  token: { ...SHARED_TOKENS, colorBgContainer: '#1c1c1e' },
}

const NAV_ITEMS = [
  { key: '/',                       label: 'Upload Files',          icon: <CloudUploadOutlined /> },
  { key: '/dynawo-version',         label: 'Dynawo Version',        icon: <SettingOutlined /> },
  { key: '/network-view',           label: 'Network View',          icon: <ApartmentOutlined /> },
  { key: '/load-flow',              label: 'Load Flow / DynaFlow',  icon: <ThunderboltOutlined /> },
  { key: '/edit-parameters',        label: 'Edit Parameters',       icon: <EditOutlined /> },
  { key: '/edit-solver-parameters', label: 'Edit Solver Params',    icon: <ToolOutlined /> },
  { key: '/edit-curves',            label: 'Edit Curves',           icon: <LineChartOutlined /> },
  { key: '/run-simulation',         label: 'Run Simulation',        icon: <PlayCircleOutlined /> },
  { key: '/about',                  label: 'About',                 icon: <InfoCircleOutlined /> },
]

function AppLayout({ isDark, toggleTheme }: { isDark: boolean; toggleTheme: () => void }) {
  const location = useLocation()
  const navigate = useNavigate()
  const { token } = theme.useToken()

  const headerBg   = isDark ? '#141414' : '#ffffff'
  const headerBorder = isDark ? '#303030' : '#f0f0f0'
  const contentBg  = isDark ? '#0d0d0d' : '#f0f2f5'

  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      <Sider theme="dark" width={220} style={{ borderRight: '1px solid #1f1f1f', overflow: 'hidden' }}>
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div style={{ padding: '16px 24px', borderBottom: '1px solid #303030', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
            <img src="/dynawo-icon.png" alt="Dynawo" style={{ height: 28, width: 28 }} />
            <Typography.Text strong style={{ color: '#fff', fontSize: 15, letterSpacing: 0.3 }}>
              Dynawo GUI
            </Typography.Text>
          </div>
          <div style={{ flex: 1, overflow: 'auto' }}>
            <Menu
              theme="dark"
              mode="inline"
              selectedKeys={[location.pathname]}
              items={NAV_ITEMS}
              onClick={({ key }) => navigate(key)}
              style={{ marginTop: 8, borderRight: 0 }}
            />
          </div>
          <div style={{ padding: '12px', borderTop: '1px solid #303030', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Button
              block
              icon={<DownloadOutlined />}
              style={{ color: '#888', borderColor: '#333', background: 'transparent' }}
              onClick={() => { window.location.href = '/api/files/download' }}
            >
              Download Files
            </Button>
            <Popconfirm
              title="Clear session cache?"
              description="All uploaded files and simulation history will be deleted."
              okText="Clear"
              okButtonProps={{ danger: true }}
              cancelText="Cancel"
              onConfirm={async () => {
                try { await client.delete('/auth/session') } catch { /* already expired */ }
                try { await client.get('/files/') }         catch { /* pre-init new session */ }
                window.location.reload()
              }}
            >
              <Button
                block
                icon={<ClearOutlined />}
                style={{ color: '#888', borderColor: '#333', background: 'transparent' }}
              >
                Clear Cache
              </Button>
            </Popconfirm>
          </div>
        </div>
      </Sider>

      <Layout style={{ overflow: 'hidden' }}>
        <Header style={{
          background: headerBg,
          padding: '0 24px',
          borderBottom: `1px solid ${headerBorder}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {NAV_ITEMS.find(i => i.key === location.pathname)?.label ?? ''}
          </Typography.Text>
          <Tooltip title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}>
            <Button
              type="text"
              icon={isDark ? <SunOutlined /> : <MoonOutlined />}
              onClick={toggleTheme}
              style={{ color: token.colorTextSecondary }}
            />
          </Tooltip>
        </Header>

        <Content style={{ padding: 24, background: contentBg, overflow: 'auto' }}>
          <Routes>
            <Route path="/" element={<Upload />} />
            <Route path="/dynawo-version" element={<DynawoVersion />} />
            <Route path="/network-view" element={<NetworkView />} />
            <Route path="/load-flow" element={<LoadFlow />} />
            <Route path="/edit-parameters" element={<EditParameters />} />
            <Route path="/edit-solver-parameters" element={<EditSolverParameters />} />
            <Route path="/edit-curves" element={<EditCurves />} />
            <Route path="/run-simulation" element={<RunSimulation />} />
            <Route path="/about" element={<About />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  const saved = localStorage.getItem('theme')
  const [isDark, setIsDark] = useState(saved === 'dark')
  const [sessionReady, setSessionReady] = useState(false)

  const toggleTheme = () => {
    setIsDark(prev => {
      const next = !prev
      localStorage.setItem('theme', next ? 'dark' : 'light')
      return next
    })
  }

  // Initialise the session with a single request before any route component
  // mounts — prevents multiple simultaneous calls from each creating their
  // own session and leaving orphaned temp dirs in /tmp.
  useEffect(() => {
    initSession().finally(() => setSessionReady(true))
  }, [])

  if (!sessionReady) return null

  return (
    <ConfigProvider theme={isDark ? DARK_THEME : LIGHT_THEME}>
      <BrowserRouter>
        <AppLayout isDark={isDark} toggleTheme={toggleTheme} />
      </BrowserRouter>
    </ConfigProvider>
  )
}
