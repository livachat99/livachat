#include "COpus.h"

#include <opus/opus.h>

LcOpusEncoderRef lc_opus_encoder_create(int sample_rate, int channels, int *error) {
    int err = OPUS_OK;
    OpusEncoder *encoder = opus_encoder_create(sample_rate, channels,
                                               OPUS_APPLICATION_VOIP, &err);
    if (err != OPUS_OK || encoder == NULL) {
        if (error != NULL) {
            *error = err;
        }
        return NULL;
    }

    opus_encoder_ctl(encoder, OPUS_SET_BITRATE(32000));
    opus_encoder_ctl(encoder, OPUS_SET_VBR(0));
    opus_encoder_ctl(encoder, OPUS_SET_COMPLEXITY(5));
    opus_encoder_ctl(encoder, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));

    if (error != NULL) {
        *error = OPUS_OK;
    }
    return (LcOpusEncoderRef)encoder;
}

int lc_opus_encode(LcOpusEncoderRef encoder_ref,
                   const int16_t *pcm,
                   int frame_samples,
                   unsigned char *out,
                   int max_bytes) {
    OpusEncoder *encoder = (OpusEncoder *)encoder_ref;
    if (encoder == NULL || pcm == NULL || out == NULL) {
        return OPUS_BAD_ARG;
    }
    return opus_encode(encoder, pcm, frame_samples, out, max_bytes);
}

void lc_opus_encoder_destroy(LcOpusEncoderRef encoder_ref) {
    OpusEncoder *encoder = (OpusEncoder *)encoder_ref;
    if (encoder != NULL) {
        opus_encoder_destroy(encoder);
    }
}

LcOpusDecoderRef lc_opus_decoder_create(int sample_rate, int channels, int *error) {
    int err = OPUS_OK;
    OpusDecoder *decoder = opus_decoder_create(sample_rate, channels, &err);
    if (err != OPUS_OK || decoder == NULL) {
        if (error != NULL) {
            *error = err;
        }
        return NULL;
    }
    if (error != NULL) {
        *error = OPUS_OK;
    }
    return (LcOpusDecoderRef)decoder;
}

int lc_opus_decode(LcOpusDecoderRef decoder_ref,
                   const unsigned char *data,
                   int len,
                   int16_t *pcm,
                   int max_samples) {
    OpusDecoder *decoder = (OpusDecoder *)decoder_ref;
    if (decoder == NULL || data == NULL || pcm == NULL) {
        return OPUS_BAD_ARG;
    }
    return opus_decode(decoder, data, len, pcm, max_samples, 0);
}

void lc_opus_decoder_destroy(LcOpusDecoderRef decoder_ref) {
    OpusDecoder *decoder = (OpusDecoder *)decoder_ref;
    if (decoder != NULL) {
        opus_decoder_destroy(decoder);
    }
}
