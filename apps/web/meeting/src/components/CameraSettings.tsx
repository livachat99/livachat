import {
  MediaDeviceMenu,
  TrackToggle,
  VideoTrack,
  useConnectionState,
  useLocalParticipant,
} from '@livekit/components-react'
import {
  BackgroundProcessor,
  supportsBackgroundProcessors,
  type BackgroundProcessorWrapper,
  type SwitchBackgroundProcessorOptions,
} from '@livekit/track-processors'
import {
  ConnectionState,
  ParticipantEvent,
  Track,
  isLocalTrack,
  type LocalTrackPublication,
  type LocalVideoTrack,
} from 'livekit-client'
import * as React from 'react'
import toast from 'react-hot-toast'
import BlurThumbnail from '../assets/backgrounds/blur.svg'
import DeskThumbnail from '../assets/backgrounds/desk-thumb.webp'
import DeskBackground from '../assets/backgrounds/desk.webp'
import FlowerThumbnail from '../assets/backgrounds/flower-thumb.webp'
import FlowerBackground from '../assets/backgrounds/flower.webp'
import LakeThumbnail from '../assets/backgrounds/lake-thumb.webp'
import LakeBackground from '../assets/backgrounds/lake.webp'
import NatureThumbnail from '../assets/backgrounds/nature-thumb.webp'
import NatureBackground from '../assets/backgrounds/nature.webp'
import { resolveBackgroundAssetPaths } from '../backgroundAssets'
import {
  toBackgroundImageOptions,
  type BackgroundImageOption,
} from '../backgroundImages'
import type {
  BackgroundEffectsPreload,
  BackgroundImageConfig,
} from '../config'
import { useBackgroundPreload } from '../hooks/useBackgroundPreload'
import { messages } from '../messages'
import { errorMessage } from '../utils'
import styles from './CameraSettings.module.css'

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

const BACKGROUND_IMAGES: BackgroundImageOption[] = [
  {
    name: messages.backgroundDesk,
    path: DeskBackground,
    thumbnail: DeskThumbnail,
  },
  {
    name: messages.backgroundNature,
    path: NatureBackground,
    thumbnail: NatureThumbnail,
  },
  {
    name: messages.backgroundLake,
    path: LakeBackground,
    thumbnail: LakeThumbnail,
  },
  {
    name: messages.backgroundFlower,
    path: FlowerBackground,
    thumbnail: FlowerThumbnail,
  },
]

function thumbnailStyle(url: string): React.CSSProperties {
  return { backgroundImage: `url(${JSON.stringify(url)})` }
}

export type CameraSettingsProps = {
  enableBackgroundEffects: boolean
  backgroundEffectsPreload: BackgroundEffectsPreload
  backgroundImages: BackgroundImageConfig[]
  mediapipeAssetBase: string
}

export function CameraSettings({
  enableBackgroundEffects,
  backgroundEffectsPreload,
  backgroundImages,
  mediapipeAssetBase,
}: CameraSettingsProps) {
  const { cameraTrack, localParticipant, isCameraEnabled } =
    useLocalParticipant()
  const connectionState = useConnectionState()
  const connected = connectionState === ConnectionState.Connected
  const [backgroundType, setBackgroundType] =
    React.useState<BackgroundType>('none')
  const [imagePath, setImagePath] = React.useState<string | null>(null)
  const [trackRevision, setTrackRevision] = React.useState(0)
  const [loading, setLoading] = React.useState(false)
  const [processorReady, setProcessorReady] = React.useState(false)
  const processorRef = React.useRef<BackgroundProcessorWrapper | null>(null)
  const attachInFlightRef = React.useRef<{
    track: LocalVideoTrack
    processor: BackgroundProcessorWrapper
    promise: Promise<unknown>
  } | null>(null)
  const firstFrameRef = React.useRef(false)
  const effectsActive = backgroundType !== 'none'
  const preload = useBackgroundPreload({
    enabled: enableBackgroundEffects,
    preload: backgroundEffectsPreload,
    supported: canUseBackgroundEffects(),
    connected,
    bypass: effectsActive,
    mediapipeAssetBase,
  })
  const imageOptions = React.useMemo(() => {
    const origin =
      typeof window === 'undefined'
        ? 'http://localhost'
        : window.location.origin
    return [
      ...BACKGROUND_IMAGES,
      ...toBackgroundImageOptions(
        backgroundImages,
        import.meta.env.BASE_URL,
        origin,
      ),
    ]
  }, [backgroundImages])

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
    setLoading(false)
    setProcessorReady(false)
    firstFrameRef.current = false
    setBackgroundType('none')
    setImagePath(null)
  }, [])

  const handleFrameProcessed = React.useCallback(() => {
    if (firstFrameRef.current) return
    firstFrameRef.current = true
    setProcessorReady(true)
    setLoading(false)
  }, [])

  const modeOptions = React.useCallback((): SwitchBackgroundProcessorOptions => {
    if (backgroundType === 'blur') {
      return { mode: 'background-blur', blurRadius: 16 }
    }
    if (backgroundType === 'image' && imagePath) {
      return { mode: 'virtual-background', imagePath }
    }
    return { mode: 'disabled' }
  }, [backgroundType, imagePath])

  const getProcessor = React.useCallback((): BackgroundProcessorWrapper => {
    if (processorRef.current) return processorRef.current
    const paths =
      preload.assets ?? resolveBackgroundAssetPaths(mediapipeAssetBase)
    processorRef.current = BackgroundProcessor({
      mode: 'disabled',
      assetPaths: {
        tasksVisionFileSet: paths.wasmBase,
        modelAssetPath: paths.modelUrl,
      },
      onFrameProcessed: handleFrameProcessed,
    })
    return processorRef.current
  }, [handleFrameProcessed, mediapipeAssetBase, preload.assets])

  const modeOptionsRef = React.useRef<SwitchBackgroundProcessorOptions>({
    mode: 'disabled',
  })
  React.useEffect(() => {
    modeOptionsRef.current = modeOptions()
  }, [modeOptions])

  React.useEffect(() => {
    // Only instantiate the processor after the user actually selects an
    // effect. Resource preloading never attaches a processor.
    if (!enableBackgroundEffects || !effectsActive) return
    if (!canUseBackgroundEffects()) return
    if (preload.phase === 'preloading') return
    const track = cameraTrack?.track
    if (!isLocalTrack(track)) return
    try {
      const processor = getProcessor()
      const localTrack = track as LocalVideoTrack
      // Apply the current mode immediately. Otherwise selecting a background
      // before the camera is enabled would leave a fresh processor disabled.
      void processor.switchTo(modeOptionsRef.current).catch(onProcessorError)
      // setProcessor() re-initializes the processor. Skip it when this track
      // already carries our processor, and also while an attach for the same
      // track/processor is still in flight (e.g. preload assets arrive while
      // setProcessor is running), so we never initialize MediaPipe twice.
      if (localTrack.getProcessor() !== processor) {
        const inFlight = attachInFlightRef.current
        if (
          !inFlight ||
          inFlight.track !== localTrack ||
          inFlight.processor !== processor
        ) {
          firstFrameRef.current = false
          setProcessorReady(false)
          const entry = {
            track: localTrack,
            processor,
            promise: Promise.resolve() as Promise<unknown>,
          }
          entry.promise = localTrack
            .setProcessor(processor)
            .catch(onProcessorError)
            .finally(() => {
              if (attachInFlightRef.current === entry) {
                attachInFlightRef.current = null
              }
            })
          attachInFlightRef.current = entry
        }
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
    preload.phase,
    trackRevision,
  ])

  React.useEffect(() => {
    const processor = processorRef.current
    if (!processor) return
    void processor.switchTo(modeOptions()).catch(onProcessorError)
  }, [modeOptions, onProcessorError])

  React.useEffect(() => {
    if (!isCameraEnabled) {
      setLoading(false)
      firstFrameRef.current = false
      return
    }
    if (effectsActive && !processorReady) setLoading(true)
  }, [effectsActive, isCameraEnabled, processorReady])

  const selectBackground = (type: BackgroundType, path?: string) => {
    if (type !== 'none' && !isCameraEnabled) return
    setBackgroundType(type)
    setImagePath(type === 'image' && path ? path : null)
    if (type === 'none') {
      setLoading(false)
      return
    }
    if (!processorReady || !processorRef.current) {
      firstFrameRef.current = false
      setProcessorReady(false)
      setLoading(true)
    }
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
          {!isCameraEnabled ? (
            <p
              style={{
                margin: '0 0 8px',
                color: 'rgba(255, 255, 255, 0.65)',
                fontSize: '0.85rem',
              }}
            >
              {messages.backgroundCameraRequired}
            </p>
          ) : null}
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            <button
              className={`lk-button ${styles.backgroundOption}`}
              type="button"
              aria-pressed={backgroundType === 'none'}
              onClick={() => selectBackground('none')}
            >
              {messages.backgroundNone}
            </button>
            <button
              className={`lk-button ${styles.backgroundOption} ${styles.thumbnailOption}`}
              type="button"
              aria-pressed={backgroundType === 'blur'}
              disabled={!isCameraEnabled || loading}
              onClick={() => selectBackground('blur')}
              style={thumbnailStyle(BlurThumbnail)}
              title={messages.backgroundBlur}
            >
              <span className={styles.blurLabel}>
                {messages.backgroundBlur}
              </span>
            </button>
            {imageOptions.map((image, index) => (
              <button
                key={`${image.path}-${index}`}
                className={`lk-button ${styles.backgroundOption} ${styles.thumbnailOption}`}
                type="button"
                aria-label={image.name}
                aria-pressed={
                  backgroundType === 'image' && imagePath === image.path
                }
                disabled={!isCameraEnabled || loading}
                onClick={() => selectBackground('image', image.path)}
                style={thumbnailStyle(image.thumbnail)}
                title={image.name}
              />
            ))}
          </div>
          {loading ? (
            <p
              role="status"
              aria-live="polite"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                margin: '8px 0 0',
                color: 'rgba(255, 255, 255, 0.75)',
                fontSize: '0.85rem',
              }}
            >
              <span className="lk-spinner" aria-hidden="true" />
              {messages.backgroundLoading}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
