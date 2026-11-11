import type { LocalUserChoices } from '@livekit/components-react'

export type JoinParams = {
  roomName: string
  participantName: string
  choices: LocalUserChoices
}
