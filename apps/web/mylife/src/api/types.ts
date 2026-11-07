// 与 services/bff/common/schemas.py 对齐

export type EventType = 'chat' | 'audio' | 'video' | 'window' | 'agent_job'
export type AnalysisState = 'ok' | 'pending' | 'missing'
export type EntityKind = 'event' | 'topic' | 'emotion' | 'object' | 'app' | 'scene'

export interface AnalysisStatus {
  timeline: AnalysisState
  summary: AnalysisState
}

export interface TopicItem {
  topic: string
  mentions: number
}

export interface SessionStats {
  duration_min: number | null
  chat_messages: number | null
  audio_segments: number | null
  video_key_frames: number | null
}

export interface SessionItem {
  session_id: string
  device_uuid: string
  status: string
  started_at: string | null
  stats: SessionStats
  summary: string | null
  topics: TopicItem[]
  analysis: AnalysisStatus
}

export interface OverviewDay {
  date: string
  counts: Partial<Record<EventType, number>>
  sessions: SessionItem[]
}

export interface OverviewResponse {
  schema_version: 1
  timezone: string
  days: OverviewDay[]
  total_sessions: number
}

export interface TimelineEvent {
  ts_ms: number
  ts_utc?: string | null
  type: EventType
  session_id: string
  device_uuid: string
  media_url?: string | null
  // 类型专属字段（直通 timeline.json）
  role?: string
  text?: string
  segment_id?: string
  transcript?: string
  summary?: string
  duration_s?: number
  empty?: boolean
  emotions?: { emotion: string; confidence: number }[]
  frame_id?: string
  scene?: string
  caption?: string
  apps?: unknown[]
  ocr?: unknown[]
  actions?: unknown[]
  window_id?: string
  static?: boolean
  job_key?: string
  status?: string
  fail_reason?: string | null
  has_result?: boolean
}

export interface TimelineDay {
  date: string
  events: TimelineEvent[]
}

export interface TimelineResponse {
  schema_version: 1
  timezone: string
  days: TimelineDay[]
  next_cursor: string | null
  total: number
}

export interface GraphNode {
  id: string
  kind: EntityKind
  label: string
  weight: number
  first_seen_ms: number | null
  last_seen_ms: number | null
  session_id?: string | null
}

export interface GraphEdge {
  source: string
  target: string
  weight: number
  kind: 'session_cooccur'
}

export interface GraphResponse {
  schema_version: 1
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { sessions: number; date_range: string[] }
}

export interface EntityAppearance {
  session_id: string
  session_date: string
  snippet: string | null
}

export interface EntityResponse {
  schema_version: 1
  id: string
  kind: EntityKind
  label: string
  weight: number
  first_seen_ms: number | null
  last_seen_ms: number | null
  appearances: EntityAppearance[]
}

export interface ReplaySessionInfo {
  session_id: string
  device_uuid: string
  session_date: string | null
  started_at: string | null
  ended_at: string | null
  time_span_ms: number | null
}

export interface ReplayCue {
  t_ms: number
  type: 'chat' | 'audio' | 'frame' | 'pause' | 'job'
  ts_utc?: string | null
  media_url?: string | null
  // 类型专属字段
  role?: string
  text?: string
  duration_ms?: number
  emotions?: { emotion: string; confidence: number }[]
  caption?: string
  hold_ms?: number
  hint?: string
  scene?: string
  static?: boolean
  job_key?: string
  status?: string
  fail_reason?: string | null
}

export interface ReplayResponse {
  schema_version: 1
  session: ReplaySessionInfo
  duration_ms: number
  cues: ReplayCue[]
}
