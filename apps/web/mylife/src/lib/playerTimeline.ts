import type { ReplayCue } from '../api/types'

export const DEFAULT_CUE_MS = 2500

export interface Segment {
  cue: ReplayCue
  start: number
  end: number
}

/**
 * 将 cue 序列映射为压缩时间轴上的播放段。
 *
 * 段边界 = 后端编排的绝对压缩偏移 cue.t_ms（非顺序累积），保证 Scrub/定位
 * 与后端意图一致；时长取自身 duration 与下一段起点的较小值，避免越界重叠。
 * skipPause 时 pause 段时长置 0（时间不推进，直接跳到下一事件）。
 */
export function buildSegments(cues: ReplayCue[], skipPause: boolean, defaultCueMs = DEFAULT_CUE_MS): Segment[] {
  return cues.map((cue, i) => {
    const nextT = cues[i + 1]?.t_ms
    const own = cue.type === 'pause' && skipPause ? 0 : (cue.duration_ms ?? defaultCueMs)
    const dur = nextT !== undefined ? Math.min(own, Math.max(0, nextT - cue.t_ms)) : own
    return { cue, start: cue.t_ms, end: cue.t_ms + dur }
  })
}

/** pos 所在段的索引；落在段间空隙或末尾时归入最近的后一段。 */
export function cueIndexAt(segments: Segment[], pos: number): number {
  for (let i = 0; i < segments.length; i++) {
    if (pos < segments[i].end) return i
  }
  return segments.length > 0 ? segments.length - 1 : 0
}

/** 原始绝对时刻（ts_ms）→ 压缩时间轴位置：取 ts_utc 最接近的 cue。 */
export function locateCueTs(cues: ReplayCue[], tsMs: number): number {
  let best = 0
  let bestDist = Infinity
  for (const c of cues) {
    const cueTs = c.ts_utc ? new Date(c.ts_utc).getTime() : 0
    const d = Math.abs(cueTs - tsMs)
    if (d < bestDist) {
      bestDist = d
      best = c.t_ms
    }
  }
  return best
}
