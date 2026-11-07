#include "CVad.h"

#include <webrtc_vad_lite.h>

LcVadRef lc_vad_create(int mode) {
    return (LcVadRef)Wvad_Create(mode);
}

void lc_vad_destroy(LcVadRef vad) {
    if (vad != NULL) {
        Wvad_Free((WvadInst *)vad);
    }
}

int lc_vad_set_mode(LcVadRef vad, int mode) {
    if (vad == NULL) {
        return -1;
    }
    return Wvad_SetMode((WvadInst *)vad, mode);
}

int lc_vad_process(LcVadRef vad,
                   int sample_rate_hz,
                   const int16_t *audio_frame,
                   size_t frame_length) {
    if (vad == NULL || audio_frame == NULL) {
        return -1;
    }
    return Wvad_Process((WvadInst *)vad, sample_rate_hz, audio_frame, frame_length);
}

int lc_vad_valid_rate_and_frame_length(int sample_rate_hz, size_t frame_length) {
    return Wvad_ValidRateAndFrameLength(sample_rate_hz, frame_length);
}

const char *lc_vad_source_revision(void) {
    return Wvad_SourceRevision();
}
