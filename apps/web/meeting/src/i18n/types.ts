import type { Messages } from './locales/en'

export const SUPPORTED_LOCALES = [
  'en',
  'zh-Hans',
  'zh-Hant',
  'ja',
  'ko',
  'ru',
  'de',
  'fr',
  'it',
  'es',
  'pt',
  'hi',
  'id',
  'th',
  'vi',
] as const

export type Locale = (typeof SUPPORTED_LOCALES)[number]

export type I18n = {
  locale: Locale
  messages: Messages
  formattingLocale: string
}
