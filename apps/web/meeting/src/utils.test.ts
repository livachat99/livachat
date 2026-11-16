import { describe, expect, it } from 'vitest'
import { TokenEndpointError } from './credentials/httpTokenProvider'
import { messages as en } from './i18n/locales/en'
import { messages as zhHans } from './i18n/locales/zh-Hans'
import { formatMeetingError } from './utils'

describe('formatMeetingError', () => {
  it('maps stable BFF error codes', () => {
    expect(
      formatMeetingError(new TokenEndpointError('meeting_not_found', 'x', 404)),
    ).toBe(en.meetingNotFound)
    expect(
      formatMeetingError(new TokenEndpointError('meeting_expired', 'x', 410)),
    ).toBe(en.meetingExpired)
    expect(
      formatMeetingError(new TokenEndpointError('rate_limited', 'x', 429)),
    ).toBe(en.meetingRateLimited)
    expect(
      formatMeetingError(new TokenEndpointError('meeting_disabled', 'x', 503)),
    ).toBe(en.meetingDisabled)
    expect(
      formatMeetingError(new TokenEndpointError('upstream_unavailable', 'x', 502)),
    ).toBe(en.meetingServiceUnavailable)
  })

  it('uses the selected locale catalog', () => {
    expect(
      formatMeetingError(
        new TokenEndpointError('meeting_not_found', 'x', 404),
        zhHans,
      ),
    ).toBe(zhHans.meetingNotFound)
  })

  it('omits opens_at/expires_at details from user-facing messages', () => {
    const started = formatMeetingError(
      new TokenEndpointError('meeting_not_started', 'x', 403, {
        opens_at_ms: Date.UTC(2030, 0, 1, 10, 0),
      }),
    )
    expect(started).toBe(en.meetingNotStarted)
    expect(started).not.toContain('(')

    const expired = formatMeetingError(
      new TokenEndpointError('meeting_expired', 'x', 410, {
        expires_at_ms: Date.UTC(2030, 0, 1, 11, 0),
      }),
    )
    expect(expired).toBe(en.meetingExpired)
    expect(expired).not.toContain('(')
  })

  it('uses localized generic errors for unknown codes/non-API errors', () => {
    expect(
      formatMeetingError(new TokenEndpointError('unknown', 'boom', 500)),
    ).toBe(en.tokenFailed)
    expect(formatMeetingError(new Error('plain error'))).toBe(en.joinFailed)
  })
})
