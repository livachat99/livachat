package dev.livachat.app.ui.screens.conversation

import android.net.Uri
import dev.livachat.sdk.model.AgentJob

sealed class ConversationEvent {
    data object ToggleRecording : ConversationEvent()
    data object ConfirmScreenRecordRationale : ConversationEvent()
    data object DismissScreenRecordRationale : ConversationEvent()
    // 录制中点击顶栏按钮 → 停止确认弹窗；Confirm = 点"停止"，Dismiss = "取消"/点外部
    data object ConfirmStopScreenRecord : ConversationEvent()
    data object DismissStopScreenRecord : ConversationEvent()
    data object ConsumeScreenCaptureLaunch : ConversationEvent()
    data object ScreenCaptureConsentDenied : ConversationEvent()
    // 悬浮窗权限（SYSTEM_ALERT_WINDOW）设置页：消费启动信号 + 返回后由 VM 复查
    // canDrawOverlays（Screen 不做判定，docs/android_screen_recording.md §3）
    data object ConsumeOverlayPermissionLaunch : ConversationEvent()
    data object OverlayPermissionReturned : ConversationEvent()
    data object ToggleMic : ConversationEvent()
    data object ToggleSpeaker : ConversationEvent()
    data object ToggleCamera : ConversationEvent()
    data object SwitchCamera : ConversationEvent()
    /** 图库单图选定：先进入全屏预览（docs/camera_enhance.md §10）。 */
    data class OpenGalleryPreview(val uri: Uri) : ConversationEvent()
    /** 关闭图库预览（右侧取消，回主屏幕；左返回走 onBackToPicker 不经过本事件）。 */
    data object DismissGalleryPreview : ConversationEvent()
    /** 预览点击发送：经 GalleryPhotoSender 规范化后作为 video 帧上行（§9）。 */
    data class SendGalleryPhoto(val uri: Uri) : ConversationEvent()
    data object RetryConnection : ConversationEvent()
    data object DismissError : ConversationEvent()
    data object DismissAiGeneratedNotice : ConversationEvent()
    data class SendChipMessage(val text: String) : ConversationEvent()
    data object NavigateToHistory : ConversationEvent()
    data object NavigateToSettings : ConversationEvent()
    // 麦克风输入设备选择（docs/android_audio_input_devices.md §5.1）；Select 的 id=null =「系统默认」
    data object ShowMicDevicePicker : ConversationEvent()
    data object DismissMicDevicePicker : ConversationEvent()
    data class SelectMicInputDevice(val id: Int?) : ConversationEvent()
    // 扬声器输出设备选择（docs/android_audio_output_devices.md §5.1）；Select 的 id=null =「系统默认」
    data object ShowOutputDevicePicker : ConversationEvent()
    data object DismissOutputDevicePicker : ConversationEvent()
    data class SelectOutputDevice(val id: Int?) : ConversationEvent()
    data object OnScreenStart : ConversationEvent()
    data object OnScreenStop : ConversationEvent()
    data object ShowAvatarSheet : ConversationEvent()
    data object DismissAvatarSheet : ConversationEvent()
    data class SetAvatarVisible(val visible: Boolean) : ConversationEvent()
    data object RemoveCustomAvatar : ConversationEvent()
    data object StartAvatarCrop : ConversationEvent()
    data object CancelAvatarCrop : ConversationEvent()
    data object ToggleTranslateMode : ConversationEvent()
    data class ConfirmTranslateLanguage(val languageCode: String) : ConversationEvent()
    data object DismissLanguagePicker : ConversationEvent()
    data object ToggleLectureMode : ConversationEvent()
    data class ConfirmLectureLanguage(val languageCode: String) : ConversationEvent()
    data object DismissLecturePicker : ConversationEvent()
    data object ToggleTranscribeMode : ConversationEvent()
    data class ConfirmTranscribeSource(val source: TranscribeAudioSource) : ConversationEvent()
    data object DismissTranscribeSourcePicker : ConversationEvent()
    data object ToggleOmniMode : ConversationEvent()
    data object ConfirmOmniMode : ConversationEvent()
    data object DismissOmniDialog : ConversationEvent()
    data object ToggleJobStatusPanel : ConversationEvent()
    data object DismissJobStatusPanel : ConversationEvent()
    /** 面板 meta 行 ×：进入该 job 的行内确认态（未 inFlight 时可切换目标）。 */
    data class RequestAgentJobCancel(val job: AgentJob) : ConversationEvent()
    /** 行内确认「否」：收起确认态；inFlight 时 VM 忽略。 */
    data object DismissAgentJobCancel : ConversationEvent()
    /** 行内确认「是」：发起 REST 取消。 */
    data object ConfirmAgentJobCancel : ConversationEvent()
    data class OpenImageViewer(val messageId: String) : ConversationEvent()
    data object CloseImageViewer : ConversationEvent()
    /** 保存查看器当前页图片到系统相册（messageId 为当前页消息 id）。 */
    data class SaveViewerImage(val messageId: String) : ConversationEvent()
    /** API ≤ 28 存储权限被拒：只提示，不做"去设置"引导。 */
    data object SaveViewerImagePermissionDenied : ConversationEvent()
    /**
     * 打开 agent job 结果全屏查看（messageId 为气泡消息 id，"ai-msg_agent_job_*"）。
     * [charOffset] = 点击处的正文字符偏移（全屏落位用，文档 §10.2）；null = 落顶部。
     */
    data class OpenAgentJobResult(
        val messageId: String,
        val charOffset: Int? = null,
    ) : ConversationEvent()
    data object CloseAgentJobResult : ConversationEvent()
}
