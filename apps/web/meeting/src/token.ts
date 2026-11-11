import { SignJWT } from 'jose'

export type TokenInput = {
  apiKey: string
  apiSecret: string
  roomName: string
  identity: string
  displayName: string
  ttlSeconds: number
}

/**
 * Creates a LiveKit access token in the browser.
 *
 * This mirrors livekit-server-sdk's AccessToken.toJwt(): HS256, `iss` = API key,
 * `sub` = identity, `nbf` = now - 30s, `exp` = now + ttl, and the `video` grant.
 * The API secret is necessarily present in the client in this demo mode.
 */
export async function createParticipantToken(
  input: TokenInput,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000)
  const key = new TextEncoder().encode(input.apiSecret)

  return new SignJWT({
    name: input.displayName,
    video: {
      room: input.roomName,
      roomJoin: true,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    },
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuer(input.apiKey)
    .setSubject(input.identity)
    .setNotBefore(now - 30)
    .setExpirationTime(now + input.ttlSeconds)
    .sign(key)
}
