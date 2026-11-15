const MEETING_NUMBER_PATH = /^[0-9]{8}$/

/**
 * Extract an 8-digit meeting number from the SPA pathname.
 *
 * `/meeting/34479999` and `/meeting/34479999/` → `"34479999"`.
 * Missing number, invalid format, extra path segments, or a different base
 * path → `null` (the caller then leaves the input empty).
 *
 * Only ASCII digits are accepted; the path is not NFKC-normalized.
 */
export function meetingNumberFromUrl(
  pathname: string,
  baseUrl: string = import.meta.env.BASE_URL,
): string | null {
  const base = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`
  if (!pathname.startsWith(base)) return null
  const rest = pathname.slice(base.length).replace(/\/+$/, '')
  if (!rest || rest.includes('/')) return null
  return MEETING_NUMBER_PATH.test(rest) ? rest : null
}
