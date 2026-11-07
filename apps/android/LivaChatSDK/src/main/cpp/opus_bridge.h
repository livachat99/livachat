#ifndef OPUS_BRIDGE_H_
#define OPUS_BRIDGE_H_

#include <jni.h>

#ifdef __cplusplus
extern "C" {
#endif

int register_opus_encoder_natives(JNIEnv* env);
int register_opus_decoder_natives(JNIEnv* env);

#ifdef __cplusplus
}
#endif

#endif  // OPUS_BRIDGE_H_
