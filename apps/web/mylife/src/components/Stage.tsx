import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import type { ReplayCue } from '../api/types'

function clockOf(tsUtc?: string | null): string {
  if (!tsUtc) return ''
  return new Date(tsUtc).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function Stage({ cue, audioPlayed }: { cue: ReplayCue; audioPlayed: boolean }) {
  const [audioError, setAudioError] = useState(false)
  const key = useMemo(() => `${cue.type}-${cue.t_ms}`, [cue])

  // audioError 是单段加载失败的状态：切 cue 时重置，避免后续音频被永久抑制
  useEffect(() => {
    setAudioError(false)
  }, [key])

  const clock = clockOf(cue.ts_utc)

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={key}
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -12 }}
        transition={{ duration: 0.3 }}
        className="relative flex h-full flex-col items-center justify-center overflow-hidden"
      >
        {clock && <div className="absolute top-3 left-1/2 -translate-x-1/2 font-mono text-xs text-dust">{clock}</div>}

        {cue.type === 'frame' && (
          <motion.div
            className="flex h-full w-full items-center justify-center"
            initial={{ scale: 1.15 }}
            animate={{ scale: 1 }}
            transition={{ duration: 6, ease: 'easeOut' }}
          >
            {cue.media_url ? (
              <img src={cue.media_url} alt={cue.caption ?? ''} className="max-h-[72vh] w-full object-contain" />
            ) : (
              <div className="mx-6 rounded-2xl border border-line bg-panel/80 px-5 py-8 text-center">
                <div className="mb-1 text-2xl">🗔</div>
                <div className="text-sm text-star">{cue.caption}</div>
              </div>
            )}
          </motion.div>
        )}

        {cue.type === 'chat' && (
          <div className="mx-6 w-full max-w-lg">
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              className={`rounded-2xl border px-4 py-3 text-sm ${
                cue.role === 'user'
                  ? 'ml-auto max-w-[80%] border-gold/30 bg-gold/20'
                  : 'mr-auto max-w-[80%] border-line bg-panel'
              }`}
            >
              <div className="mb-0.5 text-[10px] text-dust">{cue.role === 'user' ? '我' : 'AI'}</div>
              <div className="whitespace-pre-wrap text-star">{cue.text}</div>
            </motion.div>
          </div>
        )}

        {cue.type === 'audio' && (
          <div className="mx-6 w-full max-w-lg">
            <div className="mb-3 flex items-center justify-center gap-2">
              <span className={`text-3xl ${audioPlayed ? 'text-gold' : 'text-dust'}`}>🎙️</span>
              <span className="text-xs text-dust">{Math.round((cue.duration_ms ?? 0) / 1000)}s</span>
            </div>
            {cue.media_url && !audioError && (
              <audio
                key={key}
                src={cue.media_url}
                autoPlay={audioPlayed}
                controls={!audioPlayed}
                onError={() => setAudioError(true)}
                className="mx-auto w-full max-w-sm"
              />
            )}
            <div className="mt-3 rounded-xl border border-line bg-panel/80 px-4 py-3 text-sm text-star">{cue.text}</div>
            {(cue.emotions ?? []).slice(0, 3).map((em) => (
              <span key={em.emotion} className="mr-1.5 inline-block rounded-full bg-pink/15 px-2 py-0.5 text-xs text-pink">
                {em.emotion}
              </span>
            ))}
          </div>
        )}

        {cue.type === 'pause' && (
          <motion.div
            animate={{ opacity: [0.4, 1, 0.4] }}
            transition={{ repeat: Infinity, duration: 1.6 }}
            className="text-center"
          >
            <div className="text-4xl">🌙</div>
            <div className="mt-2 text-sm text-dust">{cue.hint ?? '沉默期'}</div>
          </motion.div>
        )}

        {cue.type === 'job' && (
          <div className="mx-6 w-full max-w-md rounded-xl border border-line bg-panel/80 p-4 text-center">
            <div className="mb-1 flex items-center justify-center gap-2">
              <span className="text-xl">🤖</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${
                  cue.status === 'done' ? 'bg-green/15 text-green' : cue.status === 'failed' ? 'bg-red/15 text-red' : 'bg-gold/15 text-gold'
                }`}
              >
                {cue.status ?? 'job'}
              </span>
            </div>
            {cue.text && <div className="text-sm text-star">{cue.text}</div>}
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  )
}
