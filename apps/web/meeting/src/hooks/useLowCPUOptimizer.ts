import {
  ParticipantEvent,
  Room,
  RoomEvent,
  VideoQuality,
  isVideoTrack,
  type LocalVideoTrack,
  type RemoteTrack,
  type RemoteTrackPublication,
} from 'livekit-client'
import * as React from 'react'

export type LowCPUOptimizerOptions = {
  reducePublisherVideoQuality: boolean
  reduceSubscriberVideoQuality: boolean
  disableVideoProcessing: boolean
}

const defaultOptions: LowCPUOptimizerOptions = {
  reducePublisherVideoQuality: true,
  reduceSubscriberVideoQuality: true,
  disableVideoProcessing: false,
}

/**
 * Lowers video quality when the browser reports CPU constraints.
 * Migrated from meet.git/lib/usePerfomanceOptimiser.ts, including the missing
 * `disableVideoProcessing` dependency in the first effect.
 */
export function useLowCPUOptimizer(
  room: Room,
  options: Partial<LowCPUOptimizerOptions> = {},
): boolean {
  const [lowPowerMode, setLowPowerMode] = React.useState(false)
  const opts = React.useMemo(
    () => ({ ...defaultOptions, ...options }),
    [options],
  )

  React.useEffect(() => {
    const handleCpuConstrained = (track: LocalVideoTrack) => {
      setLowPowerMode(true)
      console.warn('Local track CPU constrained', track)
      if (opts.reducePublisherVideoQuality) {
        void track.prioritizePerformance().catch((error: unknown) => {
          console.warn('Failed to reduce publisher video quality', error)
        })
      }
      if (opts.disableVideoProcessing && isVideoTrack(track)) {
        void track.stopProcessor()
      }
      if (opts.reduceSubscriberVideoQuality) {
        room.remoteParticipants.forEach((participant) => {
          participant.videoTrackPublications.forEach((publication) => {
            publication.setVideoQuality(VideoQuality.LOW)
          })
        })
      }
    }

    room.localParticipant.on(
      ParticipantEvent.LocalTrackCpuConstrained,
      handleCpuConstrained,
    )
    return () => {
      room.localParticipant.off(
        ParticipantEvent.LocalTrackCpuConstrained,
        handleCpuConstrained,
      )
    }
  }, [
    room,
    opts.reducePublisherVideoQuality,
    opts.reduceSubscriberVideoQuality,
    opts.disableVideoProcessing,
  ])

  React.useEffect(() => {
    const lowerQuality = (
      _track: RemoteTrack,
      publication: RemoteTrackPublication,
    ) => {
      publication.setVideoQuality(VideoQuality.LOW)
    }
    if (lowPowerMode && opts.reduceSubscriberVideoQuality) {
      room.on(RoomEvent.TrackSubscribed, lowerQuality)
    }
    return () => {
      room.off(RoomEvent.TrackSubscribed, lowerQuality)
    }
  }, [lowPowerMode, room, opts.reduceSubscriberVideoQuality])

  return lowPowerMode
}
