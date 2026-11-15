import { countCodePoints } from './validation'

const NAME_STORAGE_KEY = 'livachat-meeting:display-name'
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>

function defaultStorage(): StorageLike | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    // Accessing localStorage itself can throw in some privacy modes.
    return null
  }
}

function validName(value: string): string | null {
  const name = value.trim()
  if (!name || countCodePoints(name) > 40 || CONTROL_CHARS.test(name)) {
    return null
  }
  return name
}

export function readStoredParticipantName(
  storage: StorageLike | null = defaultStorage(),
): string | null {
  if (!storage) return null
  try {
    const value = storage.getItem(NAME_STORAGE_KEY)
    return value === null ? null : validName(value)
  } catch {
    return null
  }
}

export function saveParticipantName(
  name: string,
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return
  const value = validName(name)
  if (!value) return
  try {
    storage.setItem(NAME_STORAGE_KEY, value)
  } catch {
    // Storage may be unavailable in private mode; the app still works.
  }
}
