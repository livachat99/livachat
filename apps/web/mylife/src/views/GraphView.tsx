import { AnimatePresence, motion } from 'framer-motion'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { useEntity, useGraph } from '../api/hooks'
import type { EntityKind, GraphNode } from '../api/types'
import LifeGraph, { KIND_COLORS } from '../components/LifeGraph'

const KIND_LABELS: Record<EntityKind, string> = {
  event: '事件',
  topic: '主题',
  emotion: '情绪',
  object: '物体',
  app: '应用',
  scene: '场景',
}

export default function GraphView() {
  const navigate = useNavigate()
  const [kinds, setKinds] = useState<Set<EntityKind>>(new Set())
  const [focusId, setFocusId] = useState<string | null>(null)
  const [drawerId, setDrawerId] = useState<string | null>(null)
  const [showList, setShowList] = useState(false)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [minWeight, setMinWeight] = useState(1)

  const typeFilter = kinds.size ? [...kinds].join(',') : undefined
  const graph = useGraph({ focus: focusId ?? undefined, types: typeFilter, dateFrom, dateTo })
  const entity = useEntity(drawerId)

  const toggleKind = (k: EntityKind) => {
    setKinds((prev) => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
    setFocusId(null)
  }

  const onNodeClick = (node: GraphNode) => {
    if (node.kind === 'event' && node.session_id) {
      navigate(`/replay?session=${node.session_id}`)
      return
    }
    setFocusId(focusId === node.id ? null : node.id)
    setDrawerId(node.id)
  }

  const nodes = graph.data?.nodes ?? []
  const edges = graph.data?.edges ?? []
  const topList = useMemo(() => [...nodes].sort((a, b) => b.weight - a.weight).slice(0, 50), [nodes])

  return (
    <div className="relative h-full">
      {/* 顶部控件 */}
      <div className="absolute inset-x-0 top-0 z-10 flex flex-wrap gap-1.5 bg-abyss/80 p-2 backdrop-blur">
        {Object.entries(KIND_LABELS).map(([k, label]) => (
          <button
            key={k}
            onClick={() => toggleKind(k as EntityKind)}
            className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors ${
              kinds.has(k as EntityKind) ? 'bg-panel text-dust line-through opacity-60' : 'bg-panel/70 text-star'
            }`}
          >
            <span className="h-2 w-2 rounded-full" style={{ background: KIND_COLORS[k] }} />
            {label}
          </button>
        ))}
        <span className="mx-1 w-px bg-line" />
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => { setDateFrom(e.target.value); setFocusId(null) }}
          className="rounded bg-panel px-1.5 py-0.5 text-xs text-dust"
        />
        <span className="text-xs text-dust">—</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => { setDateTo(e.target.value); setFocusId(null) }}
          className="rounded bg-panel px-1.5 py-0.5 text-xs text-dust"
        />
        <input
          type="number"
          min={1}
          value={minWeight}
          onChange={(e) => setMinWeight(Number(e.target.value) || 1)}
          className="w-14 rounded bg-panel px-1.5 py-0.5 text-xs text-dust"
          title="min_weight"
        />
        <button
          onClick={() => setShowList(!showList)}
          className="rounded-full bg-panel/70 px-2.5 py-1 text-xs text-star md:hidden"
        >
          {showList ? '星图' : '列表'}
        </button>
        {focusId && (
          <button onClick={() => { setFocusId(null); setDrawerId(null) }} className="rounded-full bg-gold/15 px-2.5 py-1 text-xs text-gold">
            ✕ 退出聚焦
          </button>
        )}
      </div>

      {showList ? (
        <div className="h-full overflow-y-auto pt-14">
          <div className="mx-auto max-w-md space-y-1.5 p-3">
            {topList.map((n) => (
              <button
                key={n.id}
                onClick={() => onNodeClick(n)}
                className="flex w-full items-center gap-2 rounded-lg border border-line bg-panel/70 px-3 py-2 text-left text-sm"
              >
                <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: KIND_COLORS[n.kind] }} />
                <span className="flex-1 truncate text-star">{n.label}</span>
                <span className="text-xs text-dust">{KIND_LABELS[n.kind as EntityKind] ?? n.kind} · {n.weight}</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="h-full pt-14">
          {graph.isLoading ? (
            <div className="flex h-full items-center justify-center text-dust">绘制星图…</div>
          ) : (
            <LifeGraph nodes={nodes} edges={edges} focusId={focusId} onNodeClick={onNodeClick} />
          )}
        </div>
      )}

      {/* 实体详情抽屉 */}
      <AnimatePresence>
        {entity.data && (
          <motion.div
            initial={{ y: 320 }}
            animate={{ y: 0 }}
            exit={{ y: 320 }}
            transition={{ type: 'spring', damping: 30, stiffness: 300 }}
            className="absolute inset-x-0 bottom-0 z-20 max-h-[55%] overflow-y-auto rounded-t-2xl border-t border-line bg-void/95 p-4 pb-safe backdrop-blur"
          >
            <div className="mb-1 flex items-center gap-2">
              <span className="h-3 w-3 rounded-full" style={{ background: KIND_COLORS[entity.data.kind] }} />
              <span className="text-base font-medium text-star">{entity.data.label}</span>
              <span className="text-xs text-dust">{KIND_LABELS[entity.data.kind as EntityKind] ?? entity.data.kind}</span>
              <button onClick={() => setDrawerId(null)} className="ml-auto text-dust">✕</button>
            </div>
            <div className="mb-3 text-xs text-dust">
              权重 {entity.data.weight} · {entity.data.appearances.length} 次出现
              <button onClick={() => setFocusId(focusId === entity.data.id ? null : entity.data.id)} className="ml-2 rounded bg-gold/15 px-2 py-0.5 text-gold">
                {focusId === entity.data.id ? '退出聚焦' : '聚焦关系'}
              </button>
            </div>
            <div className="space-y-1.5">
              {entity.data.appearances.map((a) => (
                <button
                  key={a.session_id}
                  onClick={() => navigate(`/timeline?date=${a.session_date}&session=${a.session_id}`)}
                  className="w-full rounded-lg border border-line bg-panel/60 p-2.5 text-left"
                >
                  <div className="text-xs font-medium text-gold">{a.session_date}</div>
                  {a.snippet && <div className="mt-0.5 text-xs text-dust line-clamp-2">{a.snippet}</div>}
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
