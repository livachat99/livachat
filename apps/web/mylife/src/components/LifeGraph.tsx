import * as d3Force from 'd3-force'
import { scaleSqrt } from 'd3-scale'
import { useEffect, useMemo, useRef } from 'react'
import type { GraphEdge, GraphNode } from '../api/types'

export const KIND_COLORS: Record<string, string> = {
  topic: '#f5c86e',
  emotion: '#f28bb0',
  app: '#7db4f5',
  event: '#e8ecff',
  object: '#b58cf5',
  scene: '#6fd6d0',
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focusId: string | null
  onNodeClick: (node: GraphNode) => void
}

interface SimNode extends GraphNode {
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number | null
  fy?: number | null
}

interface SimLink extends d3Force.SimulationLinkDatum<SimNode> {
  source: string | SimNode
  target: string | SimNode
}

export default function LifeGraph({ nodes, edges, focusId, onNodeClick }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const simRef = useRef<d3Force.Simulation<SimNode, undefined> | null>(null)
  const nodesRef = useRef<SimNode[]>([])
  const hoverRef = useRef<SimNode | null>(null)
  const dragRef = useRef<{ node: SimNode; dx: number; dy: number } | null>(null)
  const transformRef = useRef({ x: 0, y: 0, k: 1 })

  const links = useMemo<SimLink[]>(
    () => edges.map((e) => ({ source: e.source, target: e.target })),
    [edges],
  )

  const radius = useMemo(
    () => scaleSqrt<number, number>().domain([1, Math.max(...nodes.map((n) => n.weight), 1)]).range([4, 22]),
    [nodes],
  )

  // 邻居集（focus 高亮）
  const neighborIds = useMemo(() => {
    if (!focusId) return new Set<string>()
    const set = new Set<string>([focusId])
    for (const e of edges) {
      if (e.source === focusId) set.add(e.target)
      if (e.target === focusId) set.add(e.source)
    }
    return set
  }, [focusId, edges])

  useEffect(() => {
    const canvas = canvasRef.current
    const wrap = wrapRef.current
    if (!canvas || !wrap) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    let sim: d3Force.Simulation<SimNode, undefined> | null = null
    const resize = () => {
      canvas.width = wrap.clientWidth * dpr
      canvas.height = wrap.clientHeight * dpr
      canvas.style.width = `${wrap.clientWidth}px`
      canvas.style.height = `${wrap.clientHeight}px`
      if (sim) {
        sim.force('center', d3Force.forceCenter(canvas.width / dpr / 2, canvas.height / dpr / 2))
        sim.alpha(0.2).restart()
      }
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(wrap)

    // simulation
    const simNodes: SimNode[] = nodes.map((n) => ({ ...n }))
    nodesRef.current = simNodes
    sim = d3Force
      .forceSimulation<SimNode>(simNodes)
      .force('link', d3Force.forceLink<SimNode, SimLink>(links).id((d: SimNode) => d.id).distance(70).strength(0.4))
      .force('charge', d3Force.forceManyBody<SimNode>().strength((d: SimNode) => -60 - d.weight * 6))
      .force('center', d3Force.forceCenter(canvas.width / dpr / 2, canvas.height / dpr / 2))
      .force('collide', d3Force.forceCollide<SimNode>().radius((d: SimNode) => radius(d.weight) + 6))
      .alphaDecay(0.05)
    simRef.current = sim

    const t = transformRef.current
    let raf = 0

    const draw = () => {
      const w = canvas.width / dpr
      const h = canvas.height / dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      ctx.save()
      ctx.translate(t.x, t.y)
      ctx.scale(t.k, t.k)

      const dim = (n: SimNode) => (focusId && !neighborIds.has(n.id) ? 0.1 : 1)

      ctx.lineWidth = 1 / t.k
      for (const l of links) {
        const s = l.source as SimNode
        const tg = l.target as SimNode
        ctx.strokeStyle = focusId && (!neighborIds.has(s.id) || !neighborIds.has(tg.id)) ? 'rgba(139,147,184,0.05)' : 'rgba(139,147,184,0.25)'
        ctx.beginPath()
        ctx.moveTo(s.x ?? 0, s.y ?? 0)
        ctx.lineTo(tg.x ?? 0, tg.y ?? 0)
        ctx.stroke()
      }

      for (const n of simNodes) {
        const r = radius(n.weight)
        ctx.globalAlpha = dim(n)
        ctx.fillStyle = KIND_COLORS[n.kind] ?? '#8b93b8'
        ctx.beginPath()
        ctx.arc(n.x ?? 0, n.y ?? 0, r, 0, Math.PI * 2)
        ctx.fill()
        if (n.id === focusId) {
          ctx.strokeStyle = '#ffffff'
          ctx.lineWidth = 1.5 / t.k
          ctx.beginPath()
          ctx.arc(n.x ?? 0, n.y ?? 0, r + 4, 0, Math.PI * 2)
          ctx.stroke()
        }
        ctx.globalAlpha = 1
      }

      if (hoverRef.current) {
        const n = hoverRef.current
        ctx.globalAlpha = 0.9
        ctx.fillStyle = '#0d1220'
        const text = `${n.label} (${n.weight})`
        ctx.font = '12px sans-serif'
        const tw = ctx.measureText(text).width
        ctx.fillRect((n.x ?? 0) + 10, (n.y ?? 0) - 20, tw + 12, 22)
        ctx.strokeStyle = 'rgba(139,147,184,0.5)'
        ctx.strokeRect((n.x ?? 0) + 10, (n.y ?? 0) - 20, tw + 12, 22)
        ctx.fillStyle = '#e8ecff'
        ctx.fillText(text, (n.x ?? 0) + 16, (n.y ?? 0) - 5)
        ctx.globalAlpha = 1
      }
      ctx.restore()
    }

    const loop = () => {
      draw()
      raf = requestAnimationFrame(loop)
    }
    loop()

    // wheel zoom
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const k2 = e.deltaY < 0 ? 1.1 : 1 / 1.1
      const k = Math.min(4, Math.max(0.2, t.k * k2))
      t.x = mx - ((mx - t.x) * k) / t.k
      t.y = my - ((my - t.y) * k) / t.k
      t.k = k
    }
    canvas.addEventListener('wheel', onWheel, { passive: false })

    const hitTest = (mx: number, my: number): SimNode | null => {
      const wx = (mx - t.x) / t.k
      const wy = (my - t.y) / t.k
      let best: SimNode | null = null
      let bestD = 14
      for (const n of simNodes) {
        const dx = (n.x ?? 0) - wx
        const dy = (n.y ?? 0) - wy
        const d = Math.hypot(dx, dy)
        if (d < bestD) {
          bestD = d
          best = n
        }
      }
      return best
    }

    const onPointerDown = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      const node = hitTest(e.clientX - rect.left, e.clientY - rect.top)
      if (node) {
        dragRef.current = {
          node,
          dx: (e.clientX - rect.left - t.x - (node.x ?? 0) * t.k) / t.k,
          dy: (e.clientY - rect.top - t.y - (node.y ?? 0) * t.k) / t.k,
        }
        canvas.setPointerCapture(e.pointerId)
      }
    }
    const onPointerMove = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const drag = dragRef.current
      if (drag) {
        drag.node.fx = (mx - t.x) / t.k - drag.dx
        drag.node.fy = (my - t.y) / t.k - drag.dy
        return
      }
      hoverRef.current = hitTest(mx, my)
      canvas.style.cursor = hoverRef.current ? 'pointer' : 'grab'
    }
    const onPointerUp = (e: PointerEvent) => {
      const drag = dragRef.current
      if (drag) {
        const rect = canvas.getBoundingClientRect()
        const moved = Math.hypot(e.clientX - rect.left - t.x - (drag.node.x ?? 0) * t.k, e.clientY - rect.top - t.y - (drag.node.y ?? 0) * t.k)
        drag.node.fx = null
        drag.node.fy = null
        dragRef.current = null
        if (moved < 6) {
          const node = nodesRef.current.find((n) => n.id === drag.node.id)
          if (node) onNodeClick(node)
        }
      }
    }
    canvas.addEventListener('pointerdown', onPointerDown)
    canvas.addEventListener('pointermove', onPointerMove)
    canvas.addEventListener('pointerup', onPointerUp)

    // pinch zoom
    let pinch: { d: number; k: number } | null = null
    const onTouch = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        e.preventDefault()
        const [a, b] = [e.touches[0], e.touches[1]]
        const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)
        if (!pinch) pinch = { d, k: t.k }
        else {
          t.k = Math.min(4, Math.max(0.2, (pinch.k * d) / pinch.d))
        }
      } else if (e.touches.length !== 2) {
        pinch = null
      }
    }
    canvas.addEventListener('touchmove', onTouch, { passive: false })

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      sim.stop()
      canvas.removeEventListener('wheel', onWheel)
      canvas.removeEventListener('pointerdown', onPointerDown)
      canvas.removeEventListener('pointermove', onPointerMove)
      canvas.removeEventListener('pointerup', onPointerUp)
      canvas.removeEventListener('touchmove', onTouch)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, links])

  return (
    <div ref={wrapRef} className="absolute inset-0">
      <canvas ref={canvasRef} className="h-full w-full touch-none" />
    </div>
  )
}
