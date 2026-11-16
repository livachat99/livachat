import {
  MediaDeviceMenu,
  useMediaDeviceSelect,
} from '@livekit/components-react'
import { useMessages } from '../i18n'
import { cleanDeviceLabel } from '../utils'

export function SpeakerSettings() {
  const messages = useMessages()
  const { devices, activeDeviceId } = useMediaDeviceSelect({
    kind: 'audiooutput',
  })
  const activeLabel = cleanDeviceLabel(
    devices.find((device) => device.deviceId === activeDeviceId)?.label ||
      devices.find((device) => device.deviceId === 'default')?.label ||
      messages.settingsSpeaker,
  )

  return (
    <div style={{ position: 'relative', display: 'inline-flex' }}>
      <MediaDeviceMenu kind="audiooutput" title={activeLabel}>
        {activeLabel}
      </MediaDeviceMenu>
    </div>
  )
}
