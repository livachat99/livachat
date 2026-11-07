package dev.livachat.app.domain.model

data class UserPreferences(
    val vadInterruptEnabled: Boolean = false,
    val defaultFrontCamera: Boolean = false,
    val autoCameraOnJoin: Boolean = false,
    val hapticEnabled: Boolean = true,
    val avatarVisible: Boolean = true,
    val screenRecordRationaleShown: Boolean = false,
    /** 一次性 AI 提示是否已关闭（对齐 iOS `settings.ai_generated_notice_shown`）。 */
    val aiGeneratedNoticeShown: Boolean = false,
    /** 是否允许自签名证书（对齐 iOS `tlsAllowSelfSigned`，默认 true）。 */
    val tlsAllowSelfSigned: Boolean = true,
    /** 允许 AI 主动搭话（AVDetector 会话级开关；对齐 iOS `settings.avdetector_enabled`，默认 true）。 */
    val avdetectorEnabled: Boolean = true,
)
