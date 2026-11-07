#ifndef WEBRTC_VAD_LITE_H_
#define WEBRTC_VAD_LITE_H_

#include <stddef.h>
#include <stdint.h>

#include "webrtc_vad.h"

#if defined(_WIN32) && defined(WEBRTC_VAD_LITE_BUILD_SHARED)
#if defined(WEBRTC_VAD_LITE_EXPORTS)
#define WVAD_API __declspec(dllexport)
#else
#define WVAD_API __declspec(dllimport)
#endif
#elif defined(__GNUC__) || defined(__clang__)
#define WVAD_API __attribute__((visibility("default")))
#else
#define WVAD_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef VadInst WvadInst;

WVAD_API const char* Wvad_SourceRevision(void);
WVAD_API WvadInst* Wvad_Create(int mode);
WVAD_API void Wvad_Free(WvadInst* handle);
WVAD_API int Wvad_SetMode(WvadInst* handle, int mode);
WVAD_API int Wvad_Process(WvadInst* handle,
                          int sample_rate_hz,
                          const int16_t* audio_frame,
                          size_t frame_length);
WVAD_API int Wvad_ValidRateAndFrameLength(int sample_rate_hz,
                                          size_t frame_length);

#ifdef __cplusplus
}
#endif

#endif  // WEBRTC_VAD_LITE_H_
