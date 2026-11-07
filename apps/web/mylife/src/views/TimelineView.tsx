import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import { useOverview, useTimeline } from '../api/hooks'
import type { EventType, SessionItem, TimelineDay } from '../api/types'
import EventCard from '../components/EventCard'
import HeatStrip from '../components/HeatStrip'

const TYPE_FILTERS: { key: EventType | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'chat', label: '对话' },
  { key: 'audio', label: '语音' },
  { key: 'video', label: '画面' },
  { key: 'window', label: '窗口' },
  { key: 'agent_job', label: '任务' },
]

function fmtDay(day: { date: string }): { day: string; month: string; year: string } {
  const [y, m, d] = day.date.split('-')
  return { day: `${m}月${d}日`, month: `${m}月`, year: `${y}年` }
}

/** 按 YYYY-MM 分组（保持输入顺序）——月份分隔符由分组结构推导，渲染期无共享可变状态。 */
function groupByMonth<T extends { date: string }>(days: T[]): { month: string; days: T[] }[] {
  const groups: { month: string; days: T[] }[] = []
  for (const d of days) {
    const m = d.date.slice(0, 7)
    const last = groups[groups.length - 1]
    if (last && last.month === m) last.days.push(d)
    else groups.push({ month: m, days: [d] })
  }
  return groups
}

function SessionBlock({ s }: { s: SessionItem }) {
  const navigate = useNavigate()
  const pending = s.analysis.timeline === 'pending'
  return (
    <button
      onClick={() => !pending && navigate(`/replay?session=${s.session_id}`)}
      disabled={pending}
      className="w-full rounded-xl border border-line bg-panel/60 p-3 text-left transition-colors hover:border-gold/40"
    >
      {pending ? (
        <div className="flex items-center gap-2 text-sm text-dust">
          <span className="h-2 w-2 animate-pulse rounded-full bg-gold" /> 分析中…
        </div>
      ) : (
        <>
          <div className="mb-1 flex flex-wrap items-center gap-1.5 text-xs">
            {s.stats.audio_segments != null && s.stats.audio_segments > 0 && <span>🎙️ {s.stats.audio_segments} 段</span>}
            {s.stats.video_key_frames != null && s.stats.video_key_frames > 0 && <span>🖼️ {s.stats.video_key_frames} 帧</span>}
            {s.stats.chat_messages != null && s.stats.chat_messages > 0 && <span>💬 {s.stats.chat_messages} 条</span>}
            {s.stats.duration_min != null && <span className="text-dust">⏱ {s.stats.duration_min} 分</span>}
          </div>
          {s.summary && <div className="text-sm text-star line-clamp-2">{s.summary}</div>}
          <div className="mt-1.5 flex flex-wrap gap-1">
            {s.topics.slice(0, 3).map((t) => (
              <span key={t.topic} className="rounded-full bg-gold/15 px-2 py-0.5 text-xs text-gold">
                {t.topic}
              </span>
            ))}
          </div>
        </>
      )}
    </button>
  )
}

export default function TimelineView() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const targetDate = params.get('date')
  const targetSession = params.get('session')
  const [typeFilter, setTypeFilter] = useState<EventType | 'all'>('all')
  const overview = useOverview()
  const timeline = useTimeline({ types: typeFilter === 'all' ? undefined : typeFilter, sessionId: targetSession ?? undefined })
  const sentinelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (targetDate) {
      const t = window.setTimeout(() => {
        document.getElementById(`day-${targetDate}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }, 400)
      return () => window.clearTimeout(t)
    }
  }, [targetDate, timeline.data])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && timeline.hasNextPage && !timeline.isFetchingNextPage) {
          timeline.fetchNextPage()
        }
      },
      { rootMargin: '600px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [timeline])

  const days = useMemo(() => {
    const map = new Map<string, TimelineDay>()
    for (const page of timeline.data?.pages ?? []) {
      for (const d of page.days) map.set(d.date, d)
    }
    return [...map.values()].sort((a, b) => (a.date < b.date ? 1 : -1))
  }, [timeline.data])

  const overviewDays = overview.data?.days ?? []
  const overviewGroups = useMemo(() => groupByMonth(overviewDays.slice(0, 7)), [overviewDays])
  const eventGroups = useMemo(() => groupByMonth(days), [days])

  return (
    <div className="mx-auto max-w-2xl px-3 pb-24 pt-4">
      <header className="sticky top-0 z-10 -mx-3 mb-3 bg-abyss/90 px-3 py-2 backdrop-blur">
        <h1 className="mb-2 text-xl font-semibold tracking-wide text-gold">
          ✦ MyLife
        </h1>
        <div className="flex gap-1.5 overflow-x-auto pb-1">
          {TYPE_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setTypeFilter(f.key)}
              className={`shrink-0 rounded-full px-3 py-1 text-xs transition-colors ${
                typeFilter === f.key ? 'bg-gold text-abyss' : 'bg-panel text-dust hover:text-star'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </header>

      {overview.isLoading ? (
        <div className="space-y-2">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl bg-panel/60" />
          ))}
        </div>
      ) : (
        <>
          <div className="mb-4">
            <HeatStrip
              days={overviewDays}
              onPick={(date) => document.getElementById(`day-${date}`)?.scrollIntoView({ behavior: 'smooth' })}
            />
          </div>
          {/* 本地日概览（河流章节头） */}
          <div className="mb-4 space-y-2">
            {overviewGroups.map((g) => {
              const [y, m] = g.month.split('-')
              return (
                <div key={g.month}>
                  <div className="my-3 text-2xl font-light tracking-widest text-gold/70">{y}年 · {Number(m)}月</div>
                  {g.days.map((day) => {
                    const f = fmtDay(day)
                    return (
                      <div key={day.date}>
                        <div className="mb-1.5 flex items-center justify-between">
                          <span className="text-sm font-medium text-star">{f.day}</span>
                          <span className="text-xs text-dust">
                            {Object.entries(day.counts)
                              .filter(([, n]) => n > 0)
                              .map(([k, n]) => `${k}:${n}`)
                              .join(' · ')}
                          </span>
                        </div>
                        <div className="space-y-2">
                          {day.sessions.map((s) => (
                            <SessionBlock key={s.session_id} s={s} />
                          ))}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* 事件流 */}
      {timeline.isLoading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-panel/60" />
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {eventGroups.map((g) => {
            const [y, m] = g.month.split('-')
            return (
              <div key={g.month}>
                <div className="my-4 text-2xl font-light tracking-widest text-gold/70">{y}年 · {Number(m)}月</div>
                {g.days.map((day) => {
                  const f = fmtDay(day)
                  return (
                    <section key={day.date} id={`day-${day.date}`} className="scroll-mt-28">
                      <div className="sticky top-[86px] z-[5] -mx-3 mb-2 border-y border-line bg-abyss/95 px-3 py-1.5 text-sm font-medium text-star backdrop-blur">
                        {f.day}
                      </div>
                      <div className="space-y-2">
                        {day.events.map((e, i) => (
                          <EventCard
                            key={`${e.ts_ms}-${i}`}
                            e={e}
                            onOpen={() => navigate(`/replay?session=${e.session_id}&t=${e.ts_ms}`)}
                          />
                        ))}
                      </div>
                    </section>
                  )
                })}
              </div>
            )
          })}
          <div ref={sentinelRef} className="flex justify-center py-4 text-xs text-dust">
            {timeline.isFetchingNextPage ? '加载中…' : timeline.hasNextPage ? '下拉加载更多' : `共 ${timeline.data?.pages[0]?.total ?? 0} 个事件`}
          </div>
        </div>
      )}
    </div>
  )
}
