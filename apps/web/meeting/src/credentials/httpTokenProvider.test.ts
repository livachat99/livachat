import { describe, expect, it } from 'vitest'
import { parseConnectionDetails } from './httpTokenProvider'

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
})
