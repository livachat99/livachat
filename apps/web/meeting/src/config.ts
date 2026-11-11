import { messages } from './messages'

export type MeetingVideoCodec = 'vp8' | 'h264' | 'vp9' | 'av1'

export type MeetingConfigCommon = {
  appTitle: string
  livekitUrl: string
  tokenTtlSeconds: number
  videoCodec: MeetingVideoCodec
  showPreJoin: boolean
  enableSettingsMenu: boolean
  enableBackgroundEffects: boolean
  mediapipeAssetBase: string
  showDebugPanel: boolean
}

export type ClientTokenConfig = MeetingConfigCommon & {
  tokenMode: 'client'
  apiKey: string
  apiSecret: string
}

export type ServerTokenConfig = MeetingConfigCommon & {
  tokenMode: 'server'
  tokenEndpoint: string
}

export type MeetingConfig = ClientTokenConfig | ServerTokenConfig

export type ConfigValidationOptions = {
  isHttps: boolean
  isDev?: boolean
}

export type LoadConfigDeps = {
  fetchImpl?: typeof fetch
  isHttps?: boolean
  isDev?: boolean
}

const VIDEO_CODECS: readonly MeetingVideoCodec[] = ['vp8', 'h264', 'vp9', 'av1']
const DEFAULT_TITLE = messages.appTitle
const DEFAULT_TTL_SECONDS = 600
const MIN_TTL_SECONDS = 120
const MAX_TTL_SECONDS = 3600

function asRecord(raw: unknown): Record<string, unknown> {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('config.json must be a JSON object.')
  }
  return raw as Record<string, unknown>
}

function readRequiredString(
  raw: Record<string, unknown>,
  key: string,
  label = key,
): string {
  const value = raw[key]
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`config.json: "${label}" must be a non-empty string.`)
  }
  return value.trim()
}

function readOptionalString(raw: Record<string, unknown>, key: string): string {
  const value = raw[key]
  if (value === undefined || value === null) return ''
  if (typeof value !== 'string') {
    throw new Error(`config.json: "${key}" must be a string.`)
  }
  return value.trim()
}

function readOptionalNonEmptyString(
  raw: Record<string, unknown>,
  key: string,
): string {
  const value = raw[key]
  if (value === undefined || value === null) return ''
  if (typeof value !== 'string') {
    throw new Error(`config.json: "${key}" must be a string.`)
  }
  return value.trim()
}

function readBoolean(
  raw: Record<string, unknown>,
  key: string,
  fallback: boolean,
): boolean {
  const value = raw[key]
  if (value === undefined) return fallback
  if (typeof value !== 'boolean') {
    throw new Error(`config.json: "${key}" must be a boolean.`)
  }
  return value
}

function readInteger(
  raw: Record<string, unknown>,
  key: string,
  fallback: number,
  min: number,
  max: number,
): number {
  const value = raw[key]
  if (value === undefined) return fallback
  if (
    typeof value !== 'number' ||
    !Number.isInteger(value) ||
    value < min ||
    value > max
  ) {
    throw new Error(`config.json: "${key}" must be an integer in ${min}..${max}.`)
  }
  return value
}

function readVideoCodec(
  raw: Record<string, unknown>,
): MeetingVideoCodec {
  const value = raw.videoCodec
  if (value === undefined) return 'vp8'
  if (typeof value !== 'string' || !VIDEO_CODECS.includes(value as MeetingVideoCodec)) {
    throw new Error(
      `config.json: "videoCodec" must be one of ${VIDEO_CODECS.join(', ')}.`,
    )
  }
  return value as MeetingVideoCodec
}

function readMediapipeAssetBase(raw: Record<string, unknown>): string {
  const value = readOptionalString(raw, 'mediapipeAssetBase')
  if (!value) return ''
  if (!/^(?:\/|https?:\/\/)/.test(value)) {
    throw new Error(
      'config.json: "mediapipeAssetBase" must start with "/" or "http(s)://".',
    )
  }
  return value.endsWith('/') ? value : `${value}/`
}

function validateLivekitUrl(rawUrl: string, isHttps: boolean): string {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    throw new Error(messages.invalidServerUrl)
  }
  if (url.protocol !== 'ws:' && url.protocol !== 'wss:') {
    throw new Error(messages.invalidServerUrl)
  }
  if (isHttps && url.protocol === 'ws:') {
    throw new Error(messages.mixedContent)
  }
  return url.toString().replace(/\/$/, '')
}

export function validateConfig(
  raw: unknown,
  opts: ConfigValidationOptions,
): MeetingConfig {
  const record = asRecord(raw)
  const common: MeetingConfigCommon = {
    appTitle: readOptionalString(record, 'appTitle') || DEFAULT_TITLE,
    livekitUrl: validateLivekitUrl(
      readRequiredString(record, 'livekitUrl', 'livekitUrl'),
      opts.isHttps,
    ),
    tokenTtlSeconds: readInteger(
      record,
      'tokenTtlSeconds',
      DEFAULT_TTL_SECONDS,
      MIN_TTL_SECONDS,
      MAX_TTL_SECONDS,
    ),
    videoCodec: readVideoCodec(record),
    showPreJoin: readBoolean(record, 'showPreJoin', true),
    enableSettingsMenu: readBoolean(record, 'enableSettingsMenu', true),
    enableBackgroundEffects: readBoolean(record, 'enableBackgroundEffects', true),
    mediapipeAssetBase: readMediapipeAssetBase(record),
    showDebugPanel: readBoolean(record, 'showDebugPanel', opts.isDev ?? false),
  }

  const tokenMode = readOptionalString(record, 'tokenMode') || 'client'
  if (tokenMode === 'server') {
    const tokenEndpoint = readRequiredString(
      record,
      'tokenEndpoint',
      'tokenEndpoint',
    )
    const apiKey = readOptionalNonEmptyString(record, 'apiKey')
    const apiSecret = readOptionalNonEmptyString(record, 'apiSecret')
    if (apiKey || apiSecret) {
      throw new Error(
        'config.json: tokenMode=server must not contain apiKey/apiSecret.',
      )
    }
    return { ...common, tokenMode: 'server', tokenEndpoint }
  }

  if (tokenMode !== 'client') {
    throw new Error('config.json: "tokenMode" must be "client" or "server".')
  }

  const apiKey = readRequiredString(record, 'apiKey', 'apiKey')
  const apiSecret = readRequiredString(record, 'apiSecret', 'apiSecret')
  if (record.tokenEndpoint !== undefined) {
    throw new Error(
      'config.json: tokenEndpoint is only valid when tokenMode=server.',
    )
  }
  return { ...common, tokenMode: 'client', apiKey, apiSecret }
}

export async function loadConfig(
  deps: LoadConfigDeps = {},
): Promise<MeetingConfig> {
  const fetchImpl = deps.fetchImpl ?? fetch
  const url = `${import.meta.env.BASE_URL}config.json`
  const res = await fetchImpl(url, {
    cache: 'no-store',
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
  })
  if (!res.ok) {
    throw new Error(
      `Failed to load ${url} (HTTP ${res.status}). ${messages.configErrorHint}`,
    )
  }
  const contentType = res.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    throw new Error(
      `${url} did not return JSON (Content-Type: ${contentType}). ${messages.configErrorHint}`,
    )
  }
  let raw: unknown
  try {
    raw = await res.json()
  } catch {
    throw new Error(`${url} is not valid JSON. ${messages.configErrorHint}`)
  }
  const isHttps =
    deps.isHttps ??
    (typeof location !== 'undefined' && location.protocol === 'https:')
  return validateConfig(raw, {
    isHttps,
    isDev: deps.isDev ?? import.meta.env.DEV,
  })
}
