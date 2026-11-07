package dev.livachat.app.ui.screens.conversation

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.camera.view.PreviewView
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.data.remote.mylife.MylifeApiException
import dev.livachat.app.data.repository.AgentJobRepository
import dev.livachat.app.data.repository.AvatarRepository
import dev.livachat.app.data.repository.ChatRepository
import dev.livachat.app.data.repository.SettingsRepository
import dev.livachat.app.domain.ConversationCoordinator
import dev.livachat.app.domain.model.ConversationError
import dev.livachat.app.domain.model.UserPreferences
import dev.livachat.app.domain.model.isServerConfigComplete
import dev.livachat.app.R
import dev.livachat.app.media.AudioCaptureController
import dev.livachat.app.media.AudioDeviceManager
import dev.livachat.app.media.AudioPlaybackController
import dev.livachat.app.media.CameraController
import dev.livachat.app.media.CaptureTurnCounter
import dev.livachat.app.media.GalleryPhotoSender
import dev.livachat.app.media.GallerySendResult
import dev.livachat.app.media.HapticController
import dev.livachat.app.media.ImageGallerySaver
import dev.livachat.app.media.OverlayPermissions
import dev.livachat.app.media.ScreenCaptureController
import dev.livachat.app.ui.components.AgentJobCancelUiState
import dev.livachat.sdk.LivaChatSDK
import dev.livachat.sdk.model.ConnectionState
import dev.livachat.sdk.model.SessionState
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.inject.Inject

@HiltViewModel
class ConversationViewModel @Inject constructor(
    private val sdk: LivaChatSDK,
    private val audioCapture: AudioCaptureController,
    private val audioPlayback: AudioPlaybackController,
    private val cameraController: CameraController,
    private val turnCounter: CaptureTurnCounter,
    private val galleryPhotoSender: GalleryPhotoSender,
    private val screenCaptureController: ScreenCaptureController,
    private val overlayPermissions: OverlayPermissions,
    private val chatRepository: ChatRepository,
    private val settingsRepository: SettingsRepository,
    private val listenerAdapter: ConversationListenerAdapter,
    private val haptic: HapticController,
    private val coordinator: ConversationCoordinator,
    private val avatarRepository: AvatarRepository,
    private val imageGallerySaver: ImageGallerySaver,
    private val deviceManager: AudioDeviceManager,
    private val agentJobRepository: AgentJobRepository,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ConversationUiState())
    val uiState: StateFlow<ConversationUiState> = _uiState.asStateFlow()

    // 电平走独立 StateFlow 透传、UI 叶子节点 collect：50Hz 高频更新若进 uiState
    // （大 combine/整体 copy）会驱动整屏重组，故不并入 ConversationUiState
    val micLevel: StateFlow<Int> = audioCapture.micLevel

    private var previousSessionState: SessionState = SessionState.IDLE
    private var cameraLifecycle: LifecycleOwner? = null
    private var cameraPreviewView: PreviewView? = null
    private var messageObservationJob: Job? = null
    private var desiredFrontCamera: Boolean = false
    private var hapticEnabled: Boolean = true
    private var defaultFrontCameraSetting: Boolean = false
    private var autoCameraOnJoin: Boolean = false
    // 服务器配置门控（docs/setting_server_address.md §3.4）：首次发射前不决策启动
    // （DataStore 首读晚于 OnScreenStart；用默认值误启动会用占位 URL 建连/开会话）；
    // 不齐备 → 不启动连接与采集。提示策略（三改）：本次运行首次检测到不齐备提示一次，
    // 此后仅点击 app bar 连接按钮（RetryConnection）再提示，其他时机不提示；进程重启后重复
    private var serverConfigLoaded: Boolean = false
    private var serverConfigIncomplete: Boolean = false
    private var pendingScreenStart: Boolean = false
    private var serverConfigPromptShown: Boolean = false
    private var aiNoticeInitialized: Boolean = false

    // 保存提示的 TTL 定时器（取消语义见 showTipWithTimeout）；保存进行中的重入守卫
    private var tipJob: Job? = null
    private var saveInFlight = false

    // 偏好单一事实源（DataStore 镜像 + 本地乐观更新）：collect 每轮发射整体覆盖；
    // Confirm 时同步乐观更新，避免 collect 的旧快照晚到回写覆盖"已展示"判定（见 toggleScreenRecording）
    private val prefsState = MutableStateFlow(UserPreferences())

    init {
        // Load custom avatar on init
        viewModelScope.launch {
            val uri = avatarRepository.getAvatarUri()
            _uiState.value = _uiState.value.copy(customAvatarUri = uri)
        }

        // Observe coordinator session ID and switch message observation
        viewModelScope.launch {
            coordinator.currentSessionId.collect { sessionId ->
                messageObservationJob?.cancel()
                // 换会话即关查看器：避免覆盖层指向已切换 session 的图片列表
                clearSaveTip()
                _uiState.value = _uiState.value.copy(
                    imageViewerMessageId = null,
                    agentJobResultViewId = null,
                    agentJobResultInitialCharOffset = null,
                )
                if (sessionId != null) {
                    messageObservationJob = viewModelScope.launch {
                        chatRepository.observeMessages(sessionId).collect { messages ->
                            _uiState.value = _uiState.value.copy(messages = messages)
                        }
                    }
                } else {
                    _uiState.value = _uiState.value.copy(messages = emptyList())
                }
            }
        }

        // Observe isFirstLaunch from persisted preferences
        viewModelScope.launch {
            settingsRepository.isFirstLaunch.collect { isFirst ->
                _uiState.value = _uiState.value.copy(isFirstSession = isFirst)
            }
        }

        // Combine SDK + controller state into UiState
        viewModelScope.launch {
            combine(
                combine(
                    sdk.connectionState,
                    sdk.sessionState,
                    audioCapture.isMuted,
                    audioPlayback.isMuted,
                ) { conn, sess, micMuted, spkMuted ->
                    arrayOf(conn, sess, micMuted, spkMuted)
                },
                combine(
                    cameraController.isFrontCamera,
                    listenerAdapter.transcription,
                    listenerAdapter.isTranscriptionFinal,
                ) { frontCam, transcription, transFinal ->
                    Triple(frontCam, transcription, transFinal)
                },
                screenCaptureController.isRecording,
            ) { arr, triple, rec ->
                @Suppress("UNCHECKED_CAST")
                val conn = arr[0] as ConnectionState
                val sess = arr[1] as SessionState
                val micMuted = arr[2] as Boolean
                val spkMuted = arr[3] as Boolean
                val (frontCam, transcription, transFinal) = triple

                _uiState.value = _uiState.value.copy(
                    connectionState = conn,
                    sessionState = sess,
                    isMicMuted = micMuted,
                    isSpeakerMuted = spkMuted,
                    isRecording = rec,
                    // 非确认路径停录（相机互斥/系统侧停投屏/会话终结）后强制复位停止确认
                    // 标志——否则残留 true 会在下次开录瞬间无点击自动弹出确认框
                    showStopRecordingConfirm = rec && _uiState.value.showStopRecordingConfirm,
                    isFrontCamera = frontCam,
                    currentTranscription = transcription,
                    isTranscriptionFinal = transFinal,
                )

                if (sess == SessionState.SPEAKING && previousSessionState != SessionState.SPEAKING && hapticEnabled) {
                    haptic.lightTap()
                }
                previousSessionState = sess
            }.collect {}
        }

        viewModelScope.launch {
            listenerAdapter.errors.collect { error ->
                _uiState.value = _uiState.value.copy(error = error)
            }
        }

        viewModelScope.launch {
            listenerAdapter.translateMode.collect { enabled ->
                _uiState.value = _uiState.value.copy(isTranslateMode = enabled)
                if (enabled) stopRecordingIfActive()
            }
        }

        viewModelScope.launch {
            listenerAdapter.lectureMode.collect { enabled ->
                _uiState.value = _uiState.value.copy(isLectureMode = enabled)
                if (enabled) stopRecordingIfActive()
            }
        }

        viewModelScope.launch {
            listenerAdapter.transcribeMode.collect { enabled ->
                _uiState.value = _uiState.value.copy(isTranscribeMode = enabled)
                if (enabled) stopRecordingIfActive()
            }
        }

        // omni 模式镜像：服务端回声（onSessionChanged）、断网恢复的乐观置位、以及
        // 1009 失败回落都只写 adapter 的 _omniMode——缺此 collector 会让 uiState 失实
        // （开关/徽标错、canScreenRecord 卡死禁录）
        viewModelScope.launch {
            listenerAdapter.omniMode.collect { enabled ->
                _uiState.value = _uiState.value.copy(isOmniMode = enabled)
                if (enabled) stopRecordingIfActive()
            }
        }

        // 转录采集源切换（docs/android_audio_capture.md §3.3）：目标源是 UiState
        // (isTranscribeMode, transcribeAudioSource) 的纯函数，controller 幂等短路，
        // 可无条件调用。不直接订阅 listenerAdapter.transcribeMode 并回读 UiState——
        // 模式进出是乐观双写（ConfirmTranscribeSource 先 setTranscribeMode 再写
        // UiState），回读会被乐观写入破坏（定稿附录 A）。UiState 快照是唯一一致事实源。
        // 承重依赖：本 collector 依赖上方 transcribeMode collector 把 adapter 变化回写进
        // UiState；删除/绕过该回写（如直写 UiState）会使本 collector 静默失效。
        viewModelScope.launch {
            _uiState
                .map { it.isTranscribeMode to it.transcribeAudioSource }
                .distinctUntilChanged()
                .collect { (transcribe, source) ->
                    audioCapture.switchAudioSource(
                        if (transcribe && source == TranscribeAudioSource.RAW)
                            TranscribeAudioSource.RAW.toMediaSource()
                        else TranscribeAudioSource.VOICE.toMediaSource(),
                    )
                }
        }

        viewModelScope.launch {
            listenerAdapter.streamingAiText.collect { text ->
                _uiState.value = _uiState.value.copy(streamingAiText = text)
            }
        }

        viewModelScope.launch {
            listenerAdapter.agentJobs.collect { jobs ->
                val pending = _uiState.value.agentJobCancel
                val stillThere = pending != null && jobs.any { it.jobId == pending.job.jobId }
                _uiState.value = _uiState.value.copy(
                    agentJobs = jobs,
                    // D5: auto-close the panel when the list empties (incl. D6 self-clear paths).
                    showJobStatusPanel = if (jobs.isEmpty()) false
                                         else _uiState.value.showJobStatusPanel,
                    // 取消成功/自然完成/断连/session.ended：目标 job 消失即清确认态
                    agentJobCancel = if (pending != null && !stillThere) null else pending,
                )
            }
        }

        // 断网重连自动恢复：session.ready 后 adapter 按最近一次用户模式请求
        // 触发补发 session.change（translate/transcribe）
        viewModelScope.launch {
            listenerAdapter.restoreModeRequest.collect { request ->
                when (request.mode) {
                    "translate" -> sdk.sendSessionChange(
                        "translate", request.myLanguage, request.localLanguage,
                        usecase = request.usecase,
                    )
                    "transcribe" -> sdk.sendSessionChange("transcribe")
                    "omni" -> sdk.sendSessionChange("omni")
                }
            }
        }

        viewModelScope.launch {
            settingsRepository.observePreferences().collect { prefs ->
                hapticEnabled = prefs.hapticEnabled
                defaultFrontCameraSetting = prefs.defaultFrontCamera
                autoCameraOnJoin = prefs.autoCameraOnJoin
                prefsState.value = prefs
                if (!aiNoticeInitialized) {
                    aiNoticeInitialized = true
                    if (!prefs.aiGeneratedNoticeShown) {
                        _uiState.value = _uiState.value.copy(showAiGeneratedNotice = true)
                    }
                }
                _uiState.value = _uiState.value.copy(avatarVisible = prefs.avatarVisible)
            }
        }

        // 麦克风输入设备列表（docs/android_audio_input_devices.md §5.1）：manager 进程级监听，本处镜像进 UiState
        viewModelScope.launch {
            deviceManager.inputDevices.collect { devices ->
                _uiState.value = _uiState.value.copy(micInputDevices = devices)
            }
        }

        // 选中的输入设备：控制器为事实源（设备被拔/解析失败已被控制器清空 → UI 反显「系统默认」）
        viewModelScope.launch {
            audioCapture.preferredInputDeviceId.collect { id ->
                _uiState.value = _uiState.value.copy(selectedMicInputDeviceId = id)
            }
        }

        // 扬声器输出设备列表（docs/android_audio_output_devices.md §5.1）：manager 双向监听，本处镜像进 UiState
        viewModelScope.launch {
            deviceManager.outputDevices.collect { devices ->
                _uiState.value = _uiState.value.copy(outputDevices = devices)
            }
        }

        // 选中的输出设备：控制器为事实源（断开回落/解析失败兜底均收敛在控制器 → UI 直接反显）
        viewModelScope.launch {
            audioPlayback.preferredOutputDeviceId.collect { id ->
                _uiState.value = _uiState.value.copy(selectedOutputDeviceId = id)
            }
        }

        // D11：会话终结（回 IDLE）联动停录；瞬态断连（DISCONNECTED）不停录
        viewModelScope.launch {
            sdk.sessionState.collect { state ->
                if (state == SessionState.IDLE) screenCaptureController.stop()
            }
        }

        // 抓屏链路查询"当前是否全模态"（omni 下不发送 payload.text，D6b）
        screenCaptureController.isOmniModeProvider = { listenerAdapter.omniMode.value }

        // 录制启动的系统级失败（授权 token 失效/VD 创建异常）→ ErrorBanner
        viewModelScope.launch {
            screenCaptureController.startErrors.collect { message ->
                _uiState.value = _uiState.value.copy(error = ConversationError.Unknown(message))
            }
        }

        // 服务器配置门控（docs/setting_server_address.md §3.4）：地址/密钥不齐备 → 不启动连接与采集
        // （进程装配为占位 URL，真实连接由此门控拦截）。提示策略（三改）：本次运行首次检测到
        // 不齐备提示一次（可外点/返回关闭），此后仅连接按钮（RetryConnection）再提示。
        // 首发射前 OnScreenStart 已到达 → 延后决策（pendingScreenStart），本发射才启动，
        // 避免用未加载的默认值误启动（占位连接/空会话/短暂开麦）与 stop 竞态
        viewModelScope.launch {
            settingsRepository.observeServerConfig().collect { config ->
                val incomplete = !isServerConfigComplete(config.address, config.token)
                val wasLoaded = serverConfigLoaded
                serverConfigLoaded = true
                serverConfigIncomplete = incomplete
                if (incomplete) {
                    pendingScreenStart = false
                    if (!serverConfigPromptShown) {
                        serverConfigPromptShown = true
                        _uiState.value = _uiState.value.copy(showServerConfigPrompt = true)
                    }
                    if (coordinator.isActive.value) coordinator.stop()
                } else {
                    _uiState.value = _uiState.value.copy(showServerConfigPrompt = false)
                    if (!wasLoaded && pendingScreenStart) {
                        pendingScreenStart = false
                        startScreenSession()
                    }
                }
            }
        }
    }

    /** 关闭「需要先配置服务器」引导弹窗（外点/返回/去设置 均走此处；再次提示仅靠连接按钮）。 */
    fun dismissServerConfigPrompt() {
        _uiState.value = _uiState.value.copy(showServerConfigPrompt = false)
    }

    /** 进入对话页的启动序列（连接 + 自动开相机）；门控决策后由 OnScreenStart 或配置首发射调用。 */
    private fun startScreenSession() {
        coordinator.start()
        // 录制中返回页面不自动开相机（互斥守卫，D9）
        if (autoCameraOnJoin && !screenCaptureController.isRecording.value
            && _uiState.value.videoVisibility != VideoVisibility.VISIBLE
        ) {
            desiredFrontCamera = defaultFrontCameraSetting
            // 新采集轮（docs/fd_pipeline_video_process.md §8）：自动开相机同样触发首帧直起轮
            turnCounter.next()
            _uiState.value = _uiState.value.copy(
                isCameraOn = true,
                videoVisibility = VideoVisibility.VISIBLE,
            )
        }
    }

    fun onEvent(event: ConversationEvent) {
        when (event) {
            ConversationEvent.OnScreenStart -> {
                when {
                    // 配置首发射未到：延后决策，避免用默认值误启动（§3.4 二改）
                    !serverConfigLoaded -> pendingScreenStart = true
                    // 配置不齐备：不启动连接/采集；提示只在运行首次与连接按钮（三改），此处不弹
                    serverConfigIncomplete -> Unit
                    else -> startScreenSession()
                }
            }
            ConversationEvent.OnScreenStop -> coordinator.stop()
            ConversationEvent.ToggleRecording -> toggleScreenRecording()
            ConversationEvent.ConfirmScreenRecordRationale -> {
                // 乐观更新 prefsState：DataStore 写盘完成前，本 VM 内一切读取
                // （含快速再次点击录制）都判定"已展示过"，不重弹说明
                prefsState.value = prefsState.value.copy(screenRecordRationaleShown = true)
                viewModelScope.launch { settingsRepository.setScreenRecordRationaleShown() }
                // 三段式（§5.3）：说明确认后按悬浮窗权限分流——缺权限先去系统设置页
                val canDraw = overlayPermissions.canDraw()
                _uiState.value = _uiState.value.copy(
                    showScreenRecordRationale = false,
                    pendingScreenCaptureLaunch = canDraw,
                    pendingOverlayPermissionLaunch = !canDraw,
                )
            }
            ConversationEvent.DismissScreenRecordRationale ->
                _uiState.value = _uiState.value.copy(showScreenRecordRationale = false)
            // 停止确认弹窗："停止" → 停录并关框；"取消"/点外部 → 仅关框，录制继续
            ConversationEvent.ConfirmStopScreenRecord -> {
                _uiState.value = _uiState.value.copy(showStopRecordingConfirm = false)
                if (screenCaptureController.isRecording.value) screenCaptureController.stop()
            }
            ConversationEvent.DismissStopScreenRecord ->
                _uiState.value = _uiState.value.copy(showStopRecordingConfirm = false)
            ConversationEvent.ConsumeScreenCaptureLaunch ->
                _uiState.value = _uiState.value.copy(pendingScreenCaptureLaunch = false)
            // 授权被拒/取消属正常路径：默认静默，无需复位（isRecording 本就为 false）
            ConversationEvent.ScreenCaptureConsentDenied -> {}
            ConversationEvent.ConsumeOverlayPermissionLaunch ->
                _uiState.value = _uiState.value.copy(pendingOverlayPermissionLaunch = false)
            // 悬浮窗权限设置页返回：唯一判定点在此复查（Screen 不做判定，避免双源）
            ConversationEvent.OverlayPermissionReturned ->
                if (overlayPermissions.canDraw()) {
                    _uiState.value = _uiState.value.copy(pendingScreenCaptureLaunch = true)
                } else {
                    // Go 设备/顽固拒绝走同一出口（§3.9）：不启动采集，给出可理解的原因
                    _uiState.value = _uiState.value.copy(
                        error = ConversationError.Unknown(
                            appContext.getString(R.string.conv_overlay_permission_required),
                        ),
                    )
                }
            ConversationEvent.ToggleMic -> audioCapture.setMuted(!_uiState.value.isMicMuted)
            ConversationEvent.ToggleSpeaker -> audioPlayback.setMuted(!_uiState.value.isSpeakerMuted)
            ConversationEvent.ToggleCamera -> toggleCamera()
            ConversationEvent.SwitchCamera -> switchCamera()
            is ConversationEvent.OpenGalleryPreview ->
                _uiState.value = _uiState.value.copy(galleryPreviewUri = event.uri)
            ConversationEvent.DismissGalleryPreview ->
                _uiState.value = _uiState.value.copy(galleryPreviewUri = null)
            is ConversationEvent.SendGalleryPhoto -> {
                // 发送即关闭预览、回主屏幕；处理/上行在后台继续（§10.3）
                _uiState.value = _uiState.value.copy(galleryPreviewUri = null)
                sendGalleryPhoto(event.uri)
            }
            ConversationEvent.RetryConnection -> when {
                // 配置首发射未到：忽略点击（毫秒级窗口；防以占位/旧默认值误发起连接）
                !serverConfigLoaded -> Unit
                // 未配置期点连接按钮 → 再次提示引导（不发起连接；§3.4 三改）
                serverConfigIncomplete ->
                    _uiState.value = _uiState.value.copy(showServerConfigPrompt = true)
                else -> sdk.connect()
            }
            ConversationEvent.DismissError -> _uiState.value = _uiState.value.copy(error = null)
            ConversationEvent.DismissAiGeneratedNotice -> {
                // 乐观更新 prefsState：DataStore 写盘完成前，本 VM 内不再判定为“未关闭”
                prefsState.value = prefsState.value.copy(aiGeneratedNoticeShown = true)
                // NonCancellable：用户点关闭后立刻离开页面时，onCleared 取消 scope 也要写完偏好
                viewModelScope.launch {
                    withContext(NonCancellable) { settingsRepository.setAiGeneratedNoticeShown() }
                }
                _uiState.value = _uiState.value.copy(showAiGeneratedNotice = false)
            }
            is ConversationEvent.SendChipMessage -> sendChipMessage(event.text)
            ConversationEvent.NavigateToHistory -> {}
            ConversationEvent.NavigateToSettings -> {}
            ConversationEvent.ShowMicDevicePicker ->
                _uiState.value = _uiState.value.copy(showMicDevicePicker = true)
            ConversationEvent.DismissMicDevicePicker ->
                _uiState.value = _uiState.value.copy(showMicDevicePicker = false)
            is ConversationEvent.SelectMicInputDevice -> {
                // 关窗 + 下发控制器；「同项不切换」的幂等短路由控制器保证（§5.4）
                _uiState.value = _uiState.value.copy(showMicDevicePicker = false)
                audioCapture.setPreferredInputDevice(event.id)
            }
            ConversationEvent.ShowOutputDevicePicker ->
                _uiState.value = _uiState.value.copy(showOutputDevicePicker = true)
            ConversationEvent.DismissOutputDevicePicker ->
                _uiState.value = _uiState.value.copy(showOutputDevicePicker = false)
            is ConversationEvent.SelectOutputDevice -> {
                // 关窗 + 下发控制器；幂等短路由控制器保证（docs/android_audio_output_devices.md §5.1）
                _uiState.value = _uiState.value.copy(showOutputDevicePicker = false)
                audioPlayback.setPreferredOutputDevice(event.id)
            }
            ConversationEvent.ShowAvatarSheet ->
                _uiState.value = _uiState.value.copy(showAvatarSheet = true)
            ConversationEvent.DismissAvatarSheet ->
                _uiState.value = _uiState.value.copy(showAvatarSheet = false)
            is ConversationEvent.SetAvatarVisible -> {
                _uiState.value = _uiState.value.copy(avatarVisible = event.visible)
                viewModelScope.launch { settingsRepository.setAvatarVisible(event.visible) }
            }
            ConversationEvent.RemoveCustomAvatar -> removeCustomAvatar()
            ConversationEvent.StartAvatarCrop ->
                _uiState.value = _uiState.value.copy(showAvatarSheet = false, showAvatarCrop = true)
            ConversationEvent.CancelAvatarCrop -> {
                val source = _uiState.value.avatarCropSourceUri
                _uiState.value = _uiState.value.copy(showAvatarCrop = false, avatarCropSourceUri = null)
                source?.let { viewModelScope.launch { avatarRepository.deleteCameraSourceIfOwned(it) } }
            }
            ConversationEvent.ToggleTranslateMode -> {
                if (_uiState.value.isTranslateMode) {
                    listenerAdapter.setTranslateMode(false)
                    listenerAdapter.setLectureMode(false)
                    listenerAdapter.setTranscribeMode(false)
                    listenerAdapter.setOmniMode(false)
                    listenerAdapter.rememberModeRequest(null)  // 回 chat，无需重连恢复
                    _uiState.value = _uiState.value.copy(
                        isTranslateMode = false, isLectureMode = false, isTranscribeMode = false,
                        isOmniMode = false,
                    )
                    sdk.sendSessionChange("chat")
                } else {
                    _uiState.value = _uiState.value.copy(showLanguagePicker = true, showLecturePicker = false)
                }
            }
            is ConversationEvent.ConfirmTranslateLanguage -> {
                stopRecordingIfActive()  // 非 chat 模式禁用录屏
                val deviceLanguage = java.util.Locale.getDefault().language
                listenerAdapter.setTranslateMode(true)
                listenerAdapter.setLectureMode(false)
                listenerAdapter.setTranscribeMode(false)
                listenerAdapter.setOmniMode(false)
                listenerAdapter.rememberModeRequest(
                    ModeChangeRequest("translate", deviceLanguage, event.languageCode, "dialogue"),
                )
                listenerAdapter.markModeChangePending("translate")
                _uiState.value = _uiState.value.copy(
                    isTranslateMode = true,
                    isLectureMode = false,
                    isTranscribeMode = false,
                    showLanguagePicker = false,
                    showAvatarSheet = false,
                    isOmniMode = false,
                )
                sdk.sendSessionChange("translate", deviceLanguage, event.languageCode, usecase = "dialogue")
            }
            ConversationEvent.DismissLanguagePicker -> {
                _uiState.value = _uiState.value.copy(showLanguagePicker = false)
            }
            ConversationEvent.ToggleLectureMode -> {
                if (_uiState.value.isLectureMode) {
                    listenerAdapter.setTranslateMode(false)
                    listenerAdapter.setLectureMode(false)
                    listenerAdapter.setTranscribeMode(false)
                    listenerAdapter.setOmniMode(false)
                    listenerAdapter.rememberModeRequest(null)  // 回 chat，无需重连恢复
                    _uiState.value = _uiState.value.copy(
                        isTranslateMode = false, isLectureMode = false, isTranscribeMode = false,
                        isOmniMode = false,
                    )
                    sdk.sendSessionChange("chat")
                } else {
                    _uiState.value = _uiState.value.copy(showLecturePicker = true, showLanguagePicker = false)
                }
            }
            is ConversationEvent.ConfirmLectureLanguage -> {
                stopRecordingIfActive()  // 非 chat 模式禁用录屏
                val deviceLanguage = java.util.Locale.getDefault().language
                listenerAdapter.setTranslateMode(true)
                listenerAdapter.setLectureMode(true)
                listenerAdapter.setTranscribeMode(false)
                listenerAdapter.setOmniMode(false)
                listenerAdapter.rememberModeRequest(
                    ModeChangeRequest("translate", deviceLanguage, event.languageCode, "lecture"),
                )
                listenerAdapter.markModeChangePending("translate")
                _uiState.value = _uiState.value.copy(
                    isTranslateMode = true,
                    isLectureMode = true,
                    isTranscribeMode = false,
                    showLecturePicker = false,
                    showAvatarSheet = false,
                    isOmniMode = false,
                )
                sdk.sendSessionChange("translate", deviceLanguage, event.languageCode, usecase = "lecture")
            }
            ConversationEvent.DismissLecturePicker -> {
                _uiState.value = _uiState.value.copy(showLecturePicker = false)
            }
            ConversationEvent.ToggleTranscribeMode -> {
                if (_uiState.value.isTranscribeMode) {
                    // 关闭：三 flag 全复位 + 发 chat（三向互斥纪律）
                    listenerAdapter.setTranslateMode(false)
                    listenerAdapter.setLectureMode(false)
                    listenerAdapter.setTranscribeMode(false)
                    listenerAdapter.setOmniMode(false)
                    listenerAdapter.rememberModeRequest(null)  // 回 chat，无需重连恢复
                    _uiState.value = _uiState.value.copy(
                        isTranslateMode = false, isLectureMode = false, isTranscribeMode = false,
                        isOmniMode = false,
                    )
                    sdk.sendSessionChange("chat")
                } else {
                    // 开启 → 先弹「选择现场录音模式」对话框（§6.4）
                    _uiState.value = _uiState.value.copy(showTranscribeSourcePicker = true)
                }
            }
            is ConversationEvent.ConfirmTranscribeSource -> {
                stopRecordingIfActive()  // 非 chat 模式禁用录屏
                // 选择写入 UiState；采集源由派生观测 collector 按
                // (isTranscribeMode, transcribeAudioSource) 下发（docs/android_audio_capture.md §3.3）
                listenerAdapter.setTranslateMode(false)
                listenerAdapter.setLectureMode(false)
                listenerAdapter.setTranscribeMode(true)
                listenerAdapter.setOmniMode(false)
                listenerAdapter.rememberModeRequest(ModeChangeRequest("transcribe"))
                _uiState.value = _uiState.value.copy(
                    isTranslateMode = false,
                    isLectureMode = false,
                    isTranscribeMode = true,
                    transcribeAudioSource = event.source,
                    showTranscribeSourcePicker = false,
                    showAvatarSheet = false,
                    isOmniMode = false,
                )
                // §6.9：回声前收到 1009（session.change 被拒专属码）→ adapter 回落开关
                listenerAdapter.markModeChangePending("transcribe")
                sdk.sendSessionChange("transcribe")
            }
            ConversationEvent.DismissTranscribeSourcePicker -> {
                _uiState.value = _uiState.value.copy(showTranscribeSourcePicker = false)
            }
            ConversationEvent.ToggleOmniMode -> {
                if (_uiState.value.isOmniMode) {
                    // 关闭：四 flag 全复位 + 发 chat（四向互斥纪律）
                    listenerAdapter.setTranslateMode(false)
                    listenerAdapter.setLectureMode(false)
                    listenerAdapter.setTranscribeMode(false)
                    listenerAdapter.setOmniMode(false)
                    listenerAdapter.rememberModeRequest(null)  // 回 chat，无需重连恢复
                    _uiState.value = _uiState.value.copy(
                        isTranslateMode = false, isLectureMode = false, isTranscribeMode = false,
                        isOmniMode = false,
                    )
                    sdk.sendSessionChange("chat")
                } else {
                    // 开启 → 先弹「使用全模态AI模型，可以降低交互延迟」说明弹窗，确认后才切换
                    _uiState.value = _uiState.value.copy(showOmniIntroDialog = true)
                }
            }
            ConversationEvent.ConfirmOmniMode -> {
                stopRecordingIfActive()  // 非 chat 模式禁用录屏
                listenerAdapter.setTranslateMode(false)
                listenerAdapter.setLectureMode(false)
                listenerAdapter.setTranscribeMode(false)
                listenerAdapter.setOmniMode(true)
                listenerAdapter.rememberModeRequest(ModeChangeRequest("omni"))
                listenerAdapter.markModeChangePending("omni")
                _uiState.value = _uiState.value.copy(
                    isTranslateMode = false,
                    isLectureMode = false,
                    isTranscribeMode = false,
                    isOmniMode = true,
                    showOmniIntroDialog = false,
                    showAvatarSheet = false,
                )
                sdk.sendSessionChange("omni")
            }
            ConversationEvent.DismissOmniDialog -> {
                _uiState.value = _uiState.value.copy(showOmniIntroDialog = false)
            }
            ConversationEvent.ToggleJobStatusPanel -> {
                val cur = _uiState.value
                val opening = !cur.showJobStatusPanel && cur.agentJobs.isNotEmpty()
                // 关闭面板与 DismissJobStatusPanel 同口径：未 inFlight 的确认态清掉；
                // 在飞请求保留状态，重开面板继续显示进度
                val cancel = cur.agentJobCancel
                _uiState.value = cur.copy(
                    showJobStatusPanel = opening,
                    agentJobCancel = when {
                        opening -> cancel
                        cancel?.inFlight == true -> cancel
                        else -> null
                    },
                )
            }
            ConversationEvent.DismissJobStatusPanel -> {
                // 未 inFlight 的确认态随面板关闭清掉；在飞请求保留状态，重开面板继续显示进度
                val cancel = _uiState.value.agentJobCancel
                _uiState.value = _uiState.value.copy(
                    showJobStatusPanel = false,
                    agentJobCancel = if (cancel?.inFlight == true) cancel else null,
                )
            }
            is ConversationEvent.RequestAgentJobCancel -> {
                if (_uiState.value.agentJobCancel?.inFlight != true) {
                    _uiState.value = _uiState.value.copy(
                        agentJobCancel = AgentJobCancelUiState(job = event.job),
                    )
                }
            }
            ConversationEvent.DismissAgentJobCancel -> {
                if (_uiState.value.agentJobCancel?.inFlight != true) {
                    _uiState.value = _uiState.value.copy(agentJobCancel = null)
                }
            }
            ConversationEvent.ConfirmAgentJobCancel -> confirmCancelAgentJob()
            is ConversationEvent.OpenImageViewer -> {
                clearSaveTip()   // 2s 内重开查看器不得看到上一轮的陈旧提示
                _uiState.value = _uiState.value.copy(imageViewerMessageId = event.messageId)
            }
            ConversationEvent.CloseImageViewer ->
                _uiState.value = _uiState.value.copy(imageViewerMessageId = null)
            is ConversationEvent.SaveViewerImage -> saveViewerImage(event.messageId)
            ConversationEvent.SaveViewerImagePermissionDenied ->
                showTipWithTimeout(appContext.getString(R.string.common_save_failed_storage))
            is ConversationEvent.OpenAgentJobResult ->
                _uiState.value = _uiState.value.copy(
                    agentJobResultViewId = event.messageId,
                    agentJobResultInitialCharOffset = event.charOffset,
                )
            ConversationEvent.CloseAgentJobResult ->
                _uiState.value = _uiState.value.copy(
                    agentJobResultViewId = null,
                    agentJobResultInitialCharOffset = null,
                )
        }
    }

    fun setAvatarCropSourceUri(uri: Uri) {
        _uiState.value = _uiState.value.copy(avatarCropSourceUri = uri)
    }

    /** 系统相机内取消拍照（TakePicture success=false）→ 清理已创建的相机临时文件。 */
    fun discardCameraSource(uri: Uri) {
        viewModelScope.launch { avatarRepository.deleteCameraSourceIfOwned(uri) }
    }

    fun onCropConfirmed(uri: Uri) {
        val source = _uiState.value.avatarCropSourceUri
        saveCustomAvatar(uri)
        _uiState.value = _uiState.value.copy(showAvatarCrop = false, avatarCropSourceUri = null)
        source?.let { viewModelScope.launch { avatarRepository.deleteCameraSourceIfOwned(it) } }
    }

    private fun saveCustomAvatar(uri: Uri) {
        viewModelScope.launch {
            try {
                val savedUri = avatarRepository.saveAvatarFromUri(uri)
                _uiState.value = _uiState.value.copy(
                    customAvatarUri = savedUri,
                    showAvatarSheet = false,
                )
                avatarRepository.cleanupCropTemp()
            } catch (e: Exception) {
                _uiState.value = _uiState.value.copy(
                    error = ConversationError.Unknown(appContext.getString(R.string.conv_avatar_save_failed, e.message)),
                )
            }
        }
    }

    private fun removeCustomAvatar() {
        viewModelScope.launch {
            avatarRepository.removeAvatar()
            _uiState.value = _uiState.value.copy(
                customAvatarUri = null,
                showAvatarSheet = false,
            )
        }
    }

    /**
     * 行内确认「是」：发起 BFF 取消（docs/android_agent_job_status.md §5.3）。
     * 404/409 视为已结束/不存在（静默清态，交给 job_status 快照）；其它失败保留确认态。
     * 所有回写都校验 jobId，防止目标被快照清掉后陈旧响应覆盖新状态。
     */
    private fun confirmCancelAgentJob() {
        val target = _uiState.value.agentJobCancel ?: return
        if (target.inFlight) return
        val jobId = target.job.jobId
        _uiState.value = _uiState.value.copy(
            agentJobCancel = target.copy(inFlight = true, error = null),
        )
        viewModelScope.launch {
            try {
                agentJobRepository.cancel(jobId)
                if (_uiState.value.agentJobCancel?.job?.jobId == jobId) {
                    _uiState.value = _uiState.value.copy(agentJobCancel = null)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: MylifeApiException) {
                if (_uiState.value.agentJobCancel?.job?.jobId != jobId) return@launch
                if ((e.status == 404 && e.code == "not_found") ||
                    (e.status == 409 && e.code == "conflict")
                ) {
                    _uiState.value = _uiState.value.copy(agentJobCancel = null)
                } else {
                    _uiState.value = _uiState.value.copy(
                        agentJobCancel = target.copy(
                            inFlight = false,
                            error = appContext.getString(R.string.conv_agent_job_cancel_failed),
                        ),
                    )
                }
            } catch (e: Exception) {
                if (_uiState.value.agentJobCancel?.job?.jobId != jobId) return@launch
                _uiState.value = _uiState.value.copy(
                    agentJobCancel = target.copy(
                        inFlight = false,
                        error = appContext.getString(R.string.conv_agent_job_cancel_failed),
                    ),
                )
            }
        }
    }

    /** 保存查看器当前页图片：未命中/无图 no-op；进行中忽略再次点击（防连点重复落库）。 */
    private fun saveViewerImage(messageId: String) {
        if (saveInFlight) return
        val image = _uiState.value.messages.firstOrNull { it.id == messageId }?.image ?: return
        saveInFlight = true
        viewModelScope.launch {
            try {
                // runCatching 把跨类契约（saver 失败返回 false、不抛）本地化：未来 saver 违约
                // 也收敛为「保存失败」提示，而不是沿 viewModelScope 上抛成崩溃
                val ok = runCatching { imageGallerySaver.save(image) }.getOrDefault(false)
                showTipWithTimeout(appContext.getString(if (ok) R.string.common_saved_to_gallery else R.string.common_save_failed))
            } finally {
                // 防未来改动把 flag 卡死、保存入口永久静默失效
                saveInFlight = false
            }
        }
    }

    /**
     * 统一提示 TTL：先取消上一个定时器再展示 2s。「文案比对」防不了顺序保存串台——
     * 保存 A 成功后 0.5s 保存 B 成功（文案相同），A 的延迟协程到点会提前清掉 B 的提示。
     */
    private fun showTipWithTimeout(text: String) {
        tipJob?.cancel()
        tipJob = viewModelScope.launch {
            _uiState.value = _uiState.value.copy(imageSaveTip = text)
            delay(TIP_TTL_MS)
            _uiState.value = _uiState.value.copy(imageSaveTip = null)
        }
    }

    /** 清空保存提示并取消其定时器（重开查看器/换会话时调用）。 */
    private fun clearSaveTip() {
        tipJob?.cancel()
        tipJob = null
        _uiState.value = _uiState.value.copy(imageSaveTip = null)
    }

    fun unbindCamera() {
        cameraController.unbind()
    }

    fun bindCamera(lifecycleOwner: LifecycleOwner, previewView: PreviewView) {
        cameraLifecycle = lifecycleOwner
        cameraPreviewView = previewView
        cameraController.bind(lifecycleOwner, previewView, desiredFrontCamera) {
            // 绑定失败（权限缺失/撤销/autoCameraOnJoin 路径）→ 复位相机 UI，
            // 避免黑屏预览残留，并经错误通道给用户可见反馈
            cameraLifecycle = null
            cameraPreviewView = null
            _uiState.value = _uiState.value.copy(
                isCameraOn = false,
                videoVisibility = VideoVisibility.HIDDEN,
                error = ConversationError.Unknown(appContext.getString(R.string.conv_camera_start_failed)),
            )
        }
    }

    /** 非 chat 模式（translate/lecture/transcribe/omni）一律禁止录屏；进入这些模式时强制停录。 */
    private fun stopRecordingIfActive() {
        if (screenCaptureController.isRecording.value) {
            sdk.logDebug("ConversationViewModel", "mode enter: stop active screen recording")
            screenCaptureController.stop()
        }
        _uiState.value = _uiState.value.copy(showStopRecordingConfirm = false)
    }

    private fun toggleScreenRecording() {
        // 仅 chat 模式可录屏（按钮仍显示，此处为兜底：非 chat 点击/授权回调晚到等窗口）
        if (!_uiState.value.canScreenRecord) {
            sdk.logDebug("ConversationViewModel", "recording: rejected in non-chat mode")
            _uiState.value = _uiState.value.copy(
                error = ConversationError.Unknown(appContext.getString(R.string.conv_screen_capture_unsupported)),
            )
            return
        }
        if (screenCaptureController.isRecording.value) {
            // 录制中点击：不直接停（防误触打断屏幕 AI 交互），先弹停止确认框
            sdk.logDebug("ConversationViewModel", "recording: request stop confirm")
            _uiState.value = _uiState.value.copy(showStopRecordingConfirm = true)
            return
        }
        // 互斥（D9）：相机开着 → 先自动关相机（镜像 toggleCamera 的关闭路径）
        val cameraWasOn = _uiState.value.isCameraOn
        if (cameraWasOn) {
            sdk.logDebug("ConversationViewModel", "recording: camera on, unbind first")
            cameraController.unbind()
            _uiState.value = _uiState.value.copy(
                isCameraOn = false,
                videoVisibility = VideoVisibility.HIDDEN,
            )
            cameraLifecycle = null
            cameraPreviewView = null
        }
        if (prefsState.value.screenRecordRationaleShown && overlayPermissions.canDraw()) {
            // 非首次且权限齐备：直接进系统 MediaProjection 授权框
            sdk.logDebug("ConversationViewModel", "recording: request consent")
            _uiState.value = _uiState.value.copy(pendingScreenCaptureLaunch = true)
        } else {
            // 首次（D10）或悬浮窗权限缺失（§5.3）：先展示说明弹窗，解释用途后再跳转
            sdk.logDebug("ConversationViewModel", "recording: show rationale (first time / no overlay)")
            _uiState.value = _uiState.value.copy(showScreenRecordRationale = true)
        }
    }

    /** Screen 收到 consent ActivityResult（RESULT_OK）后转发给采集控制器。 */
    fun startScreenCapture(resultCode: Int, data: Intent) {
        // 二次判定（仅 chat 可录）：否则"进入非 chat 模式前已确认说明、授权回调晚到"的窗口
        // 会绕过 toggleScreenRecording 的门控直接开录
        if (!_uiState.value.canScreenRecord) {
            sdk.logDebug("ConversationViewModel", "screen capture start rejected in non-chat mode")
            _uiState.value = _uiState.value.copy(
                error = ConversationError.Unknown(appContext.getString(R.string.conv_screen_capture_unsupported)),
            )
            return
        }
        screenCaptureController.start(resultCode, data)
    }

    private fun toggleCamera() {
        // 互斥（D9）：录制中开相机 → 先自动停录制
        if (screenCaptureController.isRecording.value) {
            sdk.logDebug("ConversationViewModel", "camera: stop recording first (mutex)")
            screenCaptureController.stop()
        }
        val wasOn = _uiState.value.isCameraOn
        if (wasOn) {
            cameraController.unbind()
            _uiState.value = _uiState.value.copy(
                isCameraOn = false,
                videoVisibility = VideoVisibility.HIDDEN,
            )
            cameraLifecycle = null
            cameraPreviewView = null
        } else {
            desiredFrontCamera = defaultFrontCameraSetting
            // 新采集轮（docs/fd_pipeline_video_process.md §8）：用户开相机 → 首帧直起轮
            turnCounter.next()
            _uiState.value = _uiState.value.copy(
                isCameraOn = true,
                videoVisibility = VideoVisibility.VISIBLE,
            )
        }
    }

    /** 图库照片上行（docs/camera_enhance.md §9）：FAILED 才提示；REJECTED 是相机竞态静默丢弃。 */
    private fun sendGalleryPhoto(uri: Uri) {
        viewModelScope.launch {
            when (
                galleryPhotoSender.send(
                    uri = uri,
                    omniMode = _uiState.value.isOmniMode,
                    isStillAllowed = { !_uiState.value.isCameraOn },
                )
            ) {
                GallerySendResult.SENT, GallerySendResult.REJECTED -> Unit
                GallerySendResult.FAILED -> _uiState.value = _uiState.value.copy(
                    error = ConversationError.Unknown(appContext.getString(R.string.cap_bubble_failed)),
                )
            }
        }
    }

    private fun switchCamera() {
        // Flip the flag; actual CameraX rebind is driven by
        // cameraResetCount → new VideoPreview → LaunchedEffect → bindCamera().
        desiredFrontCamera = !desiredFrontCamera
        // 新采集轮（docs/fd_pipeline_video_process.md §8.2.2 P2-1）：递增已下沉到 CameraController
        // 的"切换后首帧 facing 变化检测"——这里只翻转期望镜头、不递增：tap→重绑窗口
        // （重组异步驱动）内旧镜头帧若携带了新 id，会被服务端在旧画面上触发新视野评论
    }

    private fun sendChipMessage(text: String) {
        val sessionId = coordinator.currentSessionId.value ?: return
        viewModelScope.launch {
            settingsRepository.setFirstLaunchComplete()
        }
        listenerAdapter.onTranscription(text, true, "chip-${System.currentTimeMillis()}")
    }

    override fun onCleared() {
        super.onCleared()
        // D11：VM 销毁联动停录，防止后台无限采样推入死管道
        screenCaptureController.stop()
        coordinator.stop()
    }

    private companion object {
        const val TIP_TTL_MS = 2_000L
    }
}
