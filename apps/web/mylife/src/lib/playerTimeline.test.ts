import { describe, expect, it } from 'vitest'
import type { ReplayCue } from '../api/types'
import { buildSegments, cueIndexAt, locateCueTs } from './playerTimeline'

function cue(partial: Partial<ReplayCue> & { t_ms: number; type: ReplayCue['type'] }): ReplayCue {
  return { ...partial, t_ms: partial.t_ms, type: partial.type }
}

const ts = (s: number) => Date.UTC(2026, 7, 17, 0, 0, s)

describe('buildSegments', () => {
  it('段边界 = 后端压缩时间轴 t_ms（非顺序累积）', () => {
    const cues = [
      cue({ t_ms: 0, type: 'chat', duration_ms: 2500 }),
      cue({ t_ms: 375, type: 'audio', duration_ms: 7500 }),
      cue({ t_ms: 8000, type: 'frame', duration_ms: 2500 }),
    ]
    const segs = buildSegments(cues, false)
    expect(segs.map((s) => [s.start, s.end])).toEqual([
      [0, 375], // chat 时长被下一段起点截断
      [375, 7875],
      [8000, 10500],
    ])
  })

  it('音频段时长不被下一段截断（间隔大于自身时长）', () => {
    const cues = [
      cue({ t_ms: 0, type: 'audio', duration_ms: 7500 }),
      cue({ t_ms: 30000, type: 'chat', duration_ms: 2500 }),
    ]
    const segs = buildSegments(cues, false)
    expect(segs[0].end).toBe(7500)
    expect(segs[1].start).toBe(30000)
  })

  it('skipPause 时 pause 段时长为 0', () => {
    const cues = [
      cue({ t_ms: 0, type: 'chat', duration_ms: 2500 }),
      cue({ t_ms: 1000, type: 'pause', duration_ms: 5000 }),
      cue({ t_ms: 7000, type: 'chat', duration_ms: 2500 }),
    ]
    const skipped = buildSegments(cues, true)
    const pause = skipped[1]
    expect(pause.start).toBe(pause.end)

    const normal = buildSegments(cues, false)
    expect(normal[1].end - normal[1].start).toBe(5000)
  })

  it('pause 无 skipPause 时保持自身时长', () => {
    const segs = buildSegments([cue({ t_ms: 0, type: 'pause', duration_ms: 4000 })], false)
    expect(segs[0].end).toBe(4000)
  })
})

describe('cueIndexAt', () => {
  const segs = buildSegments(
    [
      cue({ t_ms: 0, type: 'chat', duration_ms: 2500 }),
      cue({ t_ms: 1000, type: 'audio', duration_ms: 7500 }),
      cue({ t_ms: 9000, type: 'frame', duration_ms: 2500 }),
    ],
    false,
  )

  it('定位段内位置', () => {
    expect(segs[cueIndexAt(segs, 500)].cue.type).toBe('chat')
    expect(segs[cueIndexAt(segs, 2000)].cue.type).toBe('audio')
    expect(segs[cueIndexAt(segs, 9500)].cue.type).toBe('frame')
  })

  it('段间空隙归入后一段', () => {
    // chat 段 [0,1000) 被截断后 audio 从 1000 开始，无空隙 → 直接验证末尾
    expect(cueIndexAt(segs, 999999)).toBe(segs.length - 1)
  })

  it('空序列返回 0', () => {
    expect(cueIndexAt([], 100)).toBe(0)
  })
})

describe('locateCueTs', () => {
  const cues = [
    cue({ t_ms: 0, type: 'chat', ts_utc: '2026-08-17T00:00:00Z' }),
    cue({ t_ms: 1000, type: 'audio', ts_utc: '2026-08-17T00:01:00Z' }),
    cue({ t_ms: 2000, type: 'chat', ts_utc: '2026-08-17T00:02:00Z' }),
  ]

  it('定位到 ts 最近的 cue 的压缩位置', () => {
    expect(locateCueTs(cues, ts(1))).toBe(0)
    expect(locateCueTs(cues, ts(61))).toBe(1000)
    expect(locateCueTs(cues, ts(125))).toBe(2000)
  })
})
