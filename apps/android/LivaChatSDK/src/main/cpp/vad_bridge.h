#ifndef VAD_BRIDGE_H_
#define VAD_BRIDGE_H_

#include <jni.h>

#ifdef __cplusplus
extern "C" {
#endif

int register_vad_natives(JNIEnv* env);

#ifdef __cplusplus
}
#endif

#endif  // VAD_BRIDGE_H_
