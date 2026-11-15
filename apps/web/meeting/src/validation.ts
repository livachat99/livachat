const ROOM_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/
const MEETING_NUMBER_RE = /^\d{8}$/

export function normalizeRoomName(input: string): string {
  return input.trim().toLowerCase()
}

export function isValidRoomName(input: string): boolean {
  return ROOM_PATTERN.test(input)
}

export function normalizeMeetingNumber(input: string): string {
  // NFKC matches the server-side normalization: full-width digits/hyphens and
  // other compatibility characters collapse to their ASCII form.
  return input.normalize('NFKC').replace(/\s|-/g, '')
}

export function isValidMeetingNumber(input: string): boolean {
  return MEETING_NUMBER_RE.test(input)
}

export function countCodePoints(input: string): number {
  return Array.from(input).length
}

export function isValidParticipantName(input: string): boolean {
  const name = input.trim()
  const length = countCodePoints(name)
  return length >= 1 && length <= 40
}
