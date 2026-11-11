import { useRoomContext } from '@livekit/components-react'
import {
  LogLevel,
  setLogLevel,
  type RemoteTrackPublication,
} from 'livekit-client'
import * as React from 'react'
import { tinykeys } from 'tinykeys'
import { isEditableTarget } from '../utils'
import styles from './DebugMode.module.css'

function useDebugMode(logLevel?: LogLevel) {
  const room = useRoomContext()

  React.useEffect(() => {
    setLogLevel(logLevel ?? LogLevel.debug)
    ;(window as unknown as { __lk_room?: unknown }).__lk_room = room
    return () => {
      ;(window as unknown as { __lk_room?: unknown }).__lk_room = undefined
    }
  }, [room, logLevel])
}

function trackStatus(track: RemoteTrackPublication): string {
  if (!track.isSubscribed) return 'unsubscribed'
  return track.isEnabled ? 'enabled' : 'disabled'
}

export function DebugMode({ logLevel }: { logLevel?: LogLevel }) {
  const room = useRoomContext()
  const [open, setOpen] = React.useState(false)
  const [roomSid, setRoomSid] = React.useState('')
  const [, forceRender] = React.useState({})

  useDebugMode(logLevel)

  React.useEffect(() => {
    void room
      .getSid()
      .then(setRoomSid)
      .catch(() => setRoomSid(''))
  }, [room])

  React.useEffect(() => {
    const unsubscribe = tinykeys(window, {
      'Shift+D': (event) => {
        if (event.repeat || isEditableTarget(event.target)) return
        event.preventDefault()
        setOpen((value) => !value)
      },
    })
    return unsubscribe
  }, [])

  React.useEffect(() => {
    if (!open) return
    const interval = window.setInterval(() => forceRender({}), 1000)
    return () => window.clearInterval(interval)
  }, [open])

  if (!open) return null

  const local = room.localParticipant

  return (
    <div className={styles.overlay}>
      <h3>
        Room {room.name} / {roomSid || '...'}
      </h3>
      <details open>
        <summary>
          <b>Local participant: {local.identity}</b>
        </summary>
        <details open className={styles.section}>
          <summary>
            <b>Published tracks</b>
          </summary>
          {Array.from(local.trackPublications.values()).map((track) => (
            <div key={track.trackSid} className={styles.track}>
              <div>
                <i>
                  {track.source?.toString() ?? 'unknown'} {track.trackSid}
                </i>
              </div>
              <table>
                <tbody>
                  <tr>
                    <td>Kind</td>
                    <td>
                      {track.kind}
                      {track.kind === 'video' && track.track?.dimensions
                        ? ` ${track.track.dimensions.width}x${track.track.dimensions.height}`
                        : ''}
                    </td>
                  </tr>
                  <tr>
                    <td>Bitrate</td>
                    <td>
                      {track.track
                        ? `${Math.ceil(track.track.currentBitrate / 1000)} kbps`
                        : '-'}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          ))}
        </details>
        <details open className={styles.section}>
          <summary>
            <b>Permissions</b>
          </summary>
          <table>
            <tbody>
              {local.permissions
                ? Object.entries(local.permissions).map(([key, value]) => (
                    <tr key={key}>
                      <td>{key}</td>
                      <td>
                        {Array.isArray(value) ? value.join(', ') : String(value)}
                      </td>
                    </tr>
                  ))
                : null}
            </tbody>
          </table>
        </details>
      </details>

      <details>
        <summary>
          <b>Remote participants</b>
        </summary>
        {Array.from(room.remoteParticipants.values()).map((participant) => (
          <details key={participant.sid} className={styles.section}>
            <summary>
              <b>{participant.identity}</b>
            </summary>
            {Array.from(participant.trackPublications.values()).map((track) => (
              <div key={track.trackSid} className={styles.track}>
                <div>
                  <i>
                    {track.source?.toString() ?? 'unknown'} {track.trackSid}
                  </i>
                </div>
                <table>
                  <tbody>
                    <tr>
                      <td>Kind</td>
                      <td>
                        {track.kind}
                        {track.kind === 'video' && track.dimensions
                          ? ` ${track.dimensions.width}x${track.dimensions.height}`
                          : ''}
                      </td>
                    </tr>
                    <tr>
                      <td>Status</td>
                      <td>{trackStatus(track)}</td>
                    </tr>
                    {track.track ? (
                      <tr>
                        <td>Bitrate</td>
                        <td>
                          {Math.ceil(track.track.currentBitrate / 1000)} kbps
                        </td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
            ))}
          </details>
        ))}
      </details>
    </div>
  )
}
