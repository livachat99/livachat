import { describe, expect, it } from 'vitest'
import { loadConfig, validateConfig } from './config'

const clientConfig = {
  appTitle: 'Test Meeting',
  livekitUrl: 'wss://livekit.example.test:7880',
  tokenMode: 'client',
  apiKey: 'key',
  apiSecret: 'secret',
  tokenTtlSeconds: 600,
  videoCodec: 'vp8',
  showPreJoin: true,
  enableSettingsMenu: true,
  enableBackgroundEffects: true,
  mediapipeAssetBase: '',
  showDebugPanel: false,
}

describe('validateConfig', () => {
  it('accepts a valid client config and applies defaults', () => {
    const config = validateConfig(
      {
        livekitUrl: clientConfig.livekitUrl,
        apiKey: 'key',
        apiSecret: 'secret',
      },
      { isHttps: false, isDev: false },
    )

    expect(config).toMatchObject({
      appTitle: 'LivaChat Meeting',
      tokenMode: 'client',
      tokenTtlSeconds: 600,
      videoCodec: 'vp8',
      showPreJoin: true,
      enableSettingsMenu: true,
      enableBackgroundEffects: true,
      mediapipeAssetBase: '',
      showDebugPanel: false,
    })
  })

  it('rejects a missing or invalid LiveKit URL', () => {
    expect(() =>
      validateConfig({ apiKey: 'key', apiSecret: 'secret' }, {
        isHttps: false,
      }),
    ).toThrow()

    expect(() =>
      validateConfig(
        { livekitUrl: 'https://example.test', apiKey: 'key', apiSecret: 'secret' },
        { isHttps: false },
      ),
    ).toThrow()
  })

  it('rejects ws:// when the page is HTTPS', () => {
    expect(() =>
      validateConfig(
        { livekitUrl: 'ws://localhost:7880', apiKey: 'key', apiSecret: 'secret' },
        { isHttps: true },
      ),
    ).toThrow()
  })

  it('requires key pair for client mode and rejects server secrets', () => {
    expect(() =>
      validateConfig(
        { livekitUrl: clientConfig.livekitUrl, tokenMode: 'client' },
        { isHttps: false },
      ),
    ).toThrow()

    expect(() =>
      validateConfig(
        {
          livekitUrl: clientConfig.livekitUrl,
          tokenMode: 'server',
          tokenEndpoint: '/api/meetings/join',
          apiSecret: 'secret',
        },
        { isHttps: false },
      ),
    ).toThrow()
  })

  it('accepts server mode without a client secret', () => {
    const config = validateConfig(
      {
        livekitUrl: clientConfig.livekitUrl,
        tokenMode: 'server',
        tokenEndpoint: '/api/meetings/join',
      },
      { isHttps: false },
    )
    expect(config.tokenMode).toBe('server')
  })

  it('validates TTL, codec and mediapipeAssetBase', () => {
    expect(() =>
      validateConfig(
        {
          ...clientConfig,
          tokenTtlSeconds: 60,
        },
        { isHttps: false },
      ),
    ).toThrow()

    expect(() =>
      validateConfig(
        {
          ...clientConfig,
          videoCodec: 'h265',
        },
        { isHttps: false },
      ),
    ).toThrow()

    const config = validateConfig(
      {
        ...clientConfig,
        mediapipeAssetBase: '/meeting/vendor/mediapipe',
      },
      { isHttps: false },
    )
    expect(config.mediapipeAssetBase).toBe(
      '/meeting/vendor/mediapipe/',
    )
  })
})

describe('loadConfig', () => {
  it('loads and validates JSON config', async () => {
    const fetchImpl = (async () =>
      new Response(JSON.stringify(clientConfig), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })) as unknown as typeof fetch

    const config = await loadConfig({
      fetchImpl,
      isHttps: false,
      isDev: false,
    })
    expect(config.tokenMode).toBe('client')
    expect(config.appTitle).toBe('Test Meeting')
  })

  it('fails on HTTP errors', async () => {
    const fetchImpl = (async () =>
      new Response('missing', { status: 404 })) as unknown as typeof fetch

    await expect(
      loadConfig({ fetchImpl, isHttps: false, isDev: false }),
    ).rejects.toThrow(/HTTP 404/)
  })

  it('rejects non-JSON responses (SPA fallback)', async () => {
    const fetchImpl = (async () =>
      new Response('<!doctype html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      })) as unknown as typeof fetch

    await expect(
      loadConfig({ fetchImpl, isHttps: false, isDev: false }),
    ).rejects.toThrow(/did not return JSON/)
  })
})
