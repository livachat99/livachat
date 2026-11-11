import {
  MediaDeviceMenu,
  TrackToggle,
  VideoTrack,
  useLocalParticipant,
} from '@livekit/components-react'
import {
  BackgroundProcessor,
  supportsBackgroundProcessors,
  type BackgroundProcessorWrapper,
  type SwitchBackgroundProcessorOptions,
} from '@livekit/track-processors'
import {
  ParticipantEvent,
  Track,
  isLocalTrack,
  type LocalTrackPublication,
  type LocalVideoTrack,
} from 'livekit-client'
import * as React from 'react'
import toast from 'react-hot-toast'
import DeskBackground from '../assets/backgrounds/desk.svg'
import NatureBackground from '../assets/backgrounds/nature.svg'
import { messages } from '../messages'
import { errorMessage } from '../utils'

type BackgroundType = 'none' | 'blur' | 'image'

// supportsBackgroundProcessors() probes WebGL2 by creating a canvas context.
// CameraSettings re-renders frequently, so cache the result instead of
// creating a fresh context on every render.
let backgroundEffectsSupported: boolean | undefined

function canUseBackgroundEffects(): boolean {
  if (backgroundEffectsSupported === undefined) {
    backgroundEffectsSupported = supportsBackgroundProcessors()
  }
  return backgroundEffectsSupported
}

const BACKGROUND_IMAGES = [
  { name: messages.backgroundDesk, path: DeskBackground },
  { name: messages.backgroundNature, path: NatureBackground },
]

export type CameraSettingsProps = {
  enableBackgroundEffects: boolean
  mediapipeAssetBase: string
}

export function CameraSettings({
  enableBackgroundEffects,
  mediapipeAssetBase,
}: CameraSettingsProps) {
  const { cameraTrack, localParticipant } = useLocalParticipant()
  const [backgroundType, setBackgroundType] =
    React.useState<BackgroundType>('none')
  const [imagePath, setImagePath] = React.useState<string | null>(null)
  const [trackRevision, setTrackRevision] = React.useState(0)
  const processorRef = React.useRef<BackgroundProcessorWrapper | null>(null)
  const effectsActive = backgroundType !== 'none'

  // A full reconnect or a camera republish replaces the local track and
  // destroys its processor. Bump the revision so the attach effect runs again.
  React.useEffect(() => {
    const handleLocalTrackPublished = (publication: LocalTrackPublication) => {
      if (publication.source === Track.Source.Camera) {
        setTrackRevision((value) => value + 1)
      }
    }
    localParticipant.on(
      ParticipantEvent.LocalTrackPublished,
      handleLocalTrackPublished,
    )
    return () => {
      localParticipant.off(
        ParticipantEvent.LocalTrackPublished,
        handleLocalTrackPublished,
      )
    }
  }, [localParticipant])

  const camTrackRef = React.useMemo(() => {
    return cameraTrack
      ? {
          participant: localParticipant,
          publication: cameraTrack,
          source: Track.Source.Camera,
        }
      : undefined
  }, [localParticipant, cameraTrack])

  const onProcessorError = React.useCallback((error: unknown) => {
    console.error(error)
    toast.error(
      `${messages.backgroundEffectError}: ${errorMessage(error)}`,
    )
  }, [])

  const modeOptions = React.useCallback((): SwitchBackgroundProcessorOptions => {
    if (backgroundType === 'blur') {
      return { mode: 'background-blur', blurRadius: 10 }
    }
    if (backgroundType === 'image' && imagePath) {
      return { mode: 'virtual-background', imagePath }
    }
    return { mode: 'disabled' }
  }, [backgroundType, imagePath])

  const getProcessor = React.useCallback((): BackgroundProcessorWrapper => {
    if (processorRef.current) return processorRef.current
    processorRef.current = BackgroundProcessor({
      mode: 'disabled',
      ...(mediapipeAssetBase
        ? {
            assetPaths: {
              tasksVisionFileSet: `${mediapipeAssetBase}wasm`,
              modelAssetPath: `${mediapipeAssetBase}selfie_segmenter.tflite`,
            },
          }
        : {}),
    })
    return processorRef.current
  }, [mediapipeAssetBase])

  const modeOptionsRef = React.useRef<SwitchBackgroundProcessorOptions>({
    mode: 'disabled',
  })
  React.useEffect(() => {
    modeOptionsRef.current = modeOptions()
  }, [modeOptions])

  React.useEffect(() => {
    // Only instantiate the processor after the user actually selects an
    // effect. MediaPipe loads ~9 MB of WASM plus the segmentation model on
    // first init, which should not happen for every participant who joins
    // with a camera and never opens the background settings.
    if (!enableBackgroundEffects || !effectsActive) return
    if (!canUseBackgroundEffects()) return
    const track = cameraTrack?.track
    if (!isLocalTrack(track)) return
    try {
      const processor = getProcessor()
      const localTrack = track as LocalVideoTrack
      // Apply the current mode immediately. Otherwise selecting a background
      // before the camera is enabled would leave a fresh processor disabled.
      void processor.switchTo(modeOptionsRef.current).catch(onProcessorError)
      // setProcessor() re-initializes the processor. Skip it when this track
      // already carries our processor (e.g. the extra render caused by the
      // LocalTrackPublished event), so reconnects do not reload MediaPipe.
      if (localTrack.getProcessor() !== processor) {
        void localTrack.setProcessor(processor).catch(onProcessorError)
      }
    } catch (error) {
      onProcessorError(error)
    }
  }, [
    cameraTrack,
    enableBackgroundEffects,
    effectsActive,
    getProcessor,
    onProcessorError,
    trackRevision,
  ])

  React.useEffect(() => {
    const processor = processorRef.current
    if (!processor) return
    void processor.switchTo(modeOptions()).catch(onProcessorError)
  }, [modeOptions, onProcessorError])

  const selectBackground = (type: BackgroundType, path?: string) => {
    setBackgroundType(type)
    setImagePath(type === 'image' && path ? path : null)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {camTrackRef ? (
        <VideoTrack
          trackRef={camTrackRef}
          style={{
            maxHeight: '280px',
            objectFit: 'contain',
            objectPosition: 'right',
            transform: 'scaleX(-1)',
          }}
        />
      ) : null}

      <section className="lk-button-group">
        <TrackToggle source={Track.Source.Camera}>Camera</TrackToggle>
        <div className="lk-button-group-menu">
          <MediaDeviceMenu kind="videoinput" />
        </div>
      </section>

      {enableBackgroundEffects && canUseBackgroundEffects() ? (
        <div style={{ marginTop: '10px' }}>
          <div style={{ marginBottom: '8px' }}>Background effects</div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              className="lk-button"
              type="button"
              aria-pressed={backgroundType === 'none'}
              onClick={() => selectBackground('none')}
            >
              {messages.backgroundNone}
            </button>
            <button
              className="lk-button"
              type="button"
              aria-pressed={backgroundType === 'blur'}
              onClick={() => selectBackground('blur')}
            >
              {messages.backgroundBlur}
            </button>
            {BACKGROUND_IMAGES.map((image) => (
              <button
                key={image.path}
                className="lk-button"
                type="button"
                aria-pressed={
                  backgroundType === 'image' && imagePath === image.path
                }
                onClick={() => selectBackground('image', image.path)}
                style={{
                  backgroundImage: `url(${image.path})`,
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                  width: '80px',
                  height: '60px',
                }}
              >
                {image.name}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
