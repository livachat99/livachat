import type { ReplayCue } from '../api/types'

function fmt(ms: number): string {
  const s = Math.floor(ms / 1000)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

interface Props {
  playing: boolean
  pos: number
  duration: number
  rate: number
  skipPause: boolean
  cue: ReplayCue | null
  onToggle: () => void
  onSeek: (ms: number) => void
  onRate: (r: number) => void
  onSkipPause: (v: boolean) => void
}

export default function Player({
  playing,
  pos,
  duration,
  rate,
  skipPause,
  cue,
  onToggle,
  onSeek,
  onRate,
  onSkipPause,
}: Props) {
  return (
    <div className="border-t border-line bg-void/95 px-3 pt-2 pb-safe backdrop-blur">
      <input
        type="range"
        min={0}
        max={Math.max(1, duration)}
        value={pos}
        onChange={(e) => onSeek(Number(e.target.value))}
        className="w-full accent-gold"
        aria-label="进度"
      />
      <div className="flex items-center gap-3 py-1">
        <button onClick={onToggle} className="min-h-11 min-w-11 rounded-full bg-gold text-lg text-abyss">
          {playing ? '⏸' : '▶'}
        </button>
        <div className="flex gap-1">
          {[1, 2, 4].map((r) => (
            <button
              key={r}
              onClick={() => onRate(r)}
              className={`min-h-9 min-w-9 rounded-full text-xs ${rate === r ? 'bg-gold/25 text-gold' : 'text-dust'}`}
            >
              {r}x
            </button>
          ))}
        </div>
        <button
          onClick={() => onSkipPause(!skipPause)}
          className={`min-h-9 rounded-full px-2 text-xs ${skipPause ? 'bg-cyan/20 text-cyan' : 'text-dust'}`}
        >
          跳过静默
        </button>
        <div className="ml-auto font-mono text-xs text-dust">
          {fmt(pos)} / {fmt(duration)}
          {cue?.ts_utc && (
            <span className="ml-2 text-gold/80">
              {new Date(cue.ts_utc).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
