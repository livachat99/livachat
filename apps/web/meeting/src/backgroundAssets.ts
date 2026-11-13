import { FilesetResolver } from '@mediapipe/tasks-vision'
import type { BackgroundEffectsPreload } from './config'

export const DEFAULT_TASKS_VISION_VERSION = '0.10.14'
export const DEFAULT_WASM_BASE =
  `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${DEFAULT_TASKS_VISION_VERSION}/wasm`
export const DEFAULT_MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite'

export type BackgroundAssetPaths = {
  wasmBase: string
  modelUrl: string
}

export type BackgroundAssets = BackgroundAssetPaths & {
  partialFailure: boolean
  essentialFailure: boolean
}

export type BackgroundPreloadCapabilities = {
  supported: boolean
  visible: boolean
  saveData: boolean
  effectiveType?: string
  hardwareConcurrency?: number
  deviceMemory?: number
}

export type BackgroundPreloadOptions = {
  enabled: boolean
  preload: BackgroundEffectsPreload
  capabilities: BackgroundPreloadCapabilities
}

type NavigatorWithConnection = Navigator & {
  connection?: {
    saveData?: boolean
    effectiveType?: string
  }
  deviceMemory?: number
}

export function resolveBackgroundAssetPaths(
  mediapipeAssetBase: string,
): BackgroundAssetPaths {
  return {
    wasmBase: mediapipeAssetBase
      ? `${mediapipeAssetBase}wasm`
      : DEFAULT_WASM_BASE,
    modelUrl: mediapipeAssetBase
      ? `${mediapipeAssetBase}selfie_segmenter.tflite`
      : DEFAULT_MODEL_URL,
  }
}

export function getBackgroundCapabilities(
  supported: boolean,
): BackgroundPreloadCapabilities {
  if (typeof navigator === 'undefined') {
    return {
      supported,
      visible: false,
      saveData: false,
    }
  }

  const nav = navigator as NavigatorWithConnection
  return {
    supported,
    visible:
      typeof document !== 'undefined' &&
      document.visibilityState === 'visible',
    saveData: Boolean(nav.connection?.saveData),
    effectiveType: nav.connection?.effectiveType,
    hardwareConcurrency:
      typeof nav.hardwareConcurrency === 'number'
        ? nav.hardwareConcurrency
        : undefined,
    deviceMemory:
      typeof nav.deviceMemory === 'number' ? nav.deviceMemory : undefined,
  }
}

export function shouldPreloadBackgroundEffects({
  enabled,
  preload,
  capabilities,
}: BackgroundPreloadOptions): boolean {
  if (!enabled || preload === 'off') return false
  if (!capabilities.supported || !capabilities.visible) return false
  if (capabilities.saveData) return false
  if (
    capabilities.effectiveType &&
    ['slow-2g', '2g', '3g'].includes(capabilities.effectiveType)
  ) {
    return false
  }
  if (
    capabilities.hardwareConcurrency !== undefined &&
    capabilities.hardwareConcurrency < 4
  ) {
    return false
  }
  if (
    capabilities.deviceMemory !== undefined &&
    capabilities.deviceMemory < 4
  ) {
    return false
  }
  return true
}

async function fetchAsset(url: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(url, {
    cache: 'force-cache',
    credentials: 'omit',
    signal,
  })
  if (!response.ok) {
    throw new Error(`asset fetch failed: ${response.status} ${url}`)
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (url.endsWith('.wasm') && !contentType.includes('application/wasm')) {
    throw new Error(`unexpected wasm content-type: ${contentType}`)
  }
  if (contentType.includes('text/html')) {
    throw new Error(`asset response looks like HTML: ${url}`)
  }

  // Consume the body so the browser actually downloads and caches it.
  await response.arrayBuffer()
}

export async function preloadBackgroundAssets({
  mediapipeAssetBase,
  signal,
}: {
  mediapipeAssetBase: string
  signal?: AbortSignal
}): Promise<BackgroundAssets> {
  const paths = resolveBackgroundAssetPaths(mediapipeAssetBase)
  const fileset = await FilesetResolver.forVisionTasks(paths.wasmBase)

  const results = await Promise.allSettled([
    fetchAsset(fileset.wasmLoaderPath, signal),
    fetchAsset(fileset.wasmBinaryPath, signal),
    fetchAsset(paths.modelUrl, signal),
  ])

  const loaderFailed = results[0].status === 'rejected'
  const wasmFailed = results[1].status === 'rejected'
  const modelFailed = results[2].status === 'rejected'
  const partialFailure = loaderFailed || wasmFailed || modelFailed

  return {
    ...paths,
    partialFailure,
    essentialFailure: loaderFailed && wasmFailed,
  }
}
