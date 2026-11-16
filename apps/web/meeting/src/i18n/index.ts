import { messages as en } from './locales/en'
import { messages as de } from './locales/de'
import { messages as es } from './locales/es'
import { messages as fr } from './locales/fr'
import { messages as hi } from './locales/hi'
import { messages as id } from './locales/id'
import { messages as it } from './locales/it'
import { messages as ja } from './locales/ja'
import { messages as ko } from './locales/ko'
import { messages as pt } from './locales/pt'
import { messages as ru } from './locales/ru'
import { messages as th } from './locales/th'
import { messages as vi } from './locales/vi'
import { messages as zhHans } from './locales/zh-Hans'
import { messages as zhHant } from './locales/zh-Hant'
import type { Messages } from './locales/en'
import type { I18n, Locale } from './types'

const catalogs: Record<Locale, Messages> = {
  en,
  'zh-Hans': zhHans,
  'zh-Hant': zhHant,
  ja,
  ko,
  ru,
  de,
  fr,
  it,
  es,
  pt,
  hi,
  id,
  th,
  vi,
}

export function getMessages(locale: Locale): Messages {
  return catalogs[locale] ?? en
}

export function formattingLocale(locale: Locale): string {
  return locale === 'pt' ? 'pt-BR' : locale
}

export function createI18n(locale: Locale): I18n {
  return {
    locale,
    messages: getMessages(locale),
    formattingLocale: formattingLocale(locale),
  }
}

export function applyDocumentLocale(locale: Locale): void {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale
  document.documentElement.dir = 'ltr'
  document.documentElement.dataset.locale = locale
}

export {
  canonicalLocale,
  browserLanguages,
  localeFromQuery,
  resolveLocale,
} from './resolveLocale'
export { I18nProvider, useI18n, useMessages } from './I18nProvider'
export { SUPPORTED_LOCALES, type I18n, type Locale } from './types'
export type { Messages } from './locales/en'
