import type { TimelineEvent } from '../api/types'

function Chip({ children, tone = 'default' }: { children: React.ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    default: 'bg-line text-dust',
    gold: 'bg-gold/15 text-gold',
    blue: 'bg-blue/15 text-blue',
    pink: 'bg-pink/15 text-pink',
    cyan: 'bg-cyan/15 text-cyan',
    green: 'bg-green/15 text-green',
    red: 'bg-red/15 text-red',
  }
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${tones[tone] ?? tones.default}`}>
      {children}
    </span>
  )
}

function Card({ children, onOpen }: { children: React.ReactNode; onOpen?: () => void }) {
  return (
    <div
      onClick={onOpen}
      className={`rounded-xl border border-line bg-panel/80 p-3 shadow-sm ${
        onOpen ? 'cursor-pointer transition-colors hover:border-dust/50' : ''
      }`}
    >
      {children}
    </div>
  )
}

function timeLabel(tsMs: number): string {
  const d = new Date(tsMs)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function ChatCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  const isUser = e.role === 'user'
  return (
    <div onClick={onOpen} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
          isUser ? 'rounded-br-sm bg-gold/20 text-star' : 'rounded-bl-sm bg-panel text-star'
        } border ${isUser ? 'border-gold/30' : 'border-line'} ${onOpen ? 'cursor-pointer transition-colors hover:border-gold/40' : ''}`}
      >
        <div className="mb-0.5 text-[10px] text-dust">
          {timeLabel(e.ts_ms)} · {isUser ? '我' : 'AI'}
        </div>
        <div className="whitespace-pre-wrap break-words">{e.text}</div>
      </div>
    </div>
  )
}

function AudioCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  return (
    <Card onOpen={onOpen}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm">🎙️</span>
        <span className="text-xs text-dust">{timeLabel(e.ts_ms)} · 语音 {Math.round(e.duration_s ?? 0)}s</span>
        {(e.emotions ?? []).slice(0, 3).map((em) => (
          <Chip key={em.emotion} tone="pink">
            {em.emotion}
          </Chip>
        ))}
      </div>
      {e.media_url && <audio className="mb-2 w-full" controls preload="none" src={e.media_url} />}
      <div className="text-sm text-star">{e.summary || e.transcript || '(空段)'}</div>
    </Card>
  )
}

function VideoCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  return (
    <Card onOpen={onOpen}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm">🖼️</span>
        <span className="text-xs text-dust">{timeLabel(e.ts_ms)} · 画面</span>
        {e.scene && <Chip tone="cyan">{e.scene}</Chip>}
      </div>
      {e.media_url && (
        <img src={e.media_url} alt={e.caption ?? 'frame'} loading="lazy" className="mb-2 max-h-56 w-full rounded-lg object-cover" />
      )}
      {e.caption && <div className="text-sm text-star">{e.caption}</div>}
    </Card>
  )
}

function WindowCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  return (
    <Card onOpen={onOpen}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm">🗔</span>
        <span className="text-xs text-dust">{timeLabel(e.ts_ms)} · 窗口</span>
        <Chip tone={e.static ? 'default' : 'blue'}>{e.static ? '静态' : '活跃'}</Chip>
      </div>
      <div className="text-sm text-star">{e.caption}</div>
    </Card>
  )
}

function AgentJobCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  const done = e.status === 'done' || e.status === 'completed'
  return (
    <Card onOpen={onOpen}>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-sm">🤖</span>
        <span className="text-xs text-dust">{timeLabel(e.ts_ms)} · Agent Job</span>
        <Chip tone={done ? 'green' : e.status === 'failed' ? 'red' : 'gold'}>{e.status ?? 'unknown'}</Chip>
      </div>
      {e.summary && <div className="text-sm text-star">{e.summary}</div>}
      {e.fail_reason && <div className="mt-1 text-xs text-red">{e.fail_reason}</div>}
    </Card>
  )
}

export default function EventCard({ e, onOpen }: { e: TimelineEvent; onOpen?: () => void }) {
  switch (e.type) {
    case 'chat':
      return <ChatCard e={e} onOpen={onOpen} />
    case 'audio':
      return <AudioCard e={e} onOpen={onOpen} />
    case 'video':
      return <VideoCard e={e} onOpen={onOpen} />
    case 'window':
      return <WindowCard e={e} onOpen={onOpen} />
    case 'agent_job':
      return <AgentJobCard e={e} onOpen={onOpen} />
    default:
      return null
  }
}
