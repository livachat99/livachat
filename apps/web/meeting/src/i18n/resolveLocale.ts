import { SUPPORTED_LOCALES, type Locale } from './types'

export function canonicalLocale(tag: string): Locale | null {
  const parts = tag
    .replace(/_/g, '-')
    .toLowerCase()
    .split('-')
    .filter(Boolean)
  if (parts.length === 0) return null

  const [language, ...subtags] = parts
  if (language === 'zh') {
    if (subtags.includes('hant')) return 'zh-Hant'
    if (subtags.includes('hans')) return 'zh-Hans'
    if (subtags.some((part) => ['tw', 'hk', 'mo'].includes(part))) {
      return 'zh-Hant'
    }
    return 'zh-Hans'
  }

  const base = language === 'in' ? 'id' : language
  return SUPPORTED_LOCALES.includes(base as Locale) ? (base as Locale) : null
}

export function resolveLocale(tags: readonly string[]): Locale {
  for (const tag of tags) {
    const code = canonicalLocale(tag)
    if (code) return code
  }
  return 'en'
}

/** Explicit in-app WebView language bridge, e.g. `?lang=zh-Hans`. */
export function localeFromQuery(search: string): string | null {
  const value = new URLSearchParams(search).get('lang')?.trim()
  return value ? value : null
}

export function browserLanguages(): string[] {
  if (typeof navigator === 'undefined') return []
  const languages = navigator.languages
  if (Array.isArray(languages) && languages.length > 0) return [...languages]
  return navigator.language ? [navigator.language] : []
}
