#ifndef LIVA_CHAT_CVAD_H_
#define LIVA_CHAT_CVAD_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *LcVadRef;

LcVadRef lc_vad_create(int mode);
void lc_vad_destroy(LcVadRef vad);
int lc_vad_set_mode(LcVadRef vad, int mode);
int lc_vad_process(LcVadRef vad,
                   int sample_rate_hz,
                   const int16_t *audio_frame,
                   size_t frame_length);
int lc_vad_valid_rate_and_frame_length(int sample_rate_hz, size_t frame_length);
const char *lc_vad_source_revision(void);

#ifdef __cplusplus
}
#endif

#endif  // LIVA_CHAT_CVAD_H_
