#include "opus_bridge.h"

#include <android/log.h>
#include <cstdlib>
#include <cstring>
#include <opus/opus.h>

#define TAG "opus_bridge"
#define LOGD(...) __android_log_print(ANDROID_LOG_DEBUG, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

// ── Opus Encoder JNI ──────────────────────────────────────────────

struct EncoderHandle {
    OpusEncoder* encoder;
    int sample_rate;
    int frame_samples;  // samples per frame: sr * 20ms / 1000
};

static jlong opus_encoder_nativeInit(JNIEnv* env, jobject /*thiz*/,
                                      jint sampleRate, jint channels) {
    int err = 0;
    OpusEncoder* enc = opus_encoder_create(sampleRate, channels,
                                            OPUS_APPLICATION_VOIP, &err);
    if (err != OPUS_OK || enc == nullptr) {
        LOGE("opus_encoder_create failed: %d", err);
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Failed to create Opus encoder");
        return 0;
    }

    opus_encoder_ctl(enc, OPUS_SET_BITRATE(32000));
    opus_encoder_ctl(enc, OPUS_SET_VBR(0));          // CBR
    opus_encoder_ctl(enc, OPUS_SET_COMPLEXITY(5));
    opus_encoder_ctl(enc, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE));

    auto* h = new EncoderHandle{
        enc, sampleRate,
        sampleRate * 20 / 1000,  // frame_samples
    };

    return reinterpret_cast<jlong>(h);
}

static jbyteArray opus_encoder_nativeEncode(JNIEnv* env, jobject /*thiz*/,
                                             jlong handle, jshortArray pcm,
                                             jint offset, jint len) {
    auto* h = reinterpret_cast<EncoderHandle*>(handle);
    if (h == nullptr) {
        env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),
                      "Encoder handle is null");
        return nullptr;
    }

    jshort* pcmData = env->GetShortArrayElements(pcm, nullptr);
    if (pcmData == nullptr) return nullptr;

    const int maxBytes = 256;
    unsigned char output[256];

    int nbytes = opus_encode(h->encoder,
                              pcmData + offset,
                              h->frame_samples,
                              output,
                              maxBytes);

    env->ReleaseShortArrayElements(pcm, pcmData, JNI_ABORT);

    if (nbytes < 0) {
        LOGE("opus_encode failed: %d", nbytes);
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Opus encode failed");
        return nullptr;
    }

    jbyteArray result = env->NewByteArray(nbytes);
    env->SetByteArrayRegion(result, 0, nbytes,
                             reinterpret_cast<const jbyte*>(output));
    return result;
}

static void opus_encoder_nativeRelease(JNIEnv* /*env*/, jobject /*thiz*/,
                                        jlong handle) {
    auto* h = reinterpret_cast<EncoderHandle*>(handle);
    if (h != nullptr) {
        opus_encoder_destroy(h->encoder);
        delete h;
    }
}

static JNINativeMethod gEncoderMethods[] = {
    {"nativeInit",    "(II)J",      (void*)opus_encoder_nativeInit},
    {"nativeEncode",  "(J[SII)[B",  (void*)opus_encoder_nativeEncode},
    {"nativeRelease", "(J)V",       (void*)opus_encoder_nativeRelease},
};

// ── Opus Decoder JNI ──────────────────────────────────────────────

struct DecoderHandle {
    OpusDecoder* decoder;
    int sample_rate;
    int frame_samples;
};

static jlong opus_decoder_nativeInit(JNIEnv* env, jobject /*thiz*/,
                                      jint sampleRate, jint channels) {
    int err = 0;
    OpusDecoder* dec = opus_decoder_create(sampleRate, channels, &err);
    if (err != OPUS_OK || dec == nullptr) {
        LOGE("opus_decoder_create failed: %d", err);
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Failed to create Opus decoder");
        return 0;
    }

    auto* h = new DecoderHandle{dec, sampleRate,
                                 sampleRate * 20 / 1000};
    return reinterpret_cast<jlong>(h);
}

static jshortArray opus_decoder_nativeDecode(JNIEnv* env, jobject /*thiz*/,
                                              jlong handle, jbyteArray opusData,
                                              jint offset, jint len) {
    auto* h = reinterpret_cast<DecoderHandle*>(handle);
    if (h == nullptr) {
        env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),
                      "Decoder handle is null");
        return nullptr;
    }

    jbyte* data = env->GetByteArrayElements(opusData, nullptr);
    if (data == nullptr) return nullptr;

    const int maxSamples = h->frame_samples * 2;
    opus_int16 pcm[5760];  // max for 120ms at 48kHz; 20ms at 24kHz = 480

    int nsamples = opus_decode(h->decoder,
                                reinterpret_cast<const unsigned char*>(data + offset),
                                len,
                                pcm,
                                maxSamples,
                                0);

    env->ReleaseByteArrayElements(opusData, data, JNI_ABORT);

    if (nsamples < 0) {
        LOGE("opus_decode failed: %d", nsamples);
        env->ThrowNew(env->FindClass("java/lang/RuntimeException"),
                      "Opus decode failed");
        return nullptr;
    }

    jshortArray result = env->NewShortArray(nsamples);
    env->SetShortArrayRegion(result, 0, nsamples, pcm);
    return result;
}

static void opus_decoder_nativeRelease(JNIEnv* /*env*/, jobject /*thiz*/,
                                        jlong handle) {
    auto* h = reinterpret_cast<DecoderHandle*>(handle);
    if (h != nullptr) {
        opus_decoder_destroy(h->decoder);
        delete h;
    }
}

static JNINativeMethod gDecoderMethods[] = {
    {"nativeInit",    "(II)J",     (void*)opus_decoder_nativeInit},
    {"nativeDecode",  "(J[BII)[S", (void*)opus_decoder_nativeDecode},
    {"nativeRelease", "(J)V",      (void*)opus_decoder_nativeRelease},
};

// ── Registration ──────────────────────────────────────────────────

int register_opus_encoder_natives(JNIEnv* env) {
    jclass clazz = env->FindClass(
        "dev/livachat/sdk/internal/processors/OpusEncoderImpl");
    if (clazz == nullptr) return -1;
    return env->RegisterNatives(clazz, gEncoderMethods,
                                 sizeof(gEncoderMethods) / sizeof(gEncoderMethods[0]));
}

int register_opus_decoder_natives(JNIEnv* env) {
    jclass clazz = env->FindClass(
        "dev/livachat/sdk/internal/processors/OpusDecoderImpl");
    if (clazz == nullptr) return -1;
    return env->RegisterNatives(clazz, gDecoderMethods,
                                 sizeof(gDecoderMethods) / sizeof(gDecoderMethods[0]));
}
