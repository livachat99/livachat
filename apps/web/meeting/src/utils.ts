import { TokenEndpointError } from './credentials/httpTokenProvider'
import { messages } from './messages'

export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName.toLowerCase()
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    target.isContentEditable
  )
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return 'Unknown error'
}

function formatTime(value: unknown): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

export function formatMeetingError(error: unknown): string {
  if (!(error instanceof TokenEndpointError)) return errorMessage(error)
  switch (error.code) {
    case 'meeting_not_found':
      return messages.meetingNotFound
    case 'meeting_not_started': {
      const time = formatTime(error.details.opens_at_ms)
      return time
        ? `${messages.meetingNotStarted} (opens ${time})`
        : messages.meetingNotStarted
    }
    case 'meeting_expired': {
      const time = formatTime(error.details.expires_at_ms)
      return time
        ? `${messages.meetingExpired} (ended ${time})`
        : messages.meetingExpired
    }
    case 'rate_limited':
      return messages.meetingRateLimited
    case 'meeting_disabled':
      return messages.meetingDisabled
    case 'upstream_unavailable':
      return messages.meetingServiceUnavailable
    default:
      return error.message || messages.meetingServiceUnavailable
  }
}

export function cleanDeviceLabel(label: string): string {
  return label.replace(/^Default\s*-\s*/i, '').trim()
}

export function isIOSLike(): boolean {
  if (typeof navigator === 'undefined') return false
  if (/iPad|iPhone|iPod/.test(navigator.userAgent)) return true
  // iPadOS reports itself as MacIntel but has touch points.
  return navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1
}
