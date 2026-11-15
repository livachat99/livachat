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
      showPreJoin: false,
      enableSettingsMenu: true,
      enableBackgroundEffects: true,
      backgroundEffectsPreload: 'auto',
      backgroundImages: [],
      mediapipeAssetBase: '',
      showDebugPanel: false,
    })
  })

  it('accepts an explicit showPreJoin override and rejects non-booleans', () => {
    const base = {
      livekitUrl: clientConfig.livekitUrl,
      apiKey: 'key',
      apiSecret: 'secret',
    }
    expect(
      validateConfig({ ...base, showPreJoin: true }, { isHttps: false })
        .showPreJoin,
    ).toBe(true)
    expect(() =>
      validateConfig(
        { ...base, showPreJoin: 'yes' },
        { isHttps: false },
      ),
    ).toThrow()
  })

  it('validates backgroundEffectsPreload', () => {
    const base = {
      livekitUrl: clientConfig.livekitUrl,
      apiKey: 'key',
      apiSecret: 'secret',
    }
    expect(
      validateConfig(
        { ...base, backgroundEffectsPreload: 'off' },
        { isHttps: false },
      ).backgroundEffectsPreload,
    ).toBe('off')
    expect(
      validateConfig(
        { ...base, backgroundEffectsPreload: 'auto' },
        { isHttps: false },
      ).backgroundEffectsPreload,
    ).toBe('auto')
    expect(() =>
      validateConfig(
        { ...base, backgroundEffectsPreload: 'yes' },
        { isHttps: false },
      ),
    ).toThrow()
  })

  it('validates config-driven background images', () => {
    const base = {
      livekitUrl: clientConfig.livekitUrl,
      apiKey: 'key',
      apiSecret: 'secret',
    }
    const config = validateConfig(
      {
        ...base,
        backgroundImages: [
          {
            label: ' Office ',
            image: ' backgrounds/office.jpg ',
            thumbnail: ' backgrounds/office-thumb.jpg ',
          },
          {
            label: 'Library',
            image: 'https://cdn.example.test/meeting/library.jpg',
          },
        ],
      },
      { isHttps: false },
    )
    expect(config.backgroundImages).toEqual([
      {
        label: 'Office',
        image: 'backgrounds/office.jpg',
        thumbnail: 'backgrounds/office-thumb.jpg',
      },
      {
        label: 'Library',
        image: 'https://cdn.example.test/meeting/library.jpg',
      },
    ])

    expect(() =>
      validateConfig({ ...base, backgroundImages: 'office.jpg' }, {
        isHttps: false,
      }),
    ).toThrow()
    expect(() =>
      validateConfig(
        { ...base, backgroundImages: [{ label: 'Office' }] },
        { isHttps: false },
      ),
    ).toThrow()
    expect(() =>
      validateConfig(
        {
          ...base,
          backgroundImages: [
            { label: 'Bad', image: 'javascript:alert(1)' },
          ],
        },
        { isHttps: false },
      ),
    ).toThrow()
    expect(() =>
      validateConfig(
        {
          ...base,
          backgroundImages: [
            { label: 'One', image: 'backgrounds/one.jpg' },
            { label: 'Two', image: 'backgrounds/one.jpg' },
          ],
        },
        { isHttps: false },
      ),
    ).toThrow()
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

  it('rejects ws:// even for localhost/dev', () => {
    for (const isHttps of [true, false]) {
      expect(() =>
        validateConfig(
          { livekitUrl: 'ws://localhost:7880', apiKey: 'key', apiSecret: 'secret' },
          { isHttps },
        ),
      ).toThrow()
    }
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

  it('accepts server mode without a client secret or livekitUrl', () => {
    const config = validateConfig(
      {
        tokenMode: 'server',
        tokenEndpoint: '/api/mylife/join_meeting',
      },
      { isHttps: false },
    )
    expect(config.tokenMode).toBe('server')
    expect('livekitUrl' in config).toBe(false)
  })

  it('accepts an optional wss livekitUrl in server mode and rejects ws://', () => {
    const config = validateConfig(
      {
        tokenMode: 'server',
        tokenEndpoint: '/api/mylife/join_meeting',
        livekitUrl: 'wss://livekit.example.test:7880',
      },
      { isHttps: false },
    )
    expect(config.tokenMode).toBe('server')
    expect(config.livekitUrl).toBe('wss://livekit.example.test:7880')

    expect(() =>
      validateConfig(
        {
          tokenMode: 'server',
          tokenEndpoint: '/api/mylife/join_meeting',
          livekitUrl: 'ws://localhost:7880',
        },
        { isHttps: false },
      ),
    ).toThrow()
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

  it('loads a server-mode config without livekitUrl', async () => {
    const serverConfig = {
      tokenMode: 'server',
      tokenEndpoint: '/api/mylife/join_meeting',
    }
    const fetchImpl = (async () =>
      new Response(JSON.stringify(serverConfig), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })) as unknown as typeof fetch

    const config = await loadConfig({ fetchImpl, isHttps: false, isDev: false })
    if (config.tokenMode !== 'server') throw new Error('expected server mode')
    expect(config.tokenEndpoint).toBe('/api/mylife/join_meeting')
  })
})
