#ifndef LIVA_CHAT_COPUS_H_
#define LIVA_CHAT_COPUS_H_

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef void *LcOpusEncoderRef;
typedef void *LcOpusDecoderRef;

LcOpusEncoderRef lc_opus_encoder_create(int sample_rate, int channels, int *error);
int lc_opus_encode(LcOpusEncoderRef encoder,
                   const int16_t *pcm,
                   int frame_samples,
                   unsigned char *out,
                   int max_bytes);
void lc_opus_encoder_destroy(LcOpusEncoderRef encoder);

LcOpusDecoderRef lc_opus_decoder_create(int sample_rate, int channels, int *error);
int lc_opus_decode(LcOpusDecoderRef decoder,
                   const unsigned char *data,
                   int len,
                   int16_t *pcm,
                   int max_samples);
void lc_opus_decoder_destroy(LcOpusDecoderRef decoder);

#ifdef __cplusplus
}
#endif

#endif  // LIVA_CHAT_COPUS_H_
