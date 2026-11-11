import type { ConnectionDetails, CredentialProvider, JoinRequest } from './types'

type TokenResponse = Partial<ConnectionDetails> & {
  // personal_meeting_design.md §7.2 production field names
  livekit_url?: string
  token?: string
}

function validateServerUrl(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error('Token endpoint returned an invalid serverUrl')
  }
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error('Token endpoint returned an invalid serverUrl')
  }
  return url.toString().replace(/\/$/, '')
}

export function parseConnectionDetails(
  raw: TokenResponse,
  request: JoinRequest,
): ConnectionDetails {
  const serverUrl = validateServerUrl(raw.serverUrl ?? raw.livekit_url ?? '')
  const participantToken = raw.participantToken ?? raw.token ?? ''
  if (!participantToken) {
    throw new Error('Token endpoint returned an empty token')
  }
  if (raw.roomName && raw.roomName !== request.roomName) {
    throw new Error('Token endpoint returned a mismatched roomName')
  }
  if (raw.participantName && raw.participantName !== request.participantName) {
    throw new Error('Token endpoint returned a mismatched participantName')
  }
  return {
    serverUrl,
    roomName: request.roomName,
    participantName: request.participantName,
    participantToken,
  }
}

/**
 * Reserved for production. The production endpoint's request body is
 * `{ invite_token, display_name, passcode? }`, not `{ roomName, participantName }`;
 * extend JoinRequest to a discriminated union when wiring it up.
 */
export class HttpTokenProvider implements CredentialProvider {
  constructor(private readonly endpoint: string) {}

  async getConnectionDetails(request: JoinRequest): Promise<ConnectionDetails> {
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      credentials: 'same-origin',
    })
    if (!res.ok) {
      throw new Error(`Token endpoint failed: HTTP ${res.status}`)
    }
    return parseConnectionDetails((await res.json()) as TokenResponse, request)
  }
}
