import { describe, expect, it } from 'vitest'
import { readStoredParticipantName, saveParticipantName } from './storage'

const KEY = 'livachat-meeting:display-name'

function memoryStorage(initial?: string) {
  const values = new Map<string, string>()
  if (initial !== undefined) values.set(KEY, initial)
  return {
    values,
    getItem(key: string) {
      return values.get(key) ?? null
    },
    setItem(key: string, value: string) {
      values.set(key, value)
    },
  }
}

describe('readStoredParticipantName', () => {
  it('returns a valid trimmed name', () => {
    const storage = memoryStorage('  Alice  ')
    expect(readStoredParticipantName(storage)).toBe('Alice')
  })

  it('returns null for missing, empty, invalid, or unreadable values', () => {
    expect(readStoredParticipantName(memoryStorage())).toBeNull()
    expect(readStoredParticipantName(memoryStorage('   '))).toBeNull()
    expect(readStoredParticipantName(memoryStorage('a'.repeat(41)))).toBeNull()
    expect(readStoredParticipantName(memoryStorage('Al\nice'))).toBeNull()
    expect(readStoredParticipantName(null)).toBeNull()
    expect(
      readStoredParticipantName({
        getItem() {
          throw new Error('denied')
        },
        setItem() {},
      }),
    ).toBeNull()
  })
})

describe('saveParticipantName', () => {
  it('stores the trimmed name', () => {
    const storage = memoryStorage()
    saveParticipantName('  Alice  ', storage)
    expect(storage.values.get(KEY)).toBe('Alice')
  })

  it('ignores invalid names and storage failures', () => {
    const storage = memoryStorage()
    saveParticipantName('   ', storage)
    saveParticipantName('a'.repeat(41), storage)
    saveParticipantName('Al\nice', storage)
    expect(storage.values.has(KEY)).toBe(false)

    expect(() =>
      saveParticipantName('Alice', {
        getItem() {
          return null
        },
        setItem() {
          throw new Error('quota')
        },
      }),
    ).not.toThrow()
    expect(() => saveParticipantName('Alice', null)).not.toThrow()
  })
})
