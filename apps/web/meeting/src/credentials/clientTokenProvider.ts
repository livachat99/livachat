import type { ClientTokenConfig } from '../config'
import { makeIdentity } from '../identity'
import { createParticipantToken } from '../token'
import type { ConnectionDetails, CredentialProvider, JoinRequest } from './types'

export class ClientTokenProvider implements CredentialProvider {
  constructor(private readonly config: ClientTokenConfig) {}

  async getConnectionDetails({
    roomName,
    participantName,
  }: JoinRequest): Promise<ConnectionDetails> {
    const normalizedRoom = roomName.trim().toLowerCase()
    const displayName = participantName.trim() || 'Guest'
    const identity = makeIdentity()
    const participantToken = await createParticipantToken({
      apiKey: this.config.apiKey,
      apiSecret: this.config.apiSecret,
      roomName: normalizedRoom,
      identity,
      displayName,
      ttlSeconds: this.config.tokenTtlSeconds,
    })
    return {
      serverUrl: this.config.livekitUrl,
      roomName: normalizedRoom,
      participantName: displayName,
      participantToken,
    }
  }
}
