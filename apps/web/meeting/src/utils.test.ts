import { describe, expect, it } from 'vitest'
import { TokenEndpointError } from './credentials/httpTokenProvider'
import { messages } from './messages'
import { formatMeetingError } from './utils'

describe('formatMeetingError', () => {
  it('maps stable BFF error codes', () => {
    expect(
      formatMeetingError(new TokenEndpointError('meeting_not_found', 'x', 404)),
    ).toBe(messages.meetingNotFound)
    expect(
      formatMeetingError(new TokenEndpointError('meeting_expired', 'x', 410)),
    ).toBe(messages.meetingExpired)
    expect(
      formatMeetingError(new TokenEndpointError('rate_limited', 'x', 429)),
    ).toBe(messages.meetingRateLimited)
    expect(
      formatMeetingError(new TokenEndpointError('meeting_disabled', 'x', 503)),
    ).toBe(messages.meetingDisabled)
    expect(
      formatMeetingError(new TokenEndpointError('upstream_unavailable', 'x', 502)),
    ).toBe(messages.meetingServiceUnavailable)
  })

  it('omits opens_at/expires_at details from user-facing messages', () => {
    const started = formatMeetingError(
      new TokenEndpointError('meeting_not_started', 'x', 403, {
        opens_at_ms: Date.UTC(2030, 0, 1, 10, 0),
      }),
    )
    expect(started).toBe(messages.meetingNotStarted)
    expect(started).not.toContain('(')

    const expired = formatMeetingError(
      new TokenEndpointError('meeting_expired', 'x', 410, {
        expires_at_ms: Date.UTC(2030, 0, 1, 11, 0),
      }),
    )
    expect(expired).toBe(messages.meetingExpired)
    expect(expired).not.toContain('(')
  })

  it('falls back to the error message for unknown codes/non-API errors', () => {
    expect(
      formatMeetingError(new TokenEndpointError('unknown', 'boom', 500)),
    ).toBe('boom')
    expect(formatMeetingError(new Error('plain error'))).toBe('plain error')
  })
})
