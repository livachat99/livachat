export type JoinRequest = {
  roomName: string
  participantName: string
}

export type ConnectionDetails = {
  serverUrl: string
  roomName: string
  participantName: string
  participantToken: string
}

export interface CredentialProvider {
  getConnectionDetails(request: JoinRequest): Promise<ConnectionDetails>
}
