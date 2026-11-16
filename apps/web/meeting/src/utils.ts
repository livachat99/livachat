import { TokenEndpointError } from './credentials/httpTokenProvider'
import type { Messages } from './i18n'
import { messages as enMessages } from './i18n/locales/en'

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

export function errorMessage(error: unknown, fallback = 'Unknown error'): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  return fallback
}

export function formatMeetingError(
  error: unknown,
  messages: Messages = enMessages,
): string {
  if (!(error instanceof TokenEndpointError)) return messages.joinFailed
  switch (error.code) {
    case 'meeting_not_found':
      return messages.meetingNotFound
    case 'meeting_not_started':
      return messages.meetingNotStarted
    case 'meeting_expired':
      return messages.meetingExpired
    case 'rate_limited':
      return messages.meetingRateLimited
    case 'meeting_disabled':
      return messages.meetingDisabled
    case 'upstream_unavailable':
      return messages.meetingServiceUnavailable
    default:
      return messages.tokenFailed
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
