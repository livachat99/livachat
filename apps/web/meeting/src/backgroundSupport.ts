export type WebGLExtensionProbe = {
  getExtension(name: string): unknown
}

// MediaPipe's GPU postprocessing selects R32F only when
// EXT_color_buffer_float, OES_texture_float_linear and EXT_float_blend are
// all available. Otherwise it falls back to R16F, which requires
// EXT_color_buffer_half_float; we additionally require half-float linear
// filtering so the mask can be sampled correctly. Check both paths before
// creating a processor, so unsupported devices fail early instead of aborting
// inside MediaPipe.
export function supportsFloatColorBuffer(
  gl: WebGLExtensionProbe,
): boolean {
  const floatColor = !!gl.getExtension('EXT_color_buffer_float')
  const halfColor = !!gl.getExtension('EXT_color_buffer_half_float')
  const floatLinear = !!gl.getExtension('OES_texture_float_linear')
  const halfLinear = !!gl.getExtension('OES_texture_half_float_linear')
  const floatBlend = !!gl.getExtension('EXT_float_blend')
  return (
    (floatColor && floatLinear && floatBlend) ||
    (halfColor && halfLinear)
  )
}
