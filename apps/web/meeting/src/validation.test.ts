import { describe, expect, it } from 'vitest'
import {
  countCodePoints,
  isValidParticipantName,
  isValidRoomName,
  normalizeRoomName,
  randomRoomId,
} from './validation'

describe('meeting number validation', () => {
  it('trims and lowercases', () => {
    expect(normalizeRoomName('  Design-Review  ')).toBe('design-review')
  })

  it('accepts valid room names', () => {
    expect(isValidRoomName('123-456-789')).toBe(true)
    expect(isValidRoomName('team_standup')).toBe(true)
    expect(isValidRoomName('a')).toBe(true)
    expect(isValidRoomName('a'.repeat(64))).toBe(true)
  })

  it('rejects invalid room names', () => {
    expect(isValidRoomName('')).toBe(false)
    expect(isValidRoomName('-leading')).toBe(false)
    expect(isValidRoomName('has space')).toBe(false)
    expect(isValidRoomName('a'.repeat(65))).toBe(false)
    expect(isValidRoomName('中文')).toBe(false)
  })

  it('generates lowercase room ids', () => {
    const id = randomRoomId()
    expect(id).toMatch(/^[a-z0-9]{4}-[a-z0-9]{4}$/)
  })
})

describe('participant name validation', () => {
  it('counts Unicode code points', () => {
    expect(countCodePoints('Alice')).toBe(5)
    expect(countCodePoints('你好')).toBe(2)
    expect(countCodePoints('👩‍💻')).toBe(3)
  })

  it('accepts 1..40 characters', () => {
    expect(isValidParticipantName('A')).toBe(true)
    expect(isValidParticipantName('A'.repeat(40))).toBe(true)
    expect(isValidParticipantName('  Alice  ')).toBe(true)
  })

  it('rejects empty or too-long names', () => {
    expect(isValidParticipantName('')).toBe(false)
    expect(isValidParticipantName('   ')).toBe(false)
    expect(isValidParticipantName('A'.repeat(41))).toBe(false)
  })
})
