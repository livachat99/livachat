import { describe, expect, it } from 'vitest'
import {
  SUPPORTED_LOCALES,
  formattingLocale,
  getMessages,
} from '../i18n'
import { messages as en } from '../i18n/locales/en'

const placeholders = (value: string) =>
  [...value.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort()

describe('locale catalogs', () => {
  it('have exactly the same keys as English', () => {
    const expected = Object.keys(en).sort()
    for (const locale of SUPPORTED_LOCALES) {
      expect(Object.keys(getMessages(locale)).sort(), locale).toEqual(expected)
    }
  })

  it('have non-empty values and matching placeholders', () => {
    for (const locale of SUPPORTED_LOCALES) {
      const catalog = getMessages(locale)
      for (const key of Object.keys(en) as Array<keyof typeof en>) {
        expect(catalog[key].trim(), `${locale}.${key}`).not.toBe('')
        expect(placeholders(catalog[key]), `${locale}.${key}`).toEqual(
          placeholders(en[key]),
        )
      }
    }
  })

  it('does not include Arabic in the supported set', () => {
    expect(SUPPORTED_LOCALES).not.toContain('ar' as never)
  })
})

describe('formattingLocale', () => {
  it('maps Portuguese to pt-BR and otherwise keeps the canonical code', () => {
    expect(formattingLocale('pt')).toBe('pt-BR')
    expect(formattingLocale('en')).toBe('en')
    expect(formattingLocale('zh-Hant')).toBe('zh-Hant')
    expect(formattingLocale('hi')).toBe('hi')
  })
})
