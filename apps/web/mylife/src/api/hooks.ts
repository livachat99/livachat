import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { get } from './client'
import type {
  EntityResponse,
  GraphResponse,
  OverviewResponse,
  ReplayResponse,
  TimelineResponse,
} from './types'

export function useOverview(params: { deviceId?: string } = {}) {
  return useQuery({
    queryKey: ['overview', params.deviceId],
    queryFn: () => get<OverviewResponse>('/overview', { device_id: params.deviceId }),
    // analysis pending 时轮询消化管线延迟（60s）
    refetchInterval: (q) => (q.state.data?.days.some((d) => d.sessions.some((s) => s.analysis.timeline === 'pending')) ? 60_000 : false),
  })
}

export function useTimeline(params: { sessionId?: string; types?: string; enabled?: boolean }) {
  return useInfiniteQuery({
    queryKey: ['timeline', params.sessionId, params.types],
    queryFn: ({ pageParam }) =>
      get<TimelineResponse>('/timeline', {
        session_id: params.sessionId,
        types: params.types,
        limit: '200',
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: params.enabled ?? true,
  })
}

export function useGraph(params: { focus?: string; types?: string; dateFrom?: string; dateTo?: string } = {}) {
  return useQuery({
    queryKey: ['graph', params.focus, params.types, params.dateFrom, params.dateTo],
    queryFn: () =>
      get<GraphResponse>('/graph', {
        focus: params.focus,
        types: params.types,
        date_from: params.dateFrom,
        date_to: params.dateTo,
      }),
    placeholderData: keepPreviousData,
  })
}

export function useEntity(id: string | null) {
  return useQuery({
    queryKey: ['entity', id],
    queryFn: () => get<EntityResponse>(`/graph/entity/${encodeURIComponent(id ?? '')}`),
    enabled: !!id,
  })
}

export function useReplay(sessionId: string | null) {
  return useQuery({
    queryKey: ['replay', sessionId],
    queryFn: () => get<ReplayResponse>(`/replay/${sessionId}`),
    enabled: !!sessionId,
  })
}
