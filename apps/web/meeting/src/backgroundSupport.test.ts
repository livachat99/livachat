import { describe, expect, it } from 'vitest'
import {
  supportsFloatColorBuffer,
  type WebGLExtensionProbe,
} from './backgroundSupport'

function glWithExtensions(...extensions: string[]): WebGLExtensionProbe {
  return {
    getExtension: (name: string) =>
      extensions.includes(name) ? {} : null,
  }
}

const FLOAT_COLOR = 'EXT_color_buffer_float'
const HALF_COLOR = 'EXT_color_buffer_half_float'
const FLOAT_LINEAR = 'OES_texture_float_linear'
const HALF_LINEAR = 'OES_texture_half_float_linear'
const FLOAT_BLEND = 'EXT_float_blend'

describe('supportsFloatColorBuffer', () => {
  it('accepts the complete float32 path', () => {
    expect(
      supportsFloatColorBuffer(
        glWithExtensions(FLOAT_COLOR, FLOAT_LINEAR, FLOAT_BLEND),
      ),
    ).toBe(true)
  })

  it('rejects float32 when EXT_float_blend is missing and no half path exists', () => {
    expect(
      supportsFloatColorBuffer(glWithExtensions(FLOAT_COLOR, FLOAT_LINEAR)),
    ).toBe(false)
  })

  it('rejects float32 when float linear filtering is missing', () => {
    expect(
      supportsFloatColorBuffer(
        glWithExtensions(FLOAT_COLOR, FLOAT_BLEND),
      ),
    ).toBe(false)
  })

  it('accepts the half-float fallback with half linear filtering', () => {
    expect(
      supportsFloatColorBuffer(glWithExtensions(HALF_COLOR, HALF_LINEAR)),
    ).toBe(true)
  })

  it('rejects half-float without half linear filtering', () => {
    expect(supportsFloatColorBuffer(glWithExtensions(HALF_COLOR))).toBe(
      false,
    )
  })

  it('accepts half fallback when float32 is unavailable', () => {
    expect(
      supportsFloatColorBuffer(
        glWithExtensions(
          FLOAT_COLOR,
          FLOAT_LINEAR,
          HALF_COLOR,
          HALF_LINEAR,
        ),
      ),
    ).toBe(true)
  })
})
