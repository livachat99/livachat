package dev.livachat.sdk

import dev.livachat.sdk.model.AudioSampleRate
import dev.livachat.sdk.model.VadSensitivity

/**
 * Configuration for [LivaChatSDK]. Constructed once at SDK initialization; values are
 * fixed for the lifetime of the SDK instance, except [languageProvider], which is
 * evaluated on every session.start.
 */
data class LivaChatConfig(
    /** WebSocket server URL, e.g. "wss://host/v1/ws/stream" */
    val serverUrl: String,

    /** Wire token：明文 secret 的固定盐摘要 sha1_hex(secret + "LivaChat")（docs/server_auth.md）；
     *  App 在装配时派生（明文 secret 不上网）；SDK 只透传，不感知语义。 */
    val token: String,

    /** Persistent UUID v4 per device; App is responsible for persistence. */
    val deviceId: String,

    /** Client version carried in session.start, e.g. "android-1.0.0.130". */
    val clientVersion: String,

    /** Microphone sample rate (must match the App's AudioRecord config). */
    val audioSampleRate: AudioSampleRate = AudioSampleRate.RATE_16000,

    /** Audio frame duration in milliseconds (20 or 40). */
    val audioFrameMs: Int = 20,

    /** VAD aggressiveness for local voice-activity detection. */
    val vadSensitivity: VadSensitivity = VadSensitivity.MEDIUM,

    /**
     * 上行电平补偿（makeup-only AGC，docs/android_audio_capture.md §4.3）。
     * 默认关：SDK 不默认引入 DSP；由 App 按设备实测显式开启。
     * 生效点在 VAD 之后、编码之前；AI 说话期间自动保持 unity。
     */
    val uplinkAgc: Boolean = false,

    /** Default JPEG quality for video frames (1–100). */
    val videoQuality: Int = 70,

    /** Maximum dimension (width or height) of a video frame in pixels. */
    val videoMaxLongSide: Int = 1080,

    /** Whether the client is willing to stream video. */
    val enableVideo: Boolean = true,

    /** Maximum number of consecutive reconnect attempts before giving up. */
    val reconnectMaxAttempts: Int = 5,

    /** Initial backoff delay in milliseconds. Doubles each attempt. */
    val reconnectBaseDelayMs: Long = 1_000L,

    /** Maximum backoff delay cap in milliseconds. */
    val reconnectMaxDelayMs: Long = 30_000L,

    /** Interval between client-initiated keepalive pings in seconds. */
    val keepaliveIntervalS: Int = 15,

    /** Time without any message before the connection is considered dead. */
    val keepaliveTimeoutS: Int = 35,

    /** Optional path to a log file. When null, logging goes to logcat only. */
    val logFilePath: String? = null,

    /**
     * Client local timezone ID reported in session.start (timezone-support.md §4).
     * When null, the SDK reads TimeZone.getDefault().id on every session.start,
     * so travel across time zones takes effect naturally. Host apps may override
     * explicitly (e.g. for testing). Sent as-is; the server canonicalizes it.
     */
    val timezone: String? = null,

    /**
     * App UI language reported in session.start.config.language.
     * null = omit the field (old integrations / SDK users that do not set it).
     */
    val language: String? = null,

    /**
     * Optional dynamic provider, evaluated on every session.start.
     * Takes precedence over [language]; a null result falls back to [language].
     */
    val languageProvider: (() -> String?)? = null,

    /**
     * 初始 AVDetector 会话级开关（App 从持久化设置读取）。默认 true，与服务器缺省一致。
     * 运行期通过 [LivaChatSDK.setAvdetectorEnabled] 更新；SDK 会在每次 session.start 携带，
     * 并在已建立会话时发送 session.change(mode="config")。
     */
    val avdetectorEnabled: Boolean = true,

    /**
     * 是否允许自签名证书（对齐 iOS tlsAllowSelfSigned）。
     * SDK 默认 false（系统校验）；App 传入用户设置（默认 true）。
     * 只影响 TLS 证书校验，不改变 URL scheme。
     */
    val allowSelfSigned: Boolean = false,
)
