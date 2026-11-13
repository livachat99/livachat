import { beforeEach, describe, expect, it, vi } from 'vitest'

const { forVisionTasks } = vi.hoisted(() => ({
  forVisionTasks: vi.fn(),
}))

vi.mock('@mediapipe/tasks-vision', () => ({
  FilesetResolver: { forVisionTasks },
}))

import {
  DEFAULT_MODEL_URL,
  DEFAULT_WASM_BASE,
  getBackgroundCapabilities,
  preloadBackgroundAssets,
  resolveBackgroundAssetPaths,
  shouldPreloadBackgroundEffects,
} from './backgroundAssets'

const capabilities = {
  supported: true,
  visible: true,
  saveData: false,
}

describe('resolveBackgroundAssetPaths', () => {
  it('uses explicit CDN defaults instead of undefined', () => {
    expect(resolveBackgroundAssetPaths('')).toEqual({
      wasmBase: DEFAULT_WASM_BASE,
      modelUrl: DEFAULT_MODEL_URL,
    })
    expect(resolveBackgroundAssetPaths('/meeting/vendor/mediapipe/')).toEqual({
      wasmBase: '/meeting/vendor/mediapipe/wasm',
      modelUrl: '/meeting/vendor/mediapipe/selfie_segmenter.tflite',
    })
  })
})

describe('shouldPreloadBackgroundEffects', () => {
  it('honors config, support, visibility and network constraints', () => {
    const base = {
      enabled: true,
      preload: 'auto' as const,
      capabilities,
    }
    expect(shouldPreloadBackgroundEffects(base)).toBe(true)
    expect(shouldPreloadBackgroundEffects({ ...base, preload: 'off' })).toBe(
      false,
    )
    expect(shouldPreloadBackgroundEffects({ ...base, enabled: false })).toBe(
      false,
    )
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, supported: false },
      }),
    ).toBe(false)
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, visible: false },
      }),
    ).toBe(false)
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, saveData: true },
      }),
    ).toBe(false)
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, effectiveType: '3g' },
      }),
    ).toBe(false)
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, hardwareConcurrency: 2 },
      }),
    ).toBe(false)
    expect(
      shouldPreloadBackgroundEffects({
        ...base,
        capabilities: { ...capabilities, deviceMemory: 2 },
      }),
    ).toBe(false)
  })
})

describe('preloadBackgroundAssets', () => {
  const loaderPath = 'https://cdn.example/vision_wasm_internal.js'
  const binaryPath = 'https://cdn.example/vision_wasm_internal.wasm'

  beforeEach(() => {
    forVisionTasks.mockReset()
    forVisionTasks.mockResolvedValue({
      wasmLoaderPath: loaderPath,
      wasmBinaryPath: binaryPath,
    })
  })

  it('fetches loader, wasm and model with the explicit base and consumes bodies', async () => {
    const responses: Response[] = []
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      const response = url.endsWith('.wasm')
        ? new Response(new Uint8Array([0, 97, 115, 109]), {
            status: 200,
            headers: { 'content-type': 'application/wasm' },
          })
        : new Response('asset-body', {
            status: 200,
            headers: {
              'content-type': url.endsWith('.js')
                ? 'application/javascript'
                : 'application/octet-stream',
            },
          })
      responses.push(response)
      return response
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await preloadBackgroundAssets({
      mediapipeAssetBase: '',
    })

    expect(forVisionTasks).toHaveBeenCalledWith(DEFAULT_WASM_BASE)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(responses.every((response) => response.bodyUsed)).toBe(true)
    expect(result).toEqual({
      wasmBase: DEFAULT_WASM_BASE,
      modelUrl: DEFAULT_MODEL_URL,
      partialFailure: false,
      essentialFailure: false,
    })

    vi.unstubAllGlobals()
  })

  it('marks model-only failure as partial but not essential', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === DEFAULT_MODEL_URL) {
        return new Response('missing', { status: 404 })
      }
      return url.endsWith('.wasm')
        ? new Response(new Uint8Array([0, 97, 115, 109]), {
            status: 200,
            headers: { 'content-type': 'application/wasm' },
          })
        : new Response('asset', {
            status: 200,
            headers: { 'content-type': 'application/javascript' },
          })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await preloadBackgroundAssets({ mediapipeAssetBase: '' })
    expect(result.partialFailure).toBe(true)
    expect(result.essentialFailure).toBe(false)

    vi.unstubAllGlobals()
  })

  it('marks loader + wasm failure as essential', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.endsWith('.js') || url.endsWith('.wasm')) {
        return new Response('missing', { status: 404 })
      }
      return new Response('model', {
        status: 200,
        headers: { 'content-type': 'application/octet-stream' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await preloadBackgroundAssets({ mediapipeAssetBase: '' })
    expect(result.partialFailure).toBe(true)
    expect(result.essentialFailure).toBe(true)

    vi.unstubAllGlobals()
  })

  it('rejects a non-wasm content type', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url.endsWith('.wasm')) {
        return new Response('<html></html>', {
          status: 200,
          headers: { 'content-type': 'text/html' },
        })
      }
      return new Response('asset', {
        status: 200,
        headers: {
          'content-type': url.endsWith('.js')
            ? 'application/javascript'
            : 'application/octet-stream',
        },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await preloadBackgroundAssets({ mediapipeAssetBase: '' })
    expect(result.partialFailure).toBe(true)
    expect(result.essentialFailure).toBe(false)

    vi.unstubAllGlobals()
  })
})

describe('getBackgroundCapabilities', () => {
  it('does not throw when navigator is unavailable', () => {
    expect(getBackgroundCapabilities(true)).toMatchObject({
      supported: true,
      visible: false,
      saveData: false,
    })
  })
})
