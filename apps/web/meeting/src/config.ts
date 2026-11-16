import { messages } from './messages'
import { SUPPORTED_LOCALES } from './i18n/types'
import type { Locale } from './i18n/types'

export type HomepageSlogan = Partial<Record<Locale, string>>

export type MeetingVideoCodec = 'vp8' | 'h264' | 'vp9' | 'av1'
export type BackgroundEffectsPreload = 'auto' | 'off'

export type BackgroundImageConfig = {
  label: string
  image: string
  thumbnail?: string
}

export type MeetingConfigCommon = {
  appTitle: string
  homepageSlogan: HomepageSlogan
  homepageLink: string
  tokenTtlSeconds: number
  videoCodec: MeetingVideoCodec
  showPreJoin: boolean
  enableSettingsMenu: boolean
  enableBackgroundEffects: boolean
  backgroundEffectsPreload: BackgroundEffectsPreload
  backgroundImages: BackgroundImageConfig[]
  mediapipeAssetBase: string
  showDebugPanel: boolean
}

export type ClientTokenConfig = MeetingConfigCommon & {
  tokenMode: 'client'
  livekitUrl: string
  apiKey: string
  apiSecret: string
}

export type ServerTokenConfig = MeetingConfigCommon & {
  tokenMode: 'server'
  tokenEndpoint: string
  livekitUrl?: string
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

function readHomepageSlogan(
  raw: Record<string, unknown>,
): HomepageSlogan {
  const value = raw.homepageSlogan
  if (value === undefined || value === null) return {}
  if (typeof value === 'string') {
    const slogan = value.trim()
    return slogan ? { en: slogan } : {}
  }
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(
      'config.json: "homepageSlogan" must be a string or a locale-to-string object.',
    )
  }
  const result: HomepageSlogan = {}
  for (const [key, entry] of Object.entries(value)) {
    if (!SUPPORTED_LOCALES.includes(key as Locale)) {
      throw new Error(
        `config.json: "homepageSlogan" has unsupported locale "${key}".`,
      )
    }
    if (typeof entry !== 'string') {
      throw new Error(
        `config.json: "homepageSlogan.${key}" must be a string.`,
      )
    }
    const slogan = entry.trim()
    if (slogan) result[key as Locale] = slogan
  }
  return result
}

function readOptionalHttpUrl(
  raw: Record<string, unknown>,
  key: string,
): string {
  const value = readOptionalString(raw, key)
  if (!value) return ''
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error(`config.json: "${key}" must be an http(s) URL.`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`config.json: "${key}" must be an http(s) URL.`)
  }
  return value
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

function readBackgroundEffectsPreload(
  raw: Record<string, unknown>,
): BackgroundEffectsPreload {
  const value = raw.backgroundEffectsPreload
  if (value === undefined) return 'auto'
  if (value !== 'auto' && value !== 'off') {
    throw new Error(
      'config.json: "backgroundEffectsPreload" must be "auto" or "off".',
    )
  }
  return value
}

function validateBackgroundImageUrl(value: string, label: string): string {
  if (
    /^[a-z][a-z0-9+.-]*:/i.test(value) &&
    !/^https?:\/\//i.test(value) &&
    !/^data:image\//i.test(value)
  ) {
    throw new Error(
      `config.json: "${label}" must be an http(s):// URL, data:image/... URL, or relative path.`,
    )
  }
  return value
}

function readBackgroundImages(
  raw: Record<string, unknown>,
): BackgroundImageConfig[] {
  const value = raw.backgroundImages
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) {
    throw new Error('config.json: "backgroundImages" must be an array.')
  }

  const seen = new Set<string>()
  return value.map((entry, index) => {
    const prefix = `backgroundImages[${index}]`
    if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) {
      throw new Error(`config.json: "${prefix}" must be an object.`)
    }
    const record = entry as Record<string, unknown>
    const label = readRequiredString(record, 'label', `${prefix}.label`)
    const image = validateBackgroundImageUrl(
      readRequiredString(record, 'image', `${prefix}.image`),
      `${prefix}.image`,
    )

    const thumbnailValue = record.thumbnail
    if (
      thumbnailValue !== undefined &&
      thumbnailValue !== null &&
      typeof thumbnailValue !== 'string'
    ) {
      throw new Error(`config.json: "${prefix}.thumbnail" must be a string.`)
    }
    const thumbnail =
      typeof thumbnailValue === 'string' ? thumbnailValue.trim() : ''

    if (seen.has(image)) {
      throw new Error(
        `config.json: "${prefix}.image" duplicates an earlier background image.`,
      )
    }
    seen.add(image)

    return {
      label,
      image,
      ...(thumbnail
        ? {
            thumbnail: validateBackgroundImageUrl(
              thumbnail,
              `${prefix}.thumbnail`,
            ),
          }
        : {}),
    }
  })
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

function validateLivekitUrl(rawUrl: string): string {
  let url: URL
  try {
    url = new URL(rawUrl)
  } catch {
    throw new Error(messages.invalidServerUrl)
  }
  // wss:// is mandatory even for localhost/dev: ws:// cannot carry WebRTC reliably.
  if (url.protocol !== 'wss:') {
    throw new Error(messages.invalidServerUrl)
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
    homepageSlogan: readHomepageSlogan(record),
    homepageLink: readOptionalHttpUrl(record, 'homepageLink'),
    tokenTtlSeconds: readInteger(
      record,
      'tokenTtlSeconds',
      DEFAULT_TTL_SECONDS,
      MIN_TTL_SECONDS,
      MAX_TTL_SECONDS,
    ),
    videoCodec: readVideoCodec(record),
    showPreJoin: readBoolean(record, 'showPreJoin', false),
    enableSettingsMenu: readBoolean(record, 'enableSettingsMenu', true),
    enableBackgroundEffects: readBoolean(record, 'enableBackgroundEffects', true),
    backgroundEffectsPreload: readBackgroundEffectsPreload(record),
    backgroundImages: readBackgroundImages(record),
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
    const livekitUrl = readOptionalString(record, 'livekitUrl')
    return {
      ...common,
      tokenMode: 'server',
      tokenEndpoint,
      ...(livekitUrl ? { livekitUrl: validateLivekitUrl(livekitUrl) } : {}),
    }
  }

  if (tokenMode !== 'client') {
    throw new Error('config.json: "tokenMode" must be "client" or "server".')
  }

  const livekitUrl = validateLivekitUrl(
    readRequiredString(record, 'livekitUrl', 'livekitUrl'),
  )
  const apiKey = readRequiredString(record, 'apiKey', 'apiKey')
  const apiSecret = readRequiredString(record, 'apiSecret', 'apiSecret')
  if (record.tokenEndpoint !== undefined) {
    throw new Error(
      'config.json: tokenEndpoint is only valid when tokenMode=server.',
    )
  }
  return { ...common, tokenMode: 'client', livekitUrl, apiKey, apiSecret }
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
