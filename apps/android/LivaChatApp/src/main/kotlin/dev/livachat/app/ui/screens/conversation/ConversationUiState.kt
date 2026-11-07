package dev.livachat.app.ui.screens.conversation

import android.media.MediaRecorder
import android.net.Uri
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.ConversationError
import dev.livachat.app.media.AudioInputDevice
import dev.livachat.app.media.AudioOutputDevice
import dev.livachat.app.ui.components.AgentJobCancelUiState
import dev.livachat.sdk.model.AgentJob
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState

enum class VideoVisibility { HIDDEN, ANIMATING_IN, VISIBLE, ANIMATING_OUT }

enum class TranscribeAudioSource {
    RAW, VOICE;

    /** 采集源映射（docs/android_audio_capture.md §3.3）：原声=MIC，语音=VOICE_COMMUNICATION。 */
    fun toMediaSource(): Int = when (this) {
        RAW -> MediaRecorder.AudioSource.MIC
        VOICE -> MediaRecorder.AudioSource.VOICE_COMMUNICATION
    }
}

data class ConversationUiState(
    val connectionState: ConnectionState = ConnectionState.DISCONNECTED,
    val sessionState: SessionState = SessionState.IDLE,
    val isMicMuted: Boolean = false,
    val isSpeakerMuted: Boolean = false,
    // 屏幕录制中（stub 时期仅 UI 标志，现由 ScreenCaptureController.isRecording 事实源写入）
    val isRecording: Boolean = false,
    val isCameraOn: Boolean = false,
    val isFrontCamera: Boolean = true,
    val messages: List<ChatMessage> = emptyList(),
    val currentTranscription: String = "",
    val isTranscriptionFinal: Boolean = false,
    val error: ConversationError? = null,
    val videoVisibility: VideoVisibility = VideoVisibility.HIDDEN,
    val isFirstSession: Boolean = true,
    val customAvatarUri: Uri? = null,
    val avatarVisible: Boolean = true,
    val showAvatarSheet: Boolean = false,
    val showAvatarCrop: Boolean = false,
    val avatarCropSourceUri: Uri? = null,
    /** 图库选图预览（docs/camera_enhance.md §10）：非 null = 全屏预览显示中。 */
    val galleryPreviewUri: Uri? = null,
    val isTranslateMode: Boolean = false,
    val showLanguagePicker: Boolean = false,
    val isLectureMode: Boolean = false,
    val showLecturePicker: Boolean = false,
    val isTranscribeMode: Boolean = false,
    val showTranscribeSourcePicker: Boolean = false,
    // 麦克风输入设备（docs/android_audio_input_devices.md §5.1）：manager 枚举列表 / 弹窗可见性 / 选中 id（null=系统默认）
    val micInputDevices: List<AudioInputDevice> = emptyList(),
    val showMicDevicePicker: Boolean = false,
    val selectedMicInputDeviceId: Int? = null,
    // 扬声器输出设备（docs/android_audio_output_devices.md §5.1）：同构三件套
    val outputDevices: List<AudioOutputDevice> = emptyList(),
    val showOutputDevicePicker: Boolean = false,
    val selectedOutputDeviceId: Int? = null,
    val isOmniMode: Boolean = false,
    // 全模态说明弹窗（选中"全模态"后展示；确认才发送 session.change）
    val showOmniIntroDialog: Boolean = false,
    // 服务器配置未齐备（地址/密钥任一为空或非法）引导弹窗：不可外点/返回关闭，
    // 单按钮「去设置」（docs/setting_server_address.md §3.4 二改）
    val showServerConfigPrompt: Boolean = false,
    // 一次性 AI 提示（对齐 iOS ai_generated_notice）：每个安装显示一次，停留到用户关闭
    val showAiGeneratedNotice: Boolean = false,
    // 转录采集源选择（原声/语音）；由派生观测 collector 下发采集层（docs/android_audio_capture.md §3.3）
    val transcribeAudioSource: TranscribeAudioSource = TranscribeAudioSource.VOICE,
    val streamingAiText: String = "",
    val agentJobs: List<AgentJob> = emptyList(),
    val showJobStatusPanel: Boolean = false,
    /** 行内取消确认态（× 触发）；null = 无确认。 */
    val agentJobCancel: AgentJobCancelUiState? = null,
    // 屏幕录制 App 级说明弹窗可见性（首次点击展示一次）
    val showScreenRecordRationale: Boolean = false,
    // 录制中点击顶栏按钮的"停止确认"弹窗可见性（防误触立即停录）
    val showStopRecordingConfirm: Boolean = false,
    // 一次性"发起系统 MediaProjection 授权 intent"信号，由 Screen 消费后回调清除
    val pendingScreenCaptureLaunch: Boolean = false,
    // 一次性"发起悬浮窗（SYSTEM_ALERT_WINDOW）设置页 intent"信号，消费语义同上
    // （docs/android_screen_recording.md §3）
    val pendingOverlayPermissionLaunch: Boolean = false,
    /** 全屏图片查看器：非 null = 展示中，值为入口消息 id（"ai-<messageId>"）。 */
    val imageViewerMessageId: String? = null,
    /** 全屏 agent job 结果查看器：非 null = 展示中，值为入口消息 id（"ai-msg_agent_job_*"）。 */
    val agentJobResultViewId: String? = null,
    /** 全屏 agent job 结果查看器的落位偏移（打开时点击处的正文字符偏移；null = 落顶部）。文档 §10.2。 */
    val agentJobResultInitialCharOffset: Int? = null,
    /** 查看器保存提示（「已保存到本地相册」/「保存失败」…）；非 null 时底部胶囊可见，2s 后自动清空。 */
    val imageSaveTip: String? = null,
)
{

    // 仅 chat 模式可录屏/抓屏（translate/lecture/transcribe/omni 一律禁用；
    // docs/android_screen_recording.md §1.2）
    val canScreenRecord: Boolean
        get() = !(isTranslateMode || isLectureMode || isTranscribeMode || isOmniMode)
}
