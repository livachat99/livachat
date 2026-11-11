import { jwtVerify } from 'jose'
import { describe, expect, it } from 'vitest'
import { createParticipantToken } from './token'

const apiKey = 'test-api-key'
const apiSecret = 'test-api-secret'

describe('createParticipantToken', () => {
  it('signs a LiveKit join token with the expected claims', async () => {
    const token = await createParticipantToken({
      apiKey,
      apiSecret,
      roomName: 'design-review',
      identity: 'u_abcdefghijklmnop',
      displayName: 'Alice',
      ttlSeconds: 600,
    })

    const { payload } = await jwtVerify(
      token,
      new TextEncoder().encode(apiSecret),
      { issuer: apiKey },
    )

    expect(payload.iss).toBe(apiKey)
    expect(payload.sub).toBe('u_abcdefghijklmnop')
    expect(payload.name).toBe('Alice')
    expect(payload.video).toMatchObject({
      room: 'design-review',
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    })
    expect(typeof payload.nbf).toBe('number')
    expect(typeof payload.exp).toBe('number')
    expect((payload.exp as number) - (payload.nbf as number)).toBeGreaterThanOrEqual(
      600,
    )
  })

  it('rejects a token signed with a different secret', async () => {
    const token = await createParticipantToken({
      apiKey,
      apiSecret,
      roomName: 'room',
      identity: 'u_abcdefghijklmnop',
      displayName: 'Alice',
      ttlSeconds: 600,
    })

    await expect(
      jwtVerify(token, new TextEncoder().encode('wrong-secret'), {
        issuer: apiKey,
      }),
    ).rejects.toThrow()
  })
})
