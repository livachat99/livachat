import { describe, expect, it } from 'vitest'
import {
  canonicalLocale,
  localeFromQuery,
  resolveLocale,
  type Locale,
} from '../i18n'

describe('canonicalLocale', () => {
  it('maps the supported language variants', () => {
    expect(canonicalLocale('en-US')).toBe('en')
    expect(canonicalLocale('zh')).toBe('zh-Hans')
    expect(canonicalLocale('zh-CN')).toBe('zh-Hans')
    expect(canonicalLocale('zh-SG')).toBe('zh-Hans')
    expect(canonicalLocale('zh-Hans-HK')).toBe('zh-Hans')
    expect(canonicalLocale('zh-TW')).toBe('zh-Hant')
    expect(canonicalLocale('zh-HK')).toBe('zh-Hant')
    expect(canonicalLocale('zh-MO')).toBe('zh-Hant')
    expect(canonicalLocale('zh-Hant-CN')).toBe('zh-Hant')
    expect(canonicalLocale('pt-BR')).toBe('pt')
    expect(canonicalLocale('pt-PT')).toBe('pt')
    expect(canonicalLocale('in-ID')).toBe('id')
    expect(canonicalLocale('id-ID')).toBe('id')
    expect(canonicalLocale('de_AT')).toBe('de')
    expect(canonicalLocale('es-419')).toBe('es')
  })

  it('rejects unsupported languages', () => {
    expect(canonicalLocale('ar-EG')).toBeNull()
    expect(canonicalLocale('tr-TR')).toBeNull()
    expect(canonicalLocale('xx')).toBeNull()
    expect(canonicalLocale('')).toBeNull()
  })
})

describe('resolveLocale', () => {
  it('uses the first supported browser language', () => {
    expect(resolveLocale(['en-US'])).toBe('en')
    expect(resolveLocale(['fr-CA', 'en-US'])).toBe('fr')
    expect(resolveLocale(['tr-TR', 'fr-FR'])).toBe('fr')
    expect(resolveLocale(['ar-EG', 'fr-FR'])).toBe('fr')
  })

  it('falls back to English when no language is supported', () => {
    expect(resolveLocale(['ar-EG'])).toBe('en')
    expect(resolveLocale(['tr-TR'])).toBe('en')
    expect(resolveLocale([])).toBe('en')
  })

  it('returns a supported locale type', () => {
    const locales: Locale[] = ['en', 'zh-Hant', 'hi', 'th', 'vi']
    for (const locale of locales) {
      expect(resolveLocale([locale])).toBe(locale)
    }
  })
})

describe('localeFromQuery', () => {
  it('reads the explicit in-app locale bridge', () => {
    expect(localeFromQuery('?lang=zh-CN')).toBe('zh-CN')
    expect(localeFromQuery('?foo=1&lang=ja-JP')).toBe('ja-JP')
    expect(localeFromQuery('?lang=%20vi-VN%20')).toBe('vi-VN')
  })

  it('ignores missing or empty values', () => {
    expect(localeFromQuery('')).toBeNull()
    expect(localeFromQuery('?foo=1')).toBeNull()
    expect(localeFromQuery('?lang=')).toBeNull()
  })
})
