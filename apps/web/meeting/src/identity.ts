const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'

function randomId(length: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  let result = ''
  for (const byte of bytes) {
    result += ALPHABET[byte % ALPHABET.length]
  }
  return result
}

/**
 * Opaque participant identity. It intentionally contains no display name or
 * other PII; the display name is carried separately in the token's `name` claim.
 */
export function makeIdentity(): string {
  return `u_${randomId(16)}`
}
