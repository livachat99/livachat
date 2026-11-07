import { useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useOverview, useReplay } from '../api/hooks'
import { buildSegments, cueIndexAt, locateCueTs } from '../lib/playerTimeline'
import Player from '../components/Player'
import Stage from '../components/Stage'

export default function ReplayView() {
  const [params] = useSearchParams()
  const sessionId = params.get('session')
  const overview = useOverview()
  const replay = useReplay(sessionId)

  const [playing, setPlaying] = useState(true)
  const [pos, setPos] = useState(0)
  const [rate, setRate] = useState(1)
  const [skipPause, setSkipPause] = useState(false)
  const startedRef = useRef<number>(0)
  const locatedRef = useRef(false)

  const cues = replay.data?.cues ?? []
  const duration = replay.data?.duration_ms ?? 0

  const segments = useMemo(() => buildSegments(cues, skipPause), [cues, skipPause])
  const cueIndex = useMemo(() => cueIndexAt(segments, pos), [segments, pos])
  const currentCue = segments[cueIndex]?.cue ?? null

  // ?t=<原始 ts_ms> 定位：跳转到该时刻最近的 cue（Timeline 事件卡跳入）
  useEffect(() => {
    const t = params.get('t')
    if (t && replay.data && !locatedRef.current) {
      locatedRef.current = true
      setPos(locateCueTs(cues, Number(t)))
    }
  }, [replay.data, params, cues])

  // 时间推进（压缩时间轴；pause 已并入 segments）
  useEffect(() => {
    if (!playing || duration === 0) return
    const id = window.setInterval(() => {
      const next = Date.now()
      const delta = (next - startedRef.current) * rate
      startedRef.current = next
      setPos((p) => {
        const np = Math.min(p + delta, duration)
        if (np >= duration) setPlaying(false)
        return np
      })
    }, 50)
    return () => window.clearInterval(id)
  }, [playing, duration, rate])

  const toggle = () => {
    if (!playing && pos >= duration) setPos(0)
    startedRef.current = Date.now()
    setPlaying(!playing)
  }
  const seek = (ms: number) => {
    startedRef.current = Date.now()
    setPos(ms)
    setPlaying(true)
  }

  // 会话列表模式（无 ?session=）
  if (!sessionId) {
    const sessions = (overview.data?.days ?? []).flatMap((d) => d.sessions)
    return (
      <div className="mx-auto max-w-2xl px-3 py-4">
        <h1 className="mb-3 text-xl font-semibold text-gold">▶ Replay — 选择一段时光</h1>
        {overview.isLoading ? (
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="h-16 animate-pulse rounded-xl bg-panel/60" />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <a
                key={s.session_id}
                href={`#/replay?session=${s.session_id}`}
                className={`block rounded-xl border p-3 transition-colors ${
                  s.analysis.timeline === 'ok' ? 'border-line bg-panel/60 hover:border-gold/40' : 'border-line/50 opacity-60'
                }`}
              >
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-star">{s.started_at?.slice(0, 10)} · {s.stats.duration_min ?? 0} 分</span>
                  {s.analysis.timeline === 'ok' ? (
                    <span className="text-xs text-dust">▶ 回放</span>
                  ) : (
                    <span className="animate-pulse text-xs text-gold">分析中…</span>
                  )}
                </div>
                {s.summary && <div className="mt-1 text-xs text-dust line-clamp-2">{s.summary}</div>}
              </a>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (replay.isLoading) {
    return <div className="flex h-full items-center justify-center text-dust">加载回放…</div>
  }
  if (replay.isError || !replay.data) {
    return <div className="flex h-full items-center justify-center text-dust">该会话尚未分析完成</div>
  }

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <Stage cue={currentCue} audioPlayed={playing} />
      </div>
      <Player
        playing={playing}
        pos={pos}
        duration={duration}
        rate={rate}
        skipPause={skipPause}
        cue={currentCue}
        onToggle={toggle}
        onSeek={seek}
        onRate={setRate}
        onSkipPause={setSkipPause}
      />
    </div>
  )
}
