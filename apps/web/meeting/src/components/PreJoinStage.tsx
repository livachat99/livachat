import {
  PreJoin,
  type LocalUserChoices,
} from '@livekit/components-react'
import * as React from 'react'
import toast from 'react-hot-toast'
import type { MeetingConfig } from '../config'
import type {
  ConnectionDetails,
  CredentialProvider,
} from '../credentials/types'
import { useMessages } from '../i18n'
import { formatMessage } from '../i18n/format'
import { saveParticipantName } from '../storage'
import { formatMeetingError } from '../utils'
import styles from '../styles/home.module.css'

export type PreJoinStageProps = {
  config: MeetingConfig
  provider: CredentialProvider
  roomName: string
  participantName: string
  onBack: () => void
  onJoined: (details: ConnectionDetails, choices: LocalUserChoices) => void
}

export function PreJoinStage({
  config,
  provider,
  roomName,
  participantName,
  onBack,
  onJoined,
}: PreJoinStageProps) {
  const messages = useMessages()
  const [joining, setJoining] = React.useState(false)

  const handlePreJoinSubmit = async (values: LocalUserChoices) => {
    if (joining) return
    saveParticipantName(values.username)
    setJoining(true)
    try {
      const details = await provider.getConnectionDetails({
        roomName,
        participantName: values.username,
      })
      onJoined(details, values)
    } catch (error) {
      console.error(error)
      toast.error(formatMeetingError(error, messages))
      setJoining(false)
    }
  }

  const handlePreJoinError = (error: Error) => {
    console.error(error)
    toast.error(
      formatMessage(messages.mediaDeviceErrorWithDetail, {
        detail: error.message,
      }),
    )
  }

  return (
    <main className={styles.page}>
      <div style={{ width: 'min(100%, 640px)' }}>
        <button
          type="button"
          className="lk-button"
          onClick={onBack}
          disabled={joining}
          style={{ marginBottom: '1rem' }}
        >
          ← {messages.back}
        </button>
        <h1 className={styles.title} style={{ textAlign: 'center' }}>
          {config.appTitle}
        </h1>
        {joining ? (
          <p className={styles.subtitle} style={{ textAlign: 'center' }}>
            {messages.joining}
          </p>
        ) : null}
        <PreJoin
          defaults={{
            username: participantName,
            videoEnabled: true,
            audioEnabled: true,
          }}
          persistUserChoices={false}
          joinLabel={messages.joinMeeting}
          micLabel={messages.settingsMicrophone}
          camLabel={messages.settingsCamera}
          userLabel={messages.participantNameLabel}
          onSubmit={handlePreJoinSubmit}
          onError={handlePreJoinError}
        />
      </div>
    </main>
  )
}
