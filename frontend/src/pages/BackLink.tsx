//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useLocation, useNavigate } from 'react-router-dom'
import { Button } from 'antd'
import { ArrowLeftOutlined } from '@ant-design/icons'

/** Link back to the page that opened this one, when it passed `state.back`. */
export default function BackLink() {
  const navigate = useNavigate()
  const back = (useLocation().state as { back?: string } | null)?.back
  if (!back) return null
  return (
    <Button type="link" icon={<ArrowLeftOutlined />} style={{ paddingLeft: 0 }} onClick={() => navigate(back)}>
      Back to Simulation Settings
    </Button>
  )
}
