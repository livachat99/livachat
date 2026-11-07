#include "vad_bridge.h"

#include <android/log.h>
#include <cstdlib>
#include <webrtc_vad_lite.h>
#include <webrtc_vad.h>

#define TAG "vad_bridge"
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

struct VadHandle {
    WvadInst* inst;
    int sample_rate;
};

static jlong vad_nativeInit(JNIEnv* env, jobject /*thiz*/,
                             jint sampleRate, jint mode) {
    // Validate rate + frame length: 20ms frames
    size_t frameSamples = sampleRate * 20 / 1000;
    if (WebRtcVad_ValidRateAndFrameLength(sampleRate, frameSamples) != 0) {
        LOGE("Invalid VAD rate (%d) and frame length (%zu)", sampleRate, frameSamples);
        env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),
                      "Invalid VAD rate and frame length combination");
        return 0;
    }

    auto* h = new VadHandle{nullptr, sampleRate};

    h->inst = Wvad_Create(mode);
    if (h->inst == nullptr) {
        delete h;
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Failed to create VAD instance");
        return 0;
    }

    int rc = WebRtcVad_Init(reinterpret_cast<VadInst*>(h->inst));
    if (rc != 0) {
        Wvad_Free(h->inst);
        delete h;
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Failed to init VAD instance");
        return 0;
    }

    rc = Wvad_SetMode(h->inst, mode);
    if (rc != 0) {
        Wvad_Free(h->inst);
        delete h;
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Failed to set VAD mode");
        return 0;
    }

    return reinterpret_cast<jlong>(h);
}

static jint vad_nativeDetect(JNIEnv* env, jobject /*thiz*/,
                              jlong handle, jshortArray pcm,
                              jint offset, jint len) {
    auto* h = reinterpret_cast<VadHandle*>(handle);
    if (h == nullptr) {
        env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),
                      "VAD handle is null");
        return -1;
    }

    size_t frameSamples = static_cast<size_t>(h->sample_rate * 20 / 1000);
    if (static_cast<size_t>(len) < frameSamples) {
        env->ThrowNew(env->FindClass("java/lang/IllegalArgumentException"),
                      "PCM length too small for 20ms frame");
        return -1;
    }

    jshort* pcmData = env->GetShortArrayElements(pcm, nullptr);
    if (pcmData == nullptr) return -1;

    int result = Wvad_Process(h->inst,
                               h->sample_rate,
                               pcmData + offset,
                               frameSamples);

    env->ReleaseShortArrayElements(pcm, pcmData, JNI_ABORT);

    return static_cast<jint>(result);
}

static void vad_nativeRelease(JNIEnv* /*env*/, jobject /*thiz*/,
                               jlong handle) {
    auto* h = reinterpret_cast<VadHandle*>(handle);
    if (h != nullptr) {
        Wvad_Free(h->inst);
        delete h;
    }
}

static JNINativeMethod gVadMethods[] = {
    {"nativeInit",    "(II)J",    (void*)vad_nativeInit},
    {"nativeDetect",  "(J[SII)I", (void*)vad_nativeDetect},
    {"nativeRelease", "(J)V",     (void*)vad_nativeRelease},
};

int register_vad_natives(JNIEnv* env) {
    jclass clazz = env->FindClass(
        "dev/livachat/sdk/internal/processors/VadDetectorImpl");
    if (clazz == nullptr) return -1;
    return env->RegisterNatives(clazz, gVadMethods,
                                 sizeof(gVadMethods) / sizeof(gVadMethods[0]));
}
