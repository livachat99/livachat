import {
  MediaDeviceMenu,
  TrackToggle,
  useMediaDeviceSelect,
} from '@livekit/components-react'
import { Track } from 'livekit-client'
import { messages } from '../messages'
import { cleanDeviceLabel } from '../utils'

export function MicrophoneSettings() {
  const { devices, activeDeviceId } = useMediaDeviceSelect({
    kind: 'audioinput',
  })
  const activeLabel = cleanDeviceLabel(
    devices.find((device) => device.deviceId === activeDeviceId)?.label ||
      devices.find((device) => device.deviceId === 'default')?.label ||
      messages.settingsMicrophone,
  )

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'row',
        gap: '10px',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}
    >
      <section className="lk-button-group">
        <TrackToggle source={Track.Source.Microphone} title={activeLabel}>
          {activeLabel}
        </TrackToggle>
        <div className="lk-button-group-menu">
          <MediaDeviceMenu kind="audioinput" />
        </div>
      </section>
    </div>
  )
}
