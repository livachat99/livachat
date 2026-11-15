import type { ConnectionDetails, CredentialProvider, JoinRequest } from './types'

type TokenResponse = Partial<ConnectionDetails> & {
  // production field names (docs/livachat_meeting.md §6.2)
  livekit_url?: string
  token?: string
}

export class TokenEndpointError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'TokenEndpointError'
  }
}

function validateServerUrl(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error('Token endpoint returned an invalid serverUrl')
  }
  // wss:// only, including localhost/dev.
  if (url.protocol !== 'wss:') {
    throw new Error('Token endpoint returned an invalid serverUrl')
  }
  return url.toString().replace(/\/$/, '')
}

export function parseConnectionDetails(
  raw: TokenResponse,
  request: JoinRequest,
  fallbackServerUrl = '',
): ConnectionDetails {
  const serverUrl = validateServerUrl(
    raw.serverUrl ?? raw.livekit_url ?? fallbackServerUrl,
  )
  const participantToken = raw.participantToken ?? raw.token ?? ''
  if (!participantToken) {
    throw new Error('Token endpoint returned an empty token')
  }
  const participantName = request.participantName.trim()
  if (raw.roomName && raw.roomName !== request.roomName) {
    throw new Error('Token endpoint returned a mismatched roomName')
  }
  if (raw.participantName && raw.participantName !== participantName) {
    throw new Error('Token endpoint returned a mismatched participantName')
  }
  return {
    serverUrl,
    roomName: request.roomName,
    participantName,
    participantToken,
  }
}

export class HttpTokenProvider implements CredentialProvider {
  constructor(
    private readonly endpoint: string,
    private readonly fallbackServerUrl = '',
  ) {}

  async getConnectionDetails(request: JoinRequest): Promise<ConnectionDetails> {
    const participantName = request.participantName.trim()
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        meeting_number: request.roomName,
        participant_name: participantName,
      }),
      credentials: 'same-origin',
    })
    if (!res.ok) {
      let code = 'token_endpoint_failed'
      let message = `Token endpoint failed: HTTP ${res.status}`
      const details: Record<string, unknown> = {}
      try {
        const body = (await res.json()) as {
          error?: { code?: unknown; message?: unknown }
        }
        const err = body.error
        if (err && typeof err === 'object') {
          if (typeof err.code === 'string') code = err.code
          if (typeof err.message === 'string') message = err.message
          for (const [key, value] of Object.entries(err)) {
            if (key !== 'code' && key !== 'message') details[key] = value
          }
        }
      } catch {
        // Non-JSON proxy/error page: keep the HTTP status fallback.
      }
      throw new TokenEndpointError(code, message, res.status, details)
    }
    return parseConnectionDetails(
      (await res.json()) as TokenResponse,
      { ...request, participantName },
      this.fallbackServerUrl,
    )
  }
}
