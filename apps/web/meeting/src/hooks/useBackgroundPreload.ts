import * as React from 'react'
import {
  getBackgroundCapabilities,
  preloadBackgroundAssets,
  shouldPreloadBackgroundEffects,
  type BackgroundAssets,
} from '../backgroundAssets'
import type { BackgroundEffectsPreload } from '../config'

export type BackgroundPreloadPhase =
  | 'idle'
  | 'preloading'
  | 'ready'
  | 'partial'
  | 'error'
  | 'skipped'

export type UseBackgroundPreloadOptions = {
  enabled: boolean
  preload: BackgroundEffectsPreload
  supported: boolean
  connected: boolean
  bypass: boolean
  mediapipeAssetBase: string
}

export type BackgroundPreloadState = {
  phase: BackgroundPreloadPhase
  assets: BackgroundAssets | null
}

const PRELOAD_DELAY_MS = 2500
const PRELOAD_TIMEOUT_MS = 15_000

export function useBackgroundPreload({
  enabled,
  preload,
  supported,
  connected,
  bypass,
  mediapipeAssetBase,
}: UseBackgroundPreloadOptions): BackgroundPreloadState {
  const [phase, setPhase] =
    React.useState<BackgroundPreloadPhase>('idle')
  const [assets, setAssets] = React.useState<BackgroundAssets | null>(null)
  const [visible, setVisible] = React.useState(() =>
    typeof document === 'undefined'
      ? false
      : document.visibilityState === 'visible',
  )
  const startedRef = React.useRef(false)
  const abortRef = React.useRef<AbortController | null>(null)
  const delayTimerRef = React.useRef<number | undefined>(undefined)
  const idleHandleRef = React.useRef<number | undefined>(undefined)
  const timeoutRef = React.useRef<number | undefined>(undefined)

  React.useEffect(() => {
    if (typeof document === 'undefined') return
    const handleVisibility = () => {
      setVisible(document.visibilityState === 'visible')
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () =>
      document.removeEventListener('visibilitychange', handleVisibility)
  }, [])

  React.useEffect(() => {
    return () => {
      abortRef.current?.abort()
      if (delayTimerRef.current !== undefined) {
        window.clearTimeout(delayTimerRef.current)
      }
      if (idleHandleRef.current !== undefined) {
        window.cancelIdleCallback?.(idleHandleRef.current)
      }
      if (timeoutRef.current !== undefined) {
        window.clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  React.useEffect(() => {
    if (startedRef.current) return

    if (bypass) {
      if (phase === 'idle') setPhase('skipped')
      return
    }
    if (!enabled || !supported || preload === 'off') {
      setPhase('skipped')
      return
    }
    if (!connected || !visible) return

    const capabilities = getBackgroundCapabilities(supported)
    if (
      !shouldPreloadBackgroundEffects({
        enabled,
        preload,
        capabilities,
      })
    ) {
      setPhase('skipped')
      return
    }

    const start = () => {
      startedRef.current = true
      setPhase('preloading')
      const controller = new AbortController()
      abortRef.current = controller
      timeoutRef.current = window.setTimeout(() => {
        // Stop waiting, but do not abort the in-flight fetches: the processor
        // may still reuse the cache/in-flight responses after the fallback.
        timeoutRef.current = undefined
        setPhase('error')
      }, PRELOAD_TIMEOUT_MS)

      void preloadBackgroundAssets({
        mediapipeAssetBase,
        signal: controller.signal,
      })
        .then((result) => {
          if (controller.signal.aborted) return
          if (timeoutRef.current !== undefined) {
            window.clearTimeout(timeoutRef.current)
            timeoutRef.current = undefined
          }
          setAssets(result)
          setPhase(
            result.essentialFailure
              ? 'error'
              : result.partialFailure
                ? 'partial'
                : 'ready',
          )
        })
        .catch(() => {
          if (controller.signal.aborted) return
          if (timeoutRef.current !== undefined) {
            window.clearTimeout(timeoutRef.current)
            timeoutRef.current = undefined
          }
          setPhase('error')
        })
    }

    delayTimerRef.current = window.setTimeout(() => {
      delayTimerRef.current = undefined
      if (typeof window.requestIdleCallback === 'function') {
        idleHandleRef.current = window.requestIdleCallback(start, {
          timeout: 2000,
        })
      } else {
        start()
      }
    }, PRELOAD_DELAY_MS)

    return () => {
      if (delayTimerRef.current !== undefined) {
        window.clearTimeout(delayTimerRef.current)
        delayTimerRef.current = undefined
      }
      if (idleHandleRef.current !== undefined) {
        window.cancelIdleCallback?.(idleHandleRef.current)
        idleHandleRef.current = undefined
      }
    }
  }, [
    bypass,
    connected,
    enabled,
    mediapipeAssetBase,
    phase,
    preload,
    supported,
    visible,
  ])

  return { phase, assets }
}
