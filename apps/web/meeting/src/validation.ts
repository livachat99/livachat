const ROOM_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/
const ROOM_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

export function normalizeRoomName(input: string): string {
  return input.trim().toLowerCase()
}

export function isValidRoomName(input: string): boolean {
  return ROOM_PATTERN.test(input)
}

export function countCodePoints(input: string): number {
  return Array.from(input).length
}

export function isValidParticipantName(input: string): boolean {
  const name = input.trim()
  const length = countCodePoints(name)
  return length >= 1 && length <= 40
}

export function randomRoomId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  let value = ''
  for (const byte of bytes) {
    value += ROOM_ALPHABET[byte % ROOM_ALPHABET.length]
  }
  return `${value.slice(0, 4)}-${value.slice(4, 8)}`
}
