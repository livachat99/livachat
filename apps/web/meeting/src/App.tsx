import type { LocalUserChoices } from '@livekit/components-react'
import * as React from 'react'
import { Toaster, toast } from 'react-hot-toast'
import { ConfigError, FatalError } from './components/ConfigError'
import { ErrorView } from './components/ErrorView'
import { Home } from './components/Home'
import { Loading } from './components/Loading'
import { MeetingRoom } from './components/MeetingRoom'
import { PreJoinStage } from './components/PreJoinStage'
import { loadConfig, type MeetingConfig } from './config'
import { createCredentialProvider } from './credentials'
import type {
  ConnectionDetails,
  CredentialProvider,
} from './credentials/types'
import { useMessages } from './i18n'
import { messages as legacyMessages } from './messages'
import { meetingNumberFromUrl } from './meetingUrl'
import { saveParticipantName } from './storage'
import type { JoinParams } from './types'
import { errorMessage, formatMeetingError } from './utils'

type Stage =
  // roomName/participantName let Home be prefilled after Back/Leave; Home
  // unmounts while PreJoin/Meeting is active, so it cannot keep this state.
  | { kind: 'home'; roomName?: string; participantName?: string }
  | { kind: 'prejoin'; roomName: string; participantName: string }
  | { kind: 'meeting'; details: ConnectionDetails; choices: LocalUserChoices }
  | { kind: 'error'; message: string; join: JoinParams }

// Every stage must declare its document surface. Keying the record by the
// Stage union makes adding a new stage a compile error instead of silently
// painting the light background behind a dark screen.
const STAGE_SURFACE: Record<Stage['kind'], 'light' | 'dark'> = {
  home: 'light',
  prejoin: 'dark',
  meeting: 'dark',
  error: 'light',
}

const THEME_COLOR = {
  light: '#f4f6fb',
  dark: '#111111',
} as const

const DEFAULT_CHOICES: LocalUserChoices = {
  username: '',
  videoEnabled: true,
  audioEnabled: true,
  videoDeviceId: '',
  audioDeviceId: '',
}

function initialStage(): Stage {
  const roomName =
    typeof window === 'undefined'
      ? null
      : meetingNumberFromUrl(window.location.pathname)
  return roomName ? { kind: 'home', roomName } : { kind: 'home' }
}

export default function App() {
  const messages = useMessages()
  const [config, setConfig] = React.useState<MeetingConfig | null>(null)
  const [configError, setConfigError] = React.useState<string | null>(null)
  const [provider, setProvider] = React.useState<CredentialProvider | null>(
    null,
  )
  const [stage, setStage] = React.useState<Stage>(initialStage)

  React.useEffect(() => {
    let cancelled = false
    loadConfig()
      .then((cfg) => {
        if (cancelled) return
        setConfig(cfg)
        setProvider(createCredentialProvider(cfg))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setConfigError(errorMessage(error))
      })
    return () => {
      cancelled = true
    }
  }, [])

  React.useEffect(() => {
    if (config) document.title = config.appTitle
  }, [config])

  // Light join/error stages and dark meeting/PreJoin stages must paint with
  // matching document backgrounds; a mismatch is visible as a full-page
  // flash on Safari/Firefox. useLayoutEffect keeps the switch pre-paint.
  React.useLayoutEffect(() => {
    const surface = STAGE_SURFACE[stage.kind]
    const root = document.documentElement
    if (root.dataset.stage !== surface) root.dataset.stage = surface
    const meta = document.querySelector<HTMLMetaElement>(
      'meta[name="theme-color"]',
    )
    const color = THEME_COLOR[surface]
    if (meta && meta.content !== color) meta.setAttribute('content', color)
  }, [stage.kind])

  const secureContext =
    typeof window === 'undefined' ? true : window.isSecureContext
  const hasMediaDevices =
    typeof navigator !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  if (configError) return <ConfigError message={configError} />
  if (!secureContext) {
    return (
      <FatalError
        title={legacyMessages.secureContextTitle}
        message={legacyMessages.secureContextHint}
      />
    )
  }
  if (!hasMediaDevices) {
    return (
      <FatalError
        title={legacyMessages.unsupportedBrowserTitle}
        message={legacyMessages.unsupportedBrowserHint}
      />
    )
  }
  if (!config || !provider) return <Loading />

  const enterMeeting = async (params: JoinParams) => {
    try {
      const details = await provider.getConnectionDetails({
        roomName: params.roomName,
        participantName: params.participantName,
      })
      saveParticipantName(details.participantName)
      setStage({ kind: 'meeting', details, choices: params.choices })
    } catch (error) {
      setStage({
        kind: 'error',
        message: formatMeetingError(error, messages),
        join: params,
      })
    }
  }

  const handleHomeJoin = async (
    roomName: string,
    participantName: string,
  ) => {
    if (config.showPreJoin) {
      setStage({ kind: 'prejoin', roomName, participantName })
      return
    }
    await enterMeeting({
      roomName,
      participantName,
      choices: { ...DEFAULT_CHOICES, username: participantName },
    })
  }

  return (
    <>
      <Toaster />
      {stage.kind === 'home' ? (
        <Home
          config={config}
          defaultRoomName={stage.roomName}
          defaultParticipantName={stage.participantName}
          onJoin={handleHomeJoin}
        />
      ) : null}

      {stage.kind === 'prejoin' ? (
        <PreJoinStage
          config={config}
          provider={provider}
          roomName={stage.roomName}
          participantName={stage.participantName}
          onBack={() =>
            setStage({
              kind: 'home',
              roomName: stage.roomName,
              participantName: stage.participantName,
            })
          }
          onJoined={(details, choices) => {
            saveParticipantName(details.participantName)
            setStage({ kind: 'meeting', details, choices })
          }}
        />
      ) : null}

      {stage.kind === 'meeting' ? (
        <MeetingRoom
          key={stage.details.participantToken}
          config={config}
          details={stage.details}
          choices={stage.choices}
          onLeave={(message) => {
            toast(message ?? messages.youLeft)
            setStage({
              kind: 'home',
              roomName: stage.details.roomName,
              participantName: stage.details.participantName,
            })
          }}
          onError={(message, join) =>
            setStage({ kind: 'error', message, join })
          }
        />
      ) : null}

      {stage.kind === 'error' ? (
        <ErrorView
          message={stage.message}
          onBack={() =>
            setStage({
              kind: 'home',
              roomName: stage.join.roomName,
              participantName: stage.join.participantName,
            })
          }
        />
      ) : null}
    </>
  )
}
