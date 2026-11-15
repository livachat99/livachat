import { describe, expect, it } from 'vitest'
import { meetingNumberFromUrl } from './meetingUrl'

const BASE = '/meeting/'

describe('meetingNumberFromUrl', () => {
  it('reads an 8-digit number from the meeting path', () => {
    expect(meetingNumberFromUrl('/meeting/34479999', BASE)).toBe('34479999')
    expect(meetingNumberFromUrl('/meeting/34479999/', BASE)).toBe('34479999')
  })

  it('ignores missing or invalid numbers', () => {
    for (const pathname of [
      '/meeting/',
      '/meeting',
      '/meeting/3447999',
      '/meeting/344799999',
      '/meeting/abcdefgh',
      '/meeting/3447-9999',
      '/meeting/１２３４５６７８',
      '/meeting/34479999/extra',
      '/meeting//34479999',
      '/other/34479999',
    ]) {
      expect(meetingNumberFromUrl(pathname, BASE)).toBeNull()
    }
  })

  it('normalizes a base URL without a trailing slash', () => {
    expect(meetingNumberFromUrl('/meeting/34479999', '/meeting')).toBe(
      '34479999',
    )
  })
})
