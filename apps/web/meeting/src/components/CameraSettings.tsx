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
import { supportsFloatColorBuffer } from '../backgroundSupport'
import {
  toBackgroundImageOptions,
  type BackgroundImageOption,
} from '../backgroundImages'
import type {
  BackgroundEffectsPreload,
  BackgroundImageConfig,
} from '../config'
import { useBackgroundPreload } from '../hooks/useBackgroundPreload'
import { useMessages, type Messages } from '../i18n'
import { formatMessage } from '../i18n/format'
import { errorMessage } from '../utils'
import styles from './CameraSettings.module.css'

type BackgroundType = 'none' | 'blur' | 'image'

type ProcessorInternals = {
  transformer?: {
    canvas?: HTMLCanvasElement | OffscreenCanvas
  }
}

// track-processors does not recover from a lost WebGL context: the processed
// track keeps emitting black frames. We attach one listener per internal
// canvas and stop the processor if the context is lost. Keyed by canvas, not
// by processor: a camera change/reconnect can restart the processor and
// replace the canvas, and the new canvas needs its own listener.
const canvasesWithContextListener = new WeakSet<
  HTMLCanvasElement | OffscreenCanvas
>()

function isSafariWebKit(): boolean {
  if (typeof navigator === 'undefined') return false
  const ua = navigator.userAgent
  const ios =
    /iP(hone|ad|od)/.test(ua) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  if (ios) return true
  return /Safari\//.test(ua) && !/Chrome|Chromium|Edg|OPR|Android/.test(ua)
}

// Safari keeps showing black after LiveKit swaps the stopped processed track
// back to the raw camera track inside the same MediaStream. Reassigning
// srcObject forces the element to repaint (same workaround LiveKit uses in
// attachToElement, but it only runs when the stream object itself changes).
function refreshSafariVideoElements(track: LocalVideoTrack) {
  if (!isSafariWebKit()) return
  const mediaStreamTrack = track.mediaStreamTrack
  if (!mediaStreamTrack) return
  window.setTimeout(() => {
    document.querySelectorAll('video').forEach((video) => {
      const stream = video.srcObject
      if (!(stream instanceof MediaStream)) return
      if (!stream.getVideoTracks().includes(mediaStreamTrack)) return
      video.srcObject = null
      video.srcObject = stream
      void video.play().catch(() => {})
    })
  }, 0)
}

// supportsBackgroundProcessors() probes WebGL2 by creating a canvas context.
// CameraSettings re-renders frequently, so cache the result instead of
// creating a fresh context on every render.
let backgroundEffectsSupported: boolean | undefined

function canUseBackgroundEffects(): boolean {
  if (backgroundEffectsSupported !== undefined) return backgroundEffectsSupported
  if (!supportsBackgroundProcessors()) {
    backgroundEffectsSupported = false
    return backgroundEffectsSupported
  }
  try {
    const gl = document.createElement('canvas').getContext('webgl2')
    backgroundEffectsSupported = !!gl && supportsFloatColorBuffer(gl)
  } catch {
    backgroundEffectsSupported = false
  }
  return backgroundEffectsSupported
}

function builtInBackgroundImages(
  messages: Messages,
): BackgroundImageOption[] {
  return [
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
}

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
  const messages = useMessages()
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
  const [unsupportedNotice, setUnsupportedNotice] = React.useState(false)
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
      ...builtInBackgroundImages(messages),
      ...toBackgroundImageOptions(
        backgroundImages,
        import.meta.env.BASE_URL,
        origin,
      ),
    ]
  }, [backgroundImages, messages])

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

  const cameraTrackRef = React.useRef(cameraTrack)
  React.useEffect(() => {
    cameraTrackRef.current = cameraTrack
  }, [cameraTrack])

  // Detach the processor and restore the raw camera track. switchTo('disabled')
  // is not enough: track-processors keeps running the segmentation pipeline
  // when backgroundDisabled is set (isDisabled is initialized to false and
  // wins the nullish coalescing), so only stopProcessor() truly stops it.
  const stopProcessorAndRestore = React.useCallback(async () => {
    const processor = processorRef.current
    processorRef.current = null
    setLoading(false)
    setProcessorReady(false)
    firstFrameRef.current = false
    const track = cameraTrackRef.current?.track
    try {
      if (isLocalTrack(track)) {
        const localTrack = track as LocalVideoTrack
        try {
          await localTrack.stopProcessor()
        } finally {
          refreshSafariVideoElements(localTrack)
        }
      } else if (processor) {
        await processor.destroy()
      }
    } catch (error) {
      console.error(error)
    }
  }, [])

  const handleContextLostRef = React.useRef<(event: Event) => void>(() => {})
  const contextLostEventRef = React.useRef((event: Event) => {
    handleContextLostRef.current(event)
  })

  const handleContextLost = React.useCallback(
    (event: Event) => {
      // Ignore events from a canvas that was replaced by a processor restart.
      const currentCanvas = (processorRef.current as ProcessorInternals | null)
        ?.transformer?.canvas
      if (event.target !== currentCanvas) return
      event.preventDefault()
      console.error('Background processor WebGL context lost')
      toast.error(messages.backgroundContextLost)
      setUnsupportedNotice(true)
      setBackgroundType('none')
      setImagePath(null)
      void stopProcessorAndRestore()
    },
    [messages, stopProcessorAndRestore],
  )

  React.useEffect(() => {
    handleContextLostRef.current = handleContextLost
  }, [handleContextLost])

  const attachContextLossListener = React.useCallback(
    (processor: BackgroundProcessorWrapper | null) => {
      if (!processor) return
      const canvas = (processor as ProcessorInternals).transformer?.canvas
      if (!canvas || typeof canvas.addEventListener !== 'function') return
      if (canvasesWithContextListener.has(canvas)) return
      canvas.addEventListener('webglcontextlost', contextLostEventRef.current)
      canvasesWithContextListener.add(canvas)
    },
    [],
  )

  const onProcessorError = React.useCallback((error: unknown) => {
    console.error(error)
    toast.error(
      formatMessage(messages.backgroundEffectErrorWithDetail, {
        detail: errorMessage(error),
      }),
    )
    setLoading(false)
    setProcessorReady(false)
    firstFrameRef.current = false
    setBackgroundType('none')
    setImagePath(null)
  }, [messages.backgroundEffectErrorWithDetail])

  const handleFrameProcessed = React.useCallback(() => {
    attachContextLossListener(processorRef.current)
    if (firstFrameRef.current) return
    firstFrameRef.current = true
    setProcessorReady(true)
    setLoading(false)
  }, [attachContextLossListener])

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
    if (processorRef.current) {
      // The processor may have restarted with a new canvas after a camera
      // track change; attach is a cheap no-op when the canvas is unchanged.
      attachContextLossListener(processorRef.current)
      return processorRef.current
    }
    const paths =
      preload.assets ?? resolveBackgroundAssetPaths(mediapipeAssetBase)
    const processor = BackgroundProcessor({
      mode: 'disabled',
      assetPaths: {
        tasksVisionFileSet: paths.wasmBase,
        modelAssetPath: paths.modelUrl,
      },
      onFrameProcessed: handleFrameProcessed,
    })
    processorRef.current = processor
    // May be a no-op until the processor is initialized; the attach callback
    // and the first processed frame retry once the canvas exists.
    attachContextLossListener(processor)
    return processor
  }, [
    attachContextLossListener,
    handleFrameProcessed,
    mediapipeAssetBase,
    preload.assets,
  ])

  const modeOptionsRef = React.useRef<SwitchBackgroundProcessorOptions>({
    mode: 'disabled',
  })
  React.useEffect(() => {
    modeOptionsRef.current = modeOptions()
  }, [modeOptions])

  React.useEffect(() => {
    // "None" must detach the processor, not switch it to disabled mode:
    // switchTo({ mode: 'disabled' }) keeps the segmentation pipeline running,
    // so the device would keep paying the GPU/CPU cost for the rest of the
    // meeting. Also cover a processor left on the track while this component
    // was unmounted (e.g. the Settings widget remounts with state reset to
    // None): the UI says None, so the track must be raw.
    if (!enableBackgroundEffects) return
    if (effectsActive) return
    const track = cameraTrackRef.current?.track
    const attached = isLocalTrack(track) ? track.getProcessor() : undefined
    if (!processorRef.current && !attached) return
    void stopProcessorAndRestore()
  }, [effectsActive, enableBackgroundEffects, stopProcessorAndRestore])

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
            .then(() => attachContextLossListener(processor))
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
    attachContextLossListener,
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
    setUnsupportedNotice(false)
    if (type !== 'none' && !canUseBackgroundEffects()) {
      setUnsupportedNotice(true)
      return
    }
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
          className={styles.cameraPreview}
        />
      ) : null}

      <section className="lk-button-group">
        <TrackToggle source={Track.Source.Camera}>
          {messages.settingsCamera}
        </TrackToggle>
        <div className="lk-button-group-menu">
          <MediaDeviceMenu kind="videoinput" />
        </div>
      </section>

      {enableBackgroundEffects ? (
        <div style={{ marginTop: '10px' }}>
          <div style={{ marginBottom: '8px' }}>
            {messages.backgroundEffects}
          </div>
          {unsupportedNotice ? (
            <p
              role="alert"
              style={{
                margin: '0 0 8px',
                color: 'rgba(255, 255, 255, 0.75)',
                fontSize: '0.85rem',
              }}
            >
              {messages.backgroundUnsupported}
            </p>
          ) : null}
          {loading ? (
            <p
              role="status"
              aria-live="polite"
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.4rem',
                margin: '0 0 8px',
                color: 'rgba(255, 255, 255, 0.75)',
                fontSize: '0.85rem',
              }}
            >
              <span className="lk-spinner" aria-hidden="true" />
              {messages.backgroundLoading}
            </p>
          ) : null}
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
              className={`lk-button ${styles.backgroundOption} ${styles.thumbnailOption}`}
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
        </div>
      ) : null}
    </div>
  )
}
