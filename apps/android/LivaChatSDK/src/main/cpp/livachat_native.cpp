#include <android/log.h>
#include <jni.h>

#include "opus_bridge.h"
#include "vad_bridge.h"

#define TAG "livachat_native"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

jint JNI_OnLoad(JavaVM* vm, void* /*reserved*/) {
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) {
        LOGE("Failed to get JNIEnv");
        return JNI_ERR;
    }

    if (register_opus_encoder_natives(env) != 0) {
        LOGE("Failed to register OpusEncoder natives");
        return JNI_ERR;
    }

    if (register_opus_decoder_natives(env) != 0) {
        LOGE("Failed to register OpusDecoder natives");
        return JNI_ERR;
    }

    if (register_vad_natives(env) != 0) {
        LOGE("Failed to register VAD natives");
        return JNI_ERR;
    }

    LOGI("JNI_OnLoad: all natives registered");
    return JNI_VERSION_1_6;
}
