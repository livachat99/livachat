import { describe, expect, it } from 'vitest'
import { makeIdentity } from './identity'

describe('makeIdentity', () => {
  it('returns an opaque u_ prefixed id', () => {
    expect(makeIdentity()).toMatch(/^u_[a-z0-9]{16}$/)
  })

  it('does not repeat across many calls', () => {
    const ids = new Set(Array.from({ length: 200 }, () => makeIdentity()))
    expect(ids.size).toBe(200)
  })
})
