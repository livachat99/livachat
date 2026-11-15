import { describe, expect, it, vi } from 'vitest'
import { HttpTokenProvider, parseConnectionDetails } from './httpTokenProvider'

const request = { roomName: 'room', participantName: 'Alice' }

describe('parseConnectionDetails', () => {
  it('accepts camelCase responses', () => {
    expect(
      parseConnectionDetails(
        {
          serverUrl: 'wss://livekit.example.test:7880',
          roomName: 'room',
          participantName: 'Alice',
          participantToken: 'token',
        },
        request,
      ),
    ).toMatchObject({
      serverUrl: 'wss://livekit.example.test:7880',
      roomName: 'room',
      participantName: 'Alice',
      participantToken: 'token',
    })
  })

  it('accepts the production snake_case response', () => {
    expect(
      parseConnectionDetails(
        {
          livekit_url: 'wss://livekit.example.test:7880',
          token: 'token',
        },
        request,
      ),
    ).toMatchObject({
      serverUrl: 'wss://livekit.example.test:7880',
      roomName: 'room',
      participantName: 'Alice',
      participantToken: 'token',
    })
  })

  it('rejects invalid URLs, empty tokens and mismatched echo', () => {
    expect(() =>
      parseConnectionDetails(
        { livekit_url: 'https://example.test', token: 'token' },
        request,
      ),
    ).toThrow()
    expect(() =>
      parseConnectionDetails(
        { livekit_url: 'ws://localhost:7880', token: 'token' },
        request,
      ),
    ).toThrow()
    expect(() =>
      parseConnectionDetails(
        { livekit_url: 'wss://example.test', token: '' },
        request,
      ),
    ).toThrow()
    expect(() =>
      parseConnectionDetails(
        { livekit_url: 'wss://example.test', token: 'token', roomName: 'other' },
        request,
      ),
    ).toThrow()
  })

  it('uses fallbackServerUrl when the endpoint omits serverUrl', () => {
    expect(
      parseConnectionDetails({ token: 'token' }, request, 'wss://fallback.example.test'),
    ).toMatchObject({ serverUrl: 'wss://fallback.example.test' })
  })
})

describe('HttpTokenProvider', () => {
  it('sends meeting_number/participant_name and trims the name', async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ input, init })
      return new Response(
        JSON.stringify({
          serverUrl: 'wss://livekit.example.test:7880',
          roomName: '12345678',
          participantName: 'Alice',
          participantToken: 'token',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    try {
      const provider = new HttpTokenProvider(
        '/api/mylife/join_meeting',
        'wss://fallback.example.test',
      )
      const details = await provider.getConnectionDetails({
        roomName: '12345678',
        participantName: ' Alice ',
      })
      expect(details.participantName).toBe('Alice')
      expect(JSON.parse(String(calls[0].init?.body))).toEqual({
        meeting_number: '12345678',
        participant_name: 'Alice',
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('turns the BFF error envelope into TokenEndpointError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'meeting_not_started',
              message: 'meeting is not open yet',
              opens_at_ms: 123,
            },
          }),
          { status: 403, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )
    try {
      const provider = new HttpTokenProvider('/api/mylife/join_meeting')
      await expect(
        provider.getConnectionDetails({ roomName: '12345678', participantName: 'A' }),
      ).rejects.toMatchObject({
        code: 'meeting_not_started',
        status: 403,
        details: { opens_at_ms: 123 },
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('falls back to HTTP status for non-JSON errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('bad gateway', { status: 502 })),
    )
    try {
      const provider = new HttpTokenProvider('/api/mylife/join_meeting')
      await expect(
        provider.getConnectionDetails({ roomName: '12345678', participantName: 'A' }),
      ).rejects.toMatchObject({
        code: 'token_endpoint_failed',
        status: 502,
      })
    } finally {
      vi.unstubAllGlobals()
    }
  })
})
