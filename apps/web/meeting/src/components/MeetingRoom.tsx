import {
  RoomContext,
  formatChatMessageLinks,
  type LocalUserChoices,
} from '@livekit/components-react'
import {
  ConnectionState,
  DisconnectReason,
  Room,
  RoomEvent,
  VideoPresets,
  isSVCCodec,
  type RoomOptions,
} from 'livekit-client'
import * as React from 'react'
import toast from 'react-hot-toast'
import type { MeetingConfig } from '../config'
import type { ConnectionDetails } from '../credentials/types'
import { useLowCPUOptimizer } from '../hooks/useLowCPUOptimizer'
import { messages } from '../messages'
import type { JoinParams } from '../types'
import { useMessages } from '../i18n'
import { errorMessage, isIOSLike } from '../utils'
import { DebugMode } from './DebugMode'
import { KeyboardShortcuts } from './KeyboardShortcuts'
import { LeaveConfirmDialog } from './LeaveConfirmDialog'
import { RecordingIndicator } from './RecordingIndicator'
import { SettingsMenu } from './SettingsMenu'
import { LocalizedVideoConference } from './livekit/LocalizedVideoConference'

export type MeetingRoomProps = {
  config: MeetingConfig
  details: ConnectionDetails
  choices: LocalUserChoices
  onLeave: (message?: string) => void
  onError: (message: string, join: JoinParams) => void
}

function roomOptionsFrom(
  videoCodec: MeetingConfig['videoCodec'],
  choices: LocalUserChoices,
): RoomOptions {
  const svcCodec = isSVCCodec(videoCodec)
  return {
    adaptiveStream: true,
    dynacast: true,
    videoCaptureDefaults: {
      deviceId: choices.videoDeviceId || undefined,
      resolution: VideoPresets.h720,
    },
    audioCaptureDefaults: {
      deviceId: choices.audioDeviceId || undefined,
    },
    publishDefaults: {
      dtx: false,
      red: true,
      videoCodec,
      // SVC codecs (vp9/av1) ignore simulcast layers entirely.
      videoSimulcastLayers: svcCodec
        ? undefined
        : [VideoPresets.h540, VideoPresets.h216],
    },
    singlePeerConnection: true,
  }
}

function disconnectMessage(reason?: DisconnectReason): string {
  switch (reason) {
    case DisconnectReason.DUPLICATE_IDENTITY:
      return messages.duplicateIdentity
    case DisconnectReason.PARTICIPANT_REMOVED:
      return messages.participantRemoved
    case DisconnectReason.ROOM_DELETED:
      return messages.roomDeleted
    default:
      return messages.connectionLost
  }
}

export function MeetingRoom({
  config,
  details,
  choices,
  onLeave,
  onError,
}: MeetingRoomProps) {
  const localizedMessages = useMessages()
  const room = React.useMemo(
    () => new Room(roomOptionsFrom(config.videoCodec, choices)),
    // The component is remounted per meeting (App uses a token key).
    [],
  )

  const onLeaveRef = React.useRef(onLeave)
  const onErrorRef = React.useRef(onError)
  const hasConnectedRef = React.useRef(false)
  React.useEffect(() => {
    onLeaveRef.current = onLeave
    onErrorRef.current = onError
  }, [onLeave, onError])

  React.useEffect(() => {
    let cancelled = false
    let hasConnected = false
    hasConnectedRef.current = false

    const handleConnected = () => {
      hasConnected = true
      hasConnectedRef.current = true
    }

    const handleDisconnected = (reason?: DisconnectReason) => {
      if (cancelled || !hasConnected) return
      if (reason === DisconnectReason.CLIENT_INITIATED) {
        onLeaveRef.current()
        return
      }
      if (
        reason === DisconnectReason.DUPLICATE_IDENTITY ||
        reason === DisconnectReason.PARTICIPANT_REMOVED ||
        reason === DisconnectReason.ROOM_DELETED
      ) {
        onLeaveRef.current(disconnectMessage(reason))
        return
      }
      onErrorRef.current(disconnectMessage(reason), {
        roomName: details.roomName,
        participantName: details.participantName,
        choices,
      })
    }

    const handleDeviceError = (error: unknown) => {
      console.error(error)
      toast.error(`${messages.mediaDeviceError}: ${errorMessage(error)}`)
    }

    room.on(RoomEvent.Connected, handleConnected)
    room.on(RoomEvent.Disconnected, handleDisconnected)
    room.on(RoomEvent.MediaDevicesError, handleDeviceError)

    const enableDevice = async (enable: () => Promise<unknown>) => {
      try {
        await enable()
      } catch (error) {
        if (cancelled) return
        if (
          (error as { name?: string } | null)?.name === 'NotReadableError'
        ) {
          // PreJoin may have just released the device; Safari needs a moment.
          await new Promise((resolve) => window.setTimeout(resolve, 250))
          if (cancelled) return
          await enable().catch(handleDeviceError)
          return
        }
        handleDeviceError(error as Error)
      }
    }

    room
      .connect(details.serverUrl, details.participantToken, {
        autoSubscribe: true,
      })
      .then(async () => {
        if (cancelled) return
        if (choices.videoEnabled) {
          await enableDevice(() => room.localParticipant.setCameraEnabled(true))
        }
        if (choices.audioEnabled) {
          await enableDevice(() =>
            room.localParticipant.setMicrophoneEnabled(true),
          )
        }
      })
      .catch((error: Error) => {
        if (cancelled) return
        onErrorRef.current(errorMessage(error), {
          roomName: details.roomName,
          participantName: details.participantName,
          choices,
        })
      })

    return () => {
      cancelled = true
      room.off(RoomEvent.Connected, handleConnected)
      room.off(RoomEvent.Disconnected, handleDisconnected)
      room.off(RoomEvent.MediaDevicesError, handleDeviceError)
      try {
        room.disconnect()
      } catch (error) {
        console.error(error)
      }
    }
  }, [room, details, choices])

  const lowPowerMode = useLowCPUOptimizer(room)
  React.useEffect(() => {
    if (lowPowerMode) console.warn('Low power mode enabled')
  }, [lowPowerMode])

  const ios = React.useMemo(() => isIOSLike(), [])
  const [participantCount, setParticipantCount] = React.useState(0)
  const [showEmptyHint, setShowEmptyHint] = React.useState(false)
  const [leaveConfirmOpen, setLeaveConfirmOpen] = React.useState(false)

  React.useEffect(() => {
    let timer: number | undefined
    const update = () => {
      const count = room.numParticipants
      setParticipantCount(count)
      if (count > 1) {
        setShowEmptyHint(false)
        if (timer !== undefined) {
          window.clearTimeout(timer)
          timer = undefined
        }
        return
      }
      if (count === 1 && timer === undefined) {
        timer = window.setTimeout(() => {
          timer = undefined
          if (room.numParticipants <= 1) setShowEmptyHint(true)
        }, 30_000)
      }
    }

    update()
    room.on(RoomEvent.Connected, update)
    room.on(RoomEvent.ParticipantConnected, update)
    room.on(RoomEvent.ParticipantDisconnected, update)

    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
      room.off(RoomEvent.Connected, update)
      room.off(RoomEvent.ParticipantConnected, update)
      room.off(RoomEvent.ParticipantDisconnected, update)
    }
  }, [room])

  const closeLeaveConfirm = React.useCallback(() => {
    setLeaveConfirmOpen(false)
  }, [])

  const confirmLeave = React.useCallback(() => {
    setLeaveConfirmOpen(false)
    // Calling room.disconnect() before the initial connect completes does not
    // produce a handled Disconnected event (hasConnectedRef is still false),
    // which would leave the user stuck on the meeting screen.
    if (
      !hasConnectedRef.current ||
      room.state === ConnectionState.Disconnected
    ) {
      onLeaveRef.current()
      return
    }
    room.disconnect(true)
  }, [room])

  // The prefab ControlBar owns the Leave button, and its onClick calls
  // room.disconnect() directly. Intercept that click in the capture phase so
  // an accidental tap asks for confirmation instead of leaving immediately.
  const handleLeaveClickCapture = React.useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      const target = event.target
      if (
        !(target instanceof Element) ||
        !target.closest('.lk-disconnect-button')
      ) {
        return
      }
      event.preventDefault()
      event.stopPropagation()
      setLeaveConfirmOpen(true)
    },
    [],
  )

  const Settings = React.useMemo(
    () =>
      function SettingsForMeeting() {
        return <SettingsMenu config={config} />
      },
    [config],
  )

  return (
    <div
      className={`lk-room-container${ios ? ' is-ios' : ''}`}
      onClickCapture={handleLeaveClickCapture}
    >
      <RoomContext.Provider value={room}>
        <KeyboardShortcuts />
        <LocalizedVideoConference
          chatMessageFormatter={formatChatMessageLinks}
          SettingsComponent={
            config.enableSettingsMenu ? Settings : undefined
          }
        />
        {config.showDebugPanel ? <DebugMode /> : null}
        <RecordingIndicator />
      </RoomContext.Provider>
      <LeaveConfirmDialog
        open={leaveConfirmOpen}
        onCancel={closeLeaveConfirm}
        onConfirm={confirmLeave}
      />
      {showEmptyHint && participantCount <= 1 ? (
        <div className="empty-room-hint">{localizedMessages.noOneElse}</div>
      ) : null}
    </div>
  )
}
