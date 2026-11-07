import { getToken } from '../token'

const BASE = '/api/mylife'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message)
  }
}

export async function get<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const q = new URLSearchParams()
  const token = getToken()
  if (token) q.set('token', token)
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') q.set(k, v)
  }
  const resp = await fetch(`${BASE}${path}${q.size ? `?${q}` : ''}`)
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null
    throw new ApiError(resp.status, body?.error?.code ?? 'internal', body?.error?.message ?? `HTTP ${resp.status}`)
  }
  return resp.json() as Promise<T>
}
