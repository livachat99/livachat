export type WebGLExtensionProbe = {
  getExtension(name: string): unknown
}

// Mirrors MediaPipe's capability check in SegmentationPostprocessorGl::GlInit
// (mediapipe/tasks/cc/vision/image_segmenter/calculators/
// segmentation_postprocessor_gl.cc):
//
//   can_use_f32 = EXT_color_buffer_float && OES_texture_float_linear &&
//                 EXT_float_blend
//   can_use_f16_backup = EXT_color_buffer_half_float
//   RET_CHECK(can_use_f32 || can_use_f16_backup)
//
// The F16 backup needs only the half-float color buffer. In WebGL2 half-float
// linear filtering is core, so OES_texture_half_float_linear (a WebGL1
// extension) is not exposed and must not be required. Requiring it rejected
// working iPad Safari devices (and some Android GPUs that expose a usable
// half-float color buffer).
export function supportsFloatColorBuffer(
  gl: WebGLExtensionProbe,
): boolean {
  const floatColor = !!gl.getExtension('EXT_color_buffer_float')
  const halfColor = !!gl.getExtension('EXT_color_buffer_half_float')
  const floatLinear = !!gl.getExtension('OES_texture_float_linear')
  const floatBlend = !!gl.getExtension('EXT_float_blend')
  const canUseFloat32 = floatColor && floatLinear && floatBlend
  return canUseFloat32 || halfColor
}
