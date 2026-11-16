import {
  ChatIcon,
  ChatToggle,
  DisconnectButton,
  GearIcon,
  LeaveIcon,
  MediaDeviceMenu,
  StartMediaButton,
  TrackToggle,
  useLocalParticipantPermissions,
  useMaybeLayoutContext,
  usePersistentUserChoices,
  type ControlBarControls,
  type ControlBarProps,
} from '@livekit/components-react'
import { Track } from 'livekit-client'
import * as React from 'react'
import { useMessages } from '../../i18n'
import { useMediaQuery } from './useMediaQuery'

const trackSourceToProtocol = (source: Track.Source) => {
  switch (source) {
    case Track.Source.Camera:
      return 1
    case Track.Source.Microphone:
      return 2
    case Track.Source.ScreenShare:
      return 3
    default:
      return 0
  }
}

function browserSupportsScreenSharing(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    typeof navigator.mediaDevices?.getDisplayMedia === 'function'
  )
}

function joinClassNames(...values: Array<string | undefined>): string {
  return values.filter(Boolean).join(' ')
}

function SettingsToggle({
  showIcon,
  showText,
  label,
}: {
  showIcon: boolean
  showText: boolean
  label: string
}) {
  const layoutContext = useMaybeLayoutContext()
  const pressed = layoutContext?.widget.state?.showSettings ?? false
  return (
    <button
      type="button"
      className="lk-button lk-settings-toggle"
      aria-label={label}
      aria-pressed={pressed}
      onClick={() =>
        layoutContext?.widget.dispatch?.({ msg: 'toggle_settings' })
      }
    >
      {showIcon && <GearIcon />}
      {showText && label}
    </button>
  )
}

export function LocalizedControlBar({
  variation,
  controls,
  saveUserChoices = true,
  onDeviceError,
  className,
  ...props
}: ControlBarProps) {
  const messages = useMessages()
  const layoutContext = useMaybeLayoutContext()
  const localPermissions = useLocalParticipantPermissions()
  const isChatOpen = layoutContext?.widget.state?.showChat ?? false
  const isTooLittleSpace = useMediaQuery(
    `(max-width: ${isChatOpen ? 1000 : 760}px)`,
  )
  const activeVariation =
    variation ?? (isTooLittleSpace ? 'minimal' : 'verbose')
  const showIcon =
    activeVariation === 'minimal' || activeVariation === 'verbose'
  const showText =
    activeVariation === 'textOnly' || activeVariation === 'verbose'

  const visibleControls: ControlBarControls = { leave: true, ...controls }
  if (!localPermissions) {
    visibleControls.camera = false
    visibleControls.chat = false
    visibleControls.microphone = false
    visibleControls.screenShare = false
  } else {
    const canPublishSource = (source: Track.Source) => {
      return (
        localPermissions.canPublish &&
        (localPermissions.canPublishSources.length === 0 ||
          localPermissions.canPublishSources.includes(
            trackSourceToProtocol(source),
          ))
      )
    }
    visibleControls.camera ??= canPublishSource(Track.Source.Camera)
    visibleControls.microphone ??= canPublishSource(Track.Source.Microphone)
    visibleControls.screenShare ??= canPublishSource(Track.Source.ScreenShare)
    visibleControls.chat ??= localPermissions.canPublishData && controls?.chat
  }

  const [isScreenShareEnabled, setIsScreenShareEnabled] =
    React.useState(false)
  const onScreenShareChange = React.useCallback((enabled: boolean) => {
    setIsScreenShareEnabled(enabled)
  }, [])

  const {
    saveAudioInputEnabled,
    saveVideoInputEnabled,
    saveAudioInputDeviceId,
    saveVideoInputDeviceId,
  } = usePersistentUserChoices({ preventSave: !saveUserChoices })

  const microphoneOnChange = React.useCallback(
    (enabled: boolean, isUserInitiated: boolean) =>
      isUserInitiated ? saveAudioInputEnabled(enabled) : null,
    [saveAudioInputEnabled],
  )
  const cameraOnChange = React.useCallback(
    (enabled: boolean, isUserInitiated: boolean) =>
      isUserInitiated ? saveVideoInputEnabled(enabled) : null,
    [saveVideoInputEnabled],
  )

  return (
    <div {...props} className={joinClassNames('lk-control-bar', className)}>
      {visibleControls.microphone && (
        <div className="lk-button-group">
          <TrackToggle
            source={Track.Source.Microphone}
            showIcon={showIcon}
            aria-label={messages.settingsMicrophone}
            title={messages.settingsMicrophone}
            onChange={microphoneOnChange}
            onDeviceError={(error) =>
              onDeviceError?.({ source: Track.Source.Microphone, error })
            }
          >
            {showText && messages.settingsMicrophone}
          </TrackToggle>
          <div className="lk-button-group-menu">
            <MediaDeviceMenu
              kind="audioinput"
              onActiveDeviceChange={(_kind, deviceId) =>
                saveAudioInputDeviceId(deviceId ?? 'default')
              }
            />
          </div>
        </div>
      )}

      {visibleControls.camera && (
        <div className="lk-button-group">
          <TrackToggle
            source={Track.Source.Camera}
            showIcon={showIcon}
            aria-label={messages.settingsCamera}
            title={messages.settingsCamera}
            onChange={cameraOnChange}
            onDeviceError={(error) =>
              onDeviceError?.({ source: Track.Source.Camera, error })
            }
          >
            {showText && messages.settingsCamera}
          </TrackToggle>
          <div className="lk-button-group-menu">
            <MediaDeviceMenu
              kind="videoinput"
              onActiveDeviceChange={(_kind, deviceId) =>
                saveVideoInputDeviceId(deviceId ?? 'default')
              }
            />
          </div>
        </div>
      )}

      {visibleControls.screenShare && browserSupportsScreenSharing() && (
        <TrackToggle
          source={Track.Source.ScreenShare}
          captureOptions={{ audio: true, selfBrowserSurface: 'include' }}
          showIcon={showIcon}
          aria-label={
            isScreenShareEnabled
              ? messages.stopScreenShare
              : messages.screenShare
          }
          title={
            isScreenShareEnabled
              ? messages.stopScreenShare
              : messages.screenShare
          }
          onChange={onScreenShareChange}
          onDeviceError={(error) =>
            onDeviceError?.({ source: Track.Source.ScreenShare, error })
          }
        >
          {showText &&
            (isScreenShareEnabled
              ? messages.stopScreenShare
              : messages.screenShare)}
        </TrackToggle>
      )}

      {visibleControls.chat && (
        <ChatToggle
          aria-label={messages.chat}
          title={messages.chat}
        >
          {showIcon && <ChatIcon />}
          {showText && messages.chat}
        </ChatToggle>
      )}

      {visibleControls.settings && (
        <SettingsToggle
          showIcon={showIcon}
          showText={showText}
          label={messages.settingsMedia}
        />
      )}

      {visibleControls.leave && (
        <DisconnectButton
          aria-label={messages.leave}
          title={messages.leave}
        >
          {showIcon && <LeaveIcon />}
          {showText && messages.leave}
        </DisconnectButton>
      )}

      <StartMediaButton />
    </div>
  )
}
