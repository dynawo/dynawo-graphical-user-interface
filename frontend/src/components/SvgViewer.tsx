//
// Copyright (c) 2026, RTE (http://www.rte-france.com)
// See AUTHORS.txt
// All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, you can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0
//

import { useEffect, useRef, forwardRef, useImperativeHandle } from 'react'

export interface SvgViewerHandle {
  getSvgElement: () => SVGSVGElement | null
}

interface Props {
  svg: string
  height?: number
  onSvgReady?: (el: SVGSVGElement) => void
}

const SvgViewer = forwardRef<SvgViewerHandle, Props>(({ svg, height = 700, onSvgReady }, ref) => {
  const containerRef = useRef<HTMLDivElement>(null)

  useImperativeHandle(ref, () => ({
    getSvgElement: () => containerRef.current?.querySelector('svg') ?? null,
  }))

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    container.innerHTML = svg

    const el = container.querySelector('svg') as SVGSVGElement
    if (!el) return

    el.removeAttribute('width')
    el.removeAttribute('height')
    el.style.cssText = `width:100%;height:${height}px;display:block;cursor:grab;`

    // Wrap all non-defs/style children in a transform group
    const wrapper = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    Array.from(el.children).forEach(c => {
      if (c.tagName !== 'defs' && c.tagName !== 'style') wrapper.appendChild(c)
    })
    el.appendChild(wrapper)

    let scale = 1, tx = 0, ty = 0, dragging = false, startX = 0, startY = 0
    let rafId: number | null = null

    // Coalesce all within-frame position changes into one DOM write
    function scheduleUpdate() {
      if (rafId !== null) return
      rafId = requestAnimationFrame(() => {
        rafId = null
        wrapper.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`)
      })
    }

    function onWheel(e: WheelEvent) {
      e.preventDefault()
      const f = e.deltaY < 0 ? 1.15 : 1 / 1.15
      const r = el.getBoundingClientRect()
      const mx = e.clientX - r.left, my = e.clientY - r.top
      tx = mx + f * (tx - mx)
      ty = my + f * (ty - my)
      scale *= f
      scheduleUpdate()
    }
    function onMouseDown(e: MouseEvent) {
      dragging = true; startX = e.clientX - tx; startY = e.clientY - ty
      el.style.cursor = 'grabbing'
    }
    function onMouseMove(e: MouseEvent) {
      if (!dragging) return
      tx = e.clientX - startX; ty = e.clientY - startY
      scheduleUpdate()
    }
    function onMouseUp() { dragging = false; el.style.cursor = 'grab' }
    function onDblClick() { scale = 1; tx = 0; ty = 0; scheduleUpdate() }

    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('mousedown', onMouseDown)
    el.addEventListener('mousemove', onMouseMove)
    el.addEventListener('mouseup', onMouseUp)
    el.addEventListener('mouseleave', onMouseUp)
    el.addEventListener('dblclick', onDblClick)

    onSvgReady?.(el)

    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('mousedown', onMouseDown)
      el.removeEventListener('mousemove', onMouseMove)
      el.removeEventListener('mouseup', onMouseUp)
      el.removeEventListener('mouseleave', onMouseUp)
      el.removeEventListener('dblclick', onDblClick)
    }
  }, [svg, height])

  return <div ref={containerRef} style={{ height, overflow: 'hidden', background: '#fafafa', borderRadius: 6 }} />
})

SvgViewer.displayName = 'SvgViewer'
export default SvgViewer
