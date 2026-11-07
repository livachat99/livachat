package dev.livachat.app.ui.screens.conversation

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.ContextCompat
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Subtitles
import androidx.compose.material.icons.filled.Translate
import androidx.compose.material.icons.outlined.FiberManualRecord
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.flow.first
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.LocalLifecycleOwner
import dev.livachat.app.R
import dev.livachat.app.domain.model.ConversationError
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.model.isAgentJobResult
import dev.livachat.app.ui.components.AgentJobListPanel
import dev.livachat.app.ui.components.AgentJobPanelTopOffset
import dev.livachat.app.ui.components.AgentJobStatusBadge
import dev.livachat.app.ui.components.AiAvatar
import dev.livachat.app.ui.components.AiGeneratedNoticeBanner
import dev.livachat.app.ui.components.AvatarState
import dev.livachat.app.ui.components.AvatarTitleBarButton
import dev.livachat.app.ui.components.BannerMessage
import dev.livachat.app.ui.components.BottomToolbar
import dev.livachat.app.ui.components.ErrorBanner
import dev.livachat.app.ui.components.IndicatorState
import dev.livachat.app.ui.components.toIndicatorState
import dev.livachat.app.ui.components.MessageBubble
import dev.livachat.app.ui.components.StatusIndicator
import dev.livachat.app.ui.components.TranscriptionBubble
import dev.livachat.app.ui.components.VideoPreview
import dev.livachat.app.ui.components.WelcomeGuide
import dev.livachat.app.ui.components.rememberBitmapFromUri
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Red
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.Surface
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary
import dev.livachat.sdk.model.SessionState
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConversationScreen(
    onNavigateToHistory: () -> Unit,
    onNavigateToSettings: () -> Unit,
    onOpenLink: (String) -> Unit,
    onNavigateToTimeMachine: () -> Unit,
    viewModel: ConversationViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val lifecycleOwner = LocalLifecycleOwner.current
    val context = LocalContext.current
    var cameraPreviewView by remember { mutableStateOf<PreviewView?>(null) }
    val barAvatarBitmap = rememberBitmapFromUri(uiState.customAvatarUri)

    // ── Avatar photo pickers ──────────────────────────────────────────
    val galleryLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri: Uri? ->
        if (uri != null) {
            viewModel.setAvatarCropSourceUri(uri)
            viewModel.onEvent(ConversationEvent.StartAvatarCrop)
        }
    }

    // 会话图库选图（docs/camera_enhance.md §10）：单图，先进入全屏预览；发送才处理/上行
    val photoGalleryLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.PickVisualMedia(),
    ) { uri: Uri? ->
        if (uri != null) {
            viewModel.onEvent(ConversationEvent.OpenGalleryPreview(uri))
        } else {
            // picker 内系统返回/取消：若当前有预览（从预览左返回打开），关闭预览回主屏幕
            viewModel.onEvent(ConversationEvent.DismissGalleryPreview)
        }
    }

    val cameraUri = rememberSaveable { mutableStateOf<Uri?>(null) }
    val cameraLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.TakePicture(),
    ) { success: Boolean ->
        if (success) {
            cameraUri.value?.let { uri ->
                viewModel.setAvatarCropSourceUri(uri)
                viewModel.onEvent(ConversationEvent.StartAvatarCrop)
            }
        } else {
            // 系统相机内取消：清理已创建的相机临时文件（0 字节/半截）
            cameraUri.value?.let { viewModel.discardCameraSource(it) }
        }
    }

    // 拍照（系统相机 IMAGE_CAPTURE）要求调用方持有 CAMERA 权限——缺失时部分 OEM
    // 直接抛 SecurityException 崩溃（"with revoked permission android.permission.CAMERA"）；
    // 未授权先请求，拒绝/异常时弹系统设置引导而非裸崩。另兜底 ActivityNotFoundException
    //（设备无相机应用，如模拟器）。
    val showCameraPermissionGuide = remember { mutableStateOf(false) }

    // 构建拍照临时文件并启动系统相机；launch 失败（权限外系统拒绝/无相机应用）
    // 收敛到引导弹窗，不裸崩
    fun launchAvatarPhotoCapture() {
        val photoDir = File(context.cacheDir, "avatar_photos")
        photoDir.mkdirs()
        val photoFile = File(photoDir, "camera_${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            photoFile,
        )
        cameraUri.value = uri
        try {
            cameraLauncher.launch(uri)
        } catch (e: Exception) {
            showCameraPermissionGuide.value = true
        }
    }

    val cameraPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            launchAvatarPhotoCapture()
        } else {
            showCameraPermissionGuide.value = true
        }
    }

    // 视频摄像头开关：CAMERA 未授权时先请求系统授权再开相机——
    // 无权限直接绑定 CameraX 只会得到黑屏预览/异常
    val videoCameraPermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            viewModel.onEvent(ConversationEvent.ToggleCamera)
        } else {
            showCameraPermissionGuide.value = true
        }
    }

    fun toggleVideoCamera() {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.onEvent(ConversationEvent.ToggleCamera)
        } else {
            videoCameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }
    }

    // ── 查看器保存到相册：权限懒请求（仅 API ≤ 28）────────────────────
    // 入口（启动/进会话/开查看器）不检查不申请，仅点击保存时按需申请一次。
    // rememberSaveable（非裸 remember）：权限框在飞期间旋转重建后，回调仍能拿到暂存 id
    var pendingSaveMessageId by rememberSaveable { mutableStateOf<String?>(null) }
    val saveImagePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val messageId = pendingSaveMessageId
        pendingSaveMessageId = null   // 两条路径都清空：否则在飞守卫把保存入口永久锁死
        if (messageId != null) {
            if (granted) {
                viewModel.onEvent(ConversationEvent.SaveViewerImage(messageId))
            } else {
                // 拒绝只提示（不做"去设置"引导）；id 已清空，可再次点击重试
                viewModel.onEvent(ConversationEvent.SaveViewerImagePermissionDenied)
            }
        }
    }

    fun handleSaveViewerImage(messageId: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.onEvent(ConversationEvent.SaveViewerImage(messageId))
        } else if (pendingSaveMessageId == null) {
            // 弹窗在飞：忽略期间点击（对在飞请求重复 launch 同一 launcher 的行为不值得依赖）
            pendingSaveMessageId = messageId
            saveImagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }

    // ── Screen recording: MediaProjection system consent ────────────
    // 授权框每次开录都弹（targetSdk 34 单次授权语义，docs/android_screen_recording.md §3），
    // 无 runtime permission 流程，直接经 StartActivityForResult 发起系统弹窗。
    val projectionManager = remember {
        context.getSystemService(MediaProjectionManager::class.java)
    }
    val screenCaptureLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val data = result.data
        if (result.resultCode == Activity.RESULT_OK && data != null) {
            viewModel.startScreenCapture(result.resultCode, data)
        } else {
            // 用户取消/拒绝：默认静默复位
            viewModel.onEvent(ConversationEvent.ScreenCaptureConsentDenied)
        }
    }

    // 消费 VM 的一次性启动信号并发起系统授权
    LaunchedEffect(uiState.pendingScreenCaptureLaunch) {
        if (uiState.pendingScreenCaptureLaunch) {
            viewModel.onEvent(ConversationEvent.ConsumeScreenCaptureLaunch)
            screenCaptureLauncher.launch(projectionManager.createScreenCaptureIntent())
        }
    }

    // ── Screen recording v2: 悬浮窗权限（SYSTEM_ALERT_WINDOW 特殊权限）──
    // Screen 只负责"跳设置页 + 回报返回"，判定唯一在 VM/OverlayPermissions（§5.3）
    val overlayLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { viewModel.onEvent(ConversationEvent.OverlayPermissionReturned) }

    LaunchedEffect(uiState.pendingOverlayPermissionLaunch) {
        if (uiState.pendingOverlayPermissionLaunch) {
            viewModel.onEvent(ConversationEvent.ConsumeOverlayPermissionLaunch)
            val pkg = Uri.parse("package:${context.packageName}")
            val primary = Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, pkg)
            val fallback = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, pkg)
            // resolveActivity 受包可见性限制不可靠：直接试启动，ActivityNotFound 再退兜底
            val launched = runCatching { overlayLauncher.launch(primary) }.isSuccess ||
                runCatching { overlayLauncher.launch(fallback) }.isSuccess
            if (!launched) {
                // 两个设置页都打不开：走返回复查（仍 false → ErrorBanner 说明原因）
                viewModel.onEvent(ConversationEvent.OverlayPermissionReturned)
            }
        }
    }

    // Camera reset counter: increments every time camera turns on or user
    // switches front/back camera. Used as key for remember blocks inside
    // the VISIBLE branch so overlay state (AI text, transcription) resets.
    var prevCameraOn by remember { mutableStateOf(false) }
    var cameraResetCount by remember { mutableStateOf(0) }
    if (uiState.isCameraOn && !prevCameraOn) {
        cameraResetCount++
    }
    prevCameraOn = uiState.isCameraOn

    LaunchedEffect(Unit) {
        viewModel.onEvent(ConversationEvent.OnScreenStart)
    }

    // Unbind camera from PreviewView when leaving composition (navigation forward).
    // The ViewModel preserves isCameraOn state so camera rebinds on return via
    // the LaunchedEffect(cameraPreviewView) below.
    DisposableEffect(Unit) {
        onDispose {
            viewModel.unbindCamera()
        }
    }

    // Clear preview view reference when camera is hidden, bind when visible
    LaunchedEffect(uiState.videoVisibility) {
        if (uiState.videoVisibility != VideoVisibility.VISIBLE) {
            cameraPreviewView = null
        }
    }

    LaunchedEffect(cameraPreviewView) {
        val pv = cameraPreviewView
        if (uiState.videoVisibility == VideoVisibility.VISIBLE && pv != null) {
            viewModel.bindCamera(lifecycleOwner, pv)
        }
    }

    // ── Gallery photo preview (full-screen overlay, docs/camera_enhance.md §10) ──
    val previewUri = uiState.galleryPreviewUri
    if (previewUri != null) {
        GalleryPhotoPreviewScreen(
            uri = previewUri,
            onSend = { viewModel.onEvent(ConversationEvent.SendGalleryPhoto(previewUri)) },
            onCancel = { viewModel.onEvent(ConversationEvent.DismissGalleryPreview) },
            // 左返回/系统返回：先关闭预览（主屏为底），再拉起同一个单图 picker；
            // picker 取消/系统返回 → 主屏幕（不会闪回旧预览）；选新图 → 打开新预览
            onBackToPicker = {
                viewModel.onEvent(ConversationEvent.DismissGalleryPreview)
                photoGalleryLauncher.launch(
                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                )
            },
        )
        return
    }

    // ── Avatar crop screen (full-screen overlay) ────────────────────
    val cropSourceUri = uiState.avatarCropSourceUri
    if (uiState.showAvatarCrop && cropSourceUri != null) {
        AvatarCropScreen(
            sourceUri = cropSourceUri,
            onConfirm = { croppedUri -> viewModel.onCropConfirmed(croppedUri) },
            onCancel = { viewModel.onEvent(ConversationEvent.CancelAvatarCrop) },
        )
        return
    }

    BoxWithConstraints(modifier = Modifier.fillMaxSize().background(Background)) {
        // R5：面板上限 = 根高度 − 顶部锚点 − 实测工具栏高度 − 24dp 间距；
        // 工具栏尚未测量（首帧/旋转重建）时用 140dp 保守 reserve（工具栏≈104dp + 24dp
        // 间距，再加字体缩放余量），避免压住工具栏。
        var toolbarHeightPx by remember { mutableIntStateOf(0) }
        val toolbarHeightDp = with(LocalDensity.current) { toolbarHeightPx.toDp() }
        val bottomReserve = if (toolbarHeightPx == 0) 140.dp else toolbarHeightDp + Spacing.lg
        // edge-to-edge 后窗口从屏幕顶部开始；Popup 偏移需补状态栏高度，保持原视觉位置。
        val statusBarTop = with(LocalDensity.current) {
            WindowInsets.statusBars.getTop(this).toDp()
        }
        val panelMaxHeight = (maxHeight - AgentJobPanelTopOffset - statusBarTop - bottomReserve)
            .coerceAtLeast(0.dp)

        Column(modifier = Modifier.fillMaxSize()) {
            // Top app bar（外层 Box 承载隐藏态头像簇的固定居中覆盖层）
            Box {
                TopAppBar(
                    title = {
                        StatusIndicator(
                            state = mapSessionStateToIndicator(uiState),
                            showLabel = true,
                            onClick = if (uiState.connectionState == dev.livachat.sdk.model.ConnectionState.DISCONNECTED) {
                                { viewModel.onEvent(ConversationEvent.RetryConnection) }
                            } else null,
                        )
                    },
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = Background,
                        titleContentColor = TextPrimary,
                    ),
                    actions = {
                        // Record toggle：各模式均显示；非 chat 模式点击由 ViewModel 守卫弹提示
                        // 「当前模式下不支持屏幕交互」（进入非 chat 时已强制停录）
                        IconButton(onClick = { viewModel.onEvent(ConversationEvent.ToggleRecording) }) {
                            if (uiState.isRecording) {
                                Icon(
                                    painter = painterResource(R.drawable.ic_screen_capture_active),
                                    contentDescription = stringResource(R.string.conv_menu_stop_recording),
                                    tint = Red,
                                )
                            } else {
                                Icon(
                                    imageVector = Icons.Outlined.FiberManualRecord,
                                    contentDescription = stringResource(R.string.conv_menu_start_recording),
                                    tint = TextSecondary,
                                )
                            }
                        }
                        // More options menu
                        var menuExpanded by remember { mutableStateOf(false) }
                        IconButton(onClick = { menuExpanded = true }) {
                            Icon(
                                Icons.Filled.MoreVert,
                                contentDescription = stringResource(R.string.conv_menu_more),
                                tint = TextSecondary,
                            )
                        }
                        DropdownMenu(
                            expanded = menuExpanded,
                            onDismissRequest = { menuExpanded = false },
                        ) {
                            DropdownMenuItem(
                                text = { Text(stringResource(R.string.common_time_machine)) },
                                onClick = {
                                    menuExpanded = false
                                    onNavigateToTimeMachine()
                                },
                            )
                            DropdownMenuItem(
                                text = { Text(stringResource(R.string.conv_menu_chats)) },
                                onClick = {
                                    menuExpanded = false
                                    onNavigateToHistory()
                                },
                            )
                            DropdownMenuItem(
                                text = { Text(stringResource(R.string.conv_menu_settings)) },
                                onClick = {
                                    menuExpanded = false
                                    onNavigateToSettings()
                                },
                            )
                        }
                    },
                )

                // 隐藏态头像簇：固定居中覆盖层——头像锚定屏中线，不随状态文案/徽章出现而移动
                if (!uiState.avatarVisible) {
                    Row(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .offset(x = (AvatarBarJobSlot + Spacing.sm) / 2)
                            // edge-to-edge 后 Box 高度含状态栏；补状态栏内边距，使头像簇在
                            // TopAppBar 的 64dp 内容区内居中，而不是含状态栏的整框居中。
                            .statusBarsPadding(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AvatarTitleBarButton(
                            onClick = { viewModel.onEvent(ConversationEvent.ShowAvatarSheet) },
                            customAvatarBitmap = barAvatarBitmap,
                        ) {
                            ModeBadge(
                                translate = uiState.isTranslateMode,
                                transcribe = uiState.isTranscribeMode,
                                omni = uiState.isOmniMode,
                                modifier = Modifier.align(Alignment.BottomEnd),
                                size = 20.dp,
                                iconSize = 11.dp,
                            )
                        }
                        Spacer(Modifier.width(Spacing.sm))
                        Box(
                            modifier = Modifier.width(AvatarBarJobSlot),
                            contentAlignment = Alignment.CenterStart,
                        ) {
                            AgentJobStatusBadge(
                                count = uiState.agentJobs.size,
                                onClick = { viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel) },
                            )
                        }
                    }
                }
            }

            // Main content
            Box(modifier = Modifier.weight(1f)) {
                AnimatedContent(
                    targetState = uiState.videoVisibility,
                    label = "videoTransition",
                ) { visibility ->
                    when (visibility) {
                        VideoVisibility.VISIBLE -> key(cameraResetCount) {
                            // Fresh composition on every camera open / switch.
                            // All remember blocks below are brand new each time.
                            val initialAiId = remember {
                                uiState.messages
                                    .lastOrNull { it.role == MessageRole.AI }?.id
                            }

                            // ── AI text (same pattern as before) ──────────
                            val cameraAiText = if (initialAiId != null) {
                                val lastAi = uiState.messages
                                    .lastOrNull { it.role == MessageRole.AI }
                                if (lastAi != null && lastAi.id != initialAiId) lastAi.text
                                else ""
                            } else {
                                uiState.messages
                                    .lastOrNull { it.role == MessageRole.AI }?.text ?: ""
                            }

                            // ── Transcription ──────────────────────────
                            val initialUserId = remember {
                                uiState.messages
                                    .lastOrNull { it.role == MessageRole.USER }?.id
                            }

                            val cameraTranscription: String
                            val cameraTransFinal: Boolean
                            if (uiState.currentTranscription.isNotEmpty()) {
                                cameraTranscription = uiState.currentTranscription
                                cameraTransFinal = uiState.isTranscriptionFinal
                            } else if (initialUserId != null) {
                                val lastUser = uiState.messages
                                    .lastOrNull { it.role == MessageRole.USER }
                                if (lastUser != null && lastUser.id != initialUserId) {
                                    cameraTranscription = lastUser.text
                                    cameraTransFinal = true
                                } else {
                                    cameraTranscription = ""
                                    cameraTransFinal = false
                                }
                            } else {
                                val lastUser = uiState.messages
                                    .lastOrNull { it.role == MessageRole.USER }
                                cameraTranscription = lastUser?.text ?: ""
                                cameraTransFinal = true
                            }

                            VideoPreview(
                                isFrontCamera = uiState.isFrontCamera,
                                onSwitchCamera = {
                                    viewModel.onEvent(ConversationEvent.SwitchCamera)
                                    cameraResetCount++
                                },
                                onPreviewViewCreated = { pv -> cameraPreviewView = pv },
                                transcription = cameraTranscription,
                                isTranscriptionFinal = cameraTransFinal,
                                lastAiText = cameraAiText,
                            )
                        }
                        else -> {
                            // Voice-only mode: Avatar + messages
                            ConversationVoiceContent(
                                uiState = uiState,
                                viewModel = viewModel,
                                onOpenLink = onOpenLink,
                            )
                        }
                    }
                }
            }

            // Bottom toolbar
            BottomToolbar(
                modifier = Modifier
                    .onSizeChanged { toolbarHeightPx = it.height }
                    .navigationBarsPadding(),
                isMicOn = !uiState.isMicMuted,
                isSpeakerOn = !uiState.isSpeakerMuted,
                isCameraOn = uiState.isCameraOn,
                onToggleMic = { viewModel.onEvent(ConversationEvent.ToggleMic) },
                onToggleSpeaker = { viewModel.onEvent(ConversationEvent.ToggleSpeaker) },
                onToggleCamera = { toggleVideoCamera() },
                onPickGallery = {
                    photoGalleryLauncher.launch(
                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                    )
                },
                micLevel = viewModel.micLevel,
                // 多设备（白名单后 >1 台）才挂 ^ 指示并可点（docs/android_audio_input_devices.md §5.2）
                showMicDeviceSelector = uiState.micInputDevices.size > 1,
                onOpenMicDevicePicker = {
                    viewModel.onEvent(ConversationEvent.ShowMicDevicePicker)
                },
                // 输出侧同构（docs/android_audio_output_devices.md §5.2）
                showOutputDeviceSelector = uiState.outputDevices.size > 1,
                onOpenOutputDevicePicker = {
                    viewModel.onEvent(ConversationEvent.ShowOutputDevicePicker)
                },
            )
        }

        // 屏幕录制首次说明弹窗（D10：展示一次，之后直接进系统授权框）
        if (uiState.showScreenRecordRationale) {
            AlertDialog(
                onDismissRequest = {
                    viewModel.onEvent(ConversationEvent.DismissScreenRecordRationale)
                },
                title = {
                    Text(
                        stringResource(R.string.conv_screen_capture_rationale_title),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                text = {
                    Text(
                        stringResource(R.string.conv_screen_capture_rationale_text),
                    )
                },
                confirmButton = {
                    // 与停止确认框一致：按钮并排居中（单 confirmButton slot 内
                    // 全宽居中 Row 承载两按钮，M3 AlertDialog 无原生居中参数）
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        TextButton(
                            onClick = {
                                viewModel.onEvent(ConversationEvent.DismissScreenRecordRationale)
                            },
                        ) { Text(stringResource(R.string.common_cancel)) }
                        TextButton(
                            onClick = {
                                viewModel.onEvent(ConversationEvent.ConfirmScreenRecordRationale)
                            },
                        ) { Text(stringResource(R.string.conv_continue)) }
                    }
                },
            )
        }

        // 录制中点击顶栏按钮的停止确认弹窗（防误触立即停录打断屏幕 AI 交互）。
        // isRecording 守卫：系统侧停投屏/相机互斥等路径已停录时陈旧弹窗自动收起。
        // 标题居中 + 缩小字号；按钮并排居中（M3 AlertDialog 无按钮居中参数，
        // 以单 confirmButton slot 内全宽居中 Row 承载两个按钮实现）
        if (uiState.isRecording && uiState.showStopRecordingConfirm) {
            AlertDialog(
                onDismissRequest = {
                    viewModel.onEvent(ConversationEvent.DismissStopScreenRecord)
                },
                title = {
                    Text(
                        stringResource(R.string.conv_screen_capture_stop_title),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                text = {
                    Text(
                        stringResource(R.string.conv_screen_capture_stop_message),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                    )
                },
                confirmButton = {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        TextButton(
                            onClick = {
                                viewModel.onEvent(ConversationEvent.DismissStopScreenRecord)
                            },
                        ) { Text(stringResource(R.string.common_cancel)) }
                        TextButton(
                            onClick = {
                                viewModel.onEvent(ConversationEvent.ConfirmStopScreenRecord)
                            },
                        ) { Text(stringResource(R.string.common_stop)) }
                    }
                },
            )
        }

        // 顶部 banner：错误优先、AI 提示其次（对齐 iOS topOverlay 的堆叠顺序）
        Column(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(top = 60.dp),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            ErrorBanner(
                message = uiState.error?.let { convError ->
                    BannerMessage(
                        text = when (convError) {
                            is ConversationError.NetworkError -> convError.message
                            is ConversationError.ServerError -> convError.message
                            is ConversationError.Timeout -> stringResource(R.string.conv_conn_timeout)
                            is ConversationError.AudioError -> convError.message
                            is ConversationError.PermissionDenied ->
                                stringResource(R.string.conv_permission_denied_fmt, convError.permission)
                            is ConversationError.Unknown -> convError.message
                        },
                        recoverable = convError is ConversationError.NetworkError,
                    )
                },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissError) },
                onRetry = { viewModel.onEvent(ConversationEvent.RetryConnection) },
            )
            AiGeneratedNoticeBanner(
                visible = uiState.showAiGeneratedNotice,
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissAiGeneratedNotice) },
            )
        }

        // C4: floating translucent job list panel (window-level Popup)
        if (uiState.showJobStatusPanel && uiState.agentJobs.isNotEmpty()) {
            AgentJobListPanel(
                jobs = uiState.agentJobs,
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissJobStatusPanel) },
                cancelState = uiState.agentJobCancel,
                onRequestCancel = {
                    viewModel.onEvent(ConversationEvent.RequestAgentJobCancel(it))
                },
                onDismissCancel = {
                    viewModel.onEvent(ConversationEvent.DismissAgentJobCancel)
                },
                onConfirmCancel = {
                    viewModel.onEvent(ConversationEvent.ConfirmAgentJobCancel)
                },
                maxHeight = panelMaxHeight,
                topInset = statusBarTop,
            )
        }

        // Welcome guide for first session
        if (uiState.isFirstSession && uiState.messages.isEmpty()) {
            WelcomeGuide(
                onChipClick = { text ->
                    viewModel.onEvent(ConversationEvent.SendChipMessage(text))
                },
                onDismiss = {},
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .navigationBarsPadding()
                    .padding(bottom = 100.dp, start = Spacing.md, end = Spacing.md),
            )
        }

        // Mic input device picker（docs/android_audio_input_devices.md §5.3）
        if (uiState.showMicDevicePicker) {
            MicDevicePicker(
                devices = uiState.micInputDevices,
                selectedDeviceId = uiState.selectedMicInputDeviceId,
                onSelect = { viewModel.onEvent(ConversationEvent.SelectMicInputDevice(it)) },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissMicDevicePicker) },
            )
        }

        // Output device picker（docs/android_audio_output_devices.md §5.3）
        if (uiState.showOutputDevicePicker) {
            OutputDevicePicker(
                devices = uiState.outputDevices,
                selectedDeviceId = uiState.selectedOutputDeviceId,
                onSelect = { viewModel.onEvent(ConversationEvent.SelectOutputDevice(it)) },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissOutputDevicePicker) },
            )
        }

        // Avatar customization bottom sheet
        if (uiState.showAvatarSheet) {
            AvatarCustomizationSheet(
                hasCustomAvatar = uiState.customAvatarUri != null,
                onPickFromGallery = {
                    galleryLauncher.launch(
                        PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly),
                    )
                },
                onTakePhoto = {
                    if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA)
                        == PackageManager.PERMISSION_GRANTED
                    ) {
                        launchAvatarPhotoCapture()
                    } else {
                        cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
                    }
                },
                onRemoveCustomAvatar = {
                    viewModel.onEvent(ConversationEvent.RemoveCustomAvatar)
                },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissAvatarSheet) },
                avatarVisible = uiState.avatarVisible,
                onAvatarVisibleChange = { viewModel.onEvent(ConversationEvent.SetAvatarVisible(it)) },
                conversationSceneEnabled = (uiState.isTranslateMode && !uiState.isLectureMode) || uiState.showLanguagePicker,
                onToggleConversationScene = { viewModel.onEvent(ConversationEvent.ToggleTranslateMode) },
                lectureModeEnabled = uiState.isLectureMode || uiState.showLecturePicker,
                onToggleLectureMode = { viewModel.onEvent(ConversationEvent.ToggleLectureMode) },
                // picker 打开期间开关保持 on 态（对齐 showLanguagePicker 既有模式）
                transcribeModeEnabled = uiState.isTranscribeMode || uiState.showTranscribeSourcePicker,
                onToggleTranscribeMode = { viewModel.onEvent(ConversationEvent.ToggleTranscribeMode) },
                // 弹窗打开期间开关保持 on 态（对齐 showLanguagePicker 既有模式）
                omniModeEnabled = uiState.isOmniMode || uiState.showOmniIntroDialog,
                onToggleOmniMode = { viewModel.onEvent(ConversationEvent.ToggleOmniMode) },
            )
        }

        // 全模态说明弹窗（选中"全模态"后展示；确认才发 session.change，取消则开关回落）
        if (uiState.showOmniIntroDialog) {
            AlertDialog(
                onDismissRequest = { viewModel.onEvent(ConversationEvent.DismissOmniDialog) },
                title = {
                    Text(
                        stringResource(R.string.conv_omni),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                text = {
                    Text(
                        stringResource(R.string.conv_omni_intro),
                        textAlign = TextAlign.Center,
                    )
                },
                confirmButton = {
                    // 与其它弹窗一致：按钮并排居中（单 slot 内全宽居中 Row）
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        TextButton(
                            onClick = { viewModel.onEvent(ConversationEvent.DismissOmniDialog) },
                        ) { Text(stringResource(R.string.common_cancel)) }
                        TextButton(
                            onClick = { viewModel.onEvent(ConversationEvent.ConfirmOmniMode) },
                        ) { Text(stringResource(R.string.conv_enable)) }
                    }
                },
            )
        }

        // 相机权限缺失/启动失败引导（头像"拍照"入口被拒时的兜底，避免 SecurityException 崩溃）
        if (showCameraPermissionGuide.value) {
            AlertDialog(
                onDismissRequest = { showCameraPermissionGuide.value = false },
                title = {
                    Text(
                        stringResource(R.string.conv_camera_unavailable),
                        modifier = Modifier.fillMaxWidth(),
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.titleMedium,
                    )
                },
                text = {
                    Text(
                        stringResource(R.string.conv_camera_permission_guide),
                        textAlign = TextAlign.Center,
                    )
                },
                confirmButton = {
                    // 与其它弹窗一致：按钮并排居中（单 slot 内全宽居中 Row）
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) {
                        TextButton(
                            onClick = { showCameraPermissionGuide.value = false },
                        ) { Text(stringResource(R.string.common_cancel)) }
                        TextButton(
                            onClick = {
                                showCameraPermissionGuide.value = false
                                context.startActivity(
                                    Intent(
                                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                        Uri.fromParts("package", context.packageName, null),
                                    ),
                                )
                            },
                        ) { Text(stringResource(R.string.common_go_to_settings)) }
                    }
                },
            )
        }

        // 服务器配置未齐备引导（docs/setting_server_address.md §3.4 三改）：运行首次提示一次，
        // 可外点/返回关闭；再次提示仅靠连接按钮
        if (uiState.showServerConfigPrompt) {
            ServerConfigRequiredDialog(
                onGoToSettings = {
                    viewModel.dismissServerConfigPrompt()
                    onNavigateToSettings()
                },
                onDismiss = viewModel::dismissServerConfigPrompt,
            )
        }

        // Language picker dialog for dialogue translate
        if (uiState.showLanguagePicker) {
            LanguagePickerSheet(
                title = stringResource(R.string.conv_select_peer_language),
                // 对话模式「自动识别」：以保留字 auto 原样下发，服务端进入 loc-auto 模式
                // （逐句 JSON 回传 source 语言并自适应翻译方向），与 lecture 的 auto→en 不同
                showAutoLanguage = true,
                onLanguageSelected = { code ->
                    viewModel.onEvent(ConversationEvent.ConfirmTranslateLanguage(code))
                },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissLanguagePicker) },
            )
        }

        // Language picker dialog for lecture translate
        if (uiState.showLecturePicker) {
            LanguagePickerSheet(
                title = stringResource(R.string.conv_select_speaker_language),
                // 听众模式支持「自动识别」：语义与选英语等同（归一为 en 下发，
                // 多模态模型自行识别演讲者语种），服务端/其余链路零改动
                showAutoLanguage = true,
                onLanguageSelected = { code ->
                    val normalized = if (code == AUTO_LANGUAGE_CODE) "en" else code
                    viewModel.onEvent(ConversationEvent.ConfirmLectureLanguage(normalized))
                },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissLecturePicker) },
            )
        }

        // Transcribe source picker dialog（原声/语音，Phase 1 仅记录选择）
        if (uiState.showTranscribeSourcePicker) {
            TranscribeSourcePicker(
                initialSource = uiState.transcribeAudioSource,
                onConfirm = { source ->
                    viewModel.onEvent(ConversationEvent.ConfirmTranscribeSource(source))
                },
                onDismiss = { viewModel.onEvent(ConversationEvent.DismissTranscribeSourcePicker) },
            )
        }

        // ── Full-screen image viewer（点击气泡图片进入）──
        val viewerId = uiState.imageViewerMessageId
        if (viewerId != null) {
            // 实时派生（过滤条件与气泡渲染条件逐字一致）；追加式列表，浏览期间新图仅尾部增长
            val viewerImages = uiState.messages.filter {
                it.image != null && it.role != MessageRole.USER
            }
            val initialPage = viewerImages.indexOfFirst { it.id == viewerId }
            if (initialPage >= 0) {
                ImageViewerOverlay(
                    images = viewerImages,
                    initialPage = initialPage,
                    onDismiss = { viewModel.onEvent(ConversationEvent.CloseImageViewer) },
                    saveTip = uiState.imageSaveTip,
                    onSaveClick = { messageId -> handleSaveViewerImage(messageId) },
                )
            } else {
                // 守卫：id 未命中带图消息（正常路径不应发生；会话切换已由 VM 清理）→ 自动关窗
                LaunchedEffect(viewerId) {
                    viewModel.onEvent(ConversationEvent.CloseImageViewer)
                }
            }
        }

        // ── Full-screen agent job result（点击 job 结果气泡进入，§4.2；身份解析与守卫在宿主内，§12.3.3）──
        AgentJobResultHost(
            viewId = uiState.agentJobResultViewId,
            initialCharOffset = uiState.agentJobResultInitialCharOffset,
            resolve = { id ->
                uiState.messages.firstOrNull { it.id == id }
                    ?.let { AgentJobPayload(it.text, it.timestamp) }
            },
            onDismiss = { viewModel.onEvent(ConversationEvent.CloseAgentJobResult) },
            onLinkClick = onOpenLink,
        )
    }
}

/**
 * 服务器配置未齐备引导弹窗（docs/setting_server_address.md §3.4 三改）：
 * 运行首次检测到配置不齐备时展示一次，可外点/返回关闭；此后仅点击 app bar
 * 连接按钮（DISCONNECTED 态状态指示器）再次弹起。单按钮「去设置」。
 * internal：仪器测试可直接渲染（androidTest 与 main 同包友元）。
 */
@Composable
internal fun ServerConfigRequiredDialog(
    onGoToSettings: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                stringResource(R.string.conv_server_config_required),
                modifier = Modifier.fillMaxWidth(),
                textAlign = TextAlign.Center,
                style = MaterialTheme.typography.titleMedium,
            )
        },
        text = {
            Text(
                stringResource(R.string.conv_server_config_hint),
                textAlign = TextAlign.Center,
            )
        },
        confirmButton = {
            Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                TextButton(onClick = onGoToSettings) { Text(stringResource(R.string.common_go_to_settings)) }
            }
        },
    )
}

@Composable
private fun ConversationVoiceContent(
    uiState: ConversationUiState,
    viewModel: ConversationViewModel,
    onOpenLink: (String) -> Unit,
) {
    val listState = rememberLazyListState()

    val avatarBitmap = rememberBitmapFromUri(uiState.customAvatarUri)

    // ── Auto-scroll logic ──────────────────────────────────────────
    // autoScrollEnabled tracks whether the user wants to stay at the bottom.
    // It's disabled when the user manually scrolls up, and re-enabled when
    // they scroll back down to the very end.

    var autoScrollEnabled by rememberSaveable { mutableStateOf(true) }
    var prevFirstVisible by rememberSaveable { mutableStateOf(-1) }

    // Observe scroll events to detect manual scroll-up vs programmatic scroll.
    LaunchedEffect(listState) {
        snapshotFlow {
            val scrolling = listState.isScrollInProgress
            val idx = listState.firstVisibleItemIndex
            val atEnd = !listState.canScrollForward
            Triple(scrolling, idx, atEnd)
        }.collect { (scrolling, firstIndex, atEnd) ->
            if (!scrolling) {
                if (prevFirstVisible >= 0 && firstIndex < prevFirstVisible) {
                    autoScrollEnabled = false
                }
                if (atEnd) {
                    autoScrollEnabled = true
                }
                prevFirstVisible = firstIndex
            }
        }
    }

    // Scroll to bottom when new content arrives, unless the user has
    // intentionally scrolled up to read history. The first positioning after
    // history loads jumps instantly; later incremental arrivals animate.
    // 首次触发时 LazyColumn 可能尚未布局（layoutInfo.totalItemsCount == 0，
    // LaunchedEffect 先于首帧 layout 运行），先等布局就绪再滚，避免错过首次滚底的时机窗口。
    var hasInitialScroll by rememberSaveable { mutableStateOf(false) }
    val lastMessageText = uiState.messages.lastOrNull()?.text.orEmpty()
    LaunchedEffect(uiState.messages.size, lastMessageText) {
        if (autoScrollEnabled) {
            snapshotFlow { listState.layoutInfo.totalItemsCount }
                .first { it > 0 }
            if (hasInitialScroll) {
                listState.animateScrollToItem(listState.layoutInfo.totalItemsCount)
            } else {
                listState.scrollToItem(listState.layoutInfo.totalItemsCount)
                hasInitialScroll = true
            }
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // AI Avatar with translate mode badge
            if (uiState.avatarVisible) {
                Box(modifier = Modifier.padding(top = Spacing.sm)) {
                    AiAvatar(
                        state = mapSessionToAvatarState(uiState),
                        customAvatarBitmap = avatarBitmap,
                        onClick = { viewModel.onEvent(ConversationEvent.ShowAvatarSheet) },
                    )

                    // C2: job status badge to the avatar's right. offset() takes the
                    // badge out of layout measurement, so the parent Box size — and
                    // therefore the avatar's position — stays pixel-identical (D3).
                    AgentJobStatusBadge(
                        count = uiState.agentJobs.size,
                        onClick = { viewModel.onEvent(ConversationEvent.ToggleJobStatusPanel) },
                        modifier = Modifier
                            .align(Alignment.CenterStart)
                            .offset(x = AvatarSize + Spacing.sm),
                    )

                    ModeBadge(
                        translate = uiState.isTranslateMode,
                        transcribe = uiState.isTranscribeMode,
                        omni = uiState.isOmniMode,
                        modifier = Modifier.align(Alignment.TopEnd),
                    )
                }
            }

            // Message list
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .padding(horizontal = Spacing.md),
                state = listState,
                // 查看器覆盖层不做全量手势消费（消费移动会破坏页内 tap 检测），纵向拖动会漏到
                // 本列表；打开查看器时禁用用户滚动，避免隐藏的列表被拖动（并误关自动滚底）
                userScrollEnabled = uiState.imageViewerMessageId == null &&
                    uiState.agentJobResultViewId == null,
            ) {
                items(uiState.messages, key = { it.id }) { message ->
                    MessageBubble(
                        message = message,
                        showCursor = !message.isFinal && uiState.sessionState == SessionState.SPEAKING,
                        onLinkClick = onOpenLink,
                        onImageClick = {
                            viewModel.onEvent(ConversationEvent.OpenImageViewer(message.id))
                        },
                        // 仅 agent job 结果气泡整卡可点（文档 §2.1 判定；其余消息零变化）；
                        // 携带点击处字符偏移 → 全屏按该处落位（§10.2）
                        onJobResultClick = if (message.isAgentJobResult()) {
                            { charOffset ->
                                viewModel.onEvent(
                                    ConversationEvent.OpenAgentJobResult(message.id, charOffset),
                                )
                            }
                        } else {
                            null
                        },
                    )
                }
            }
        }

        // Transcription overlay — fixed at bottom, doesn't shift the LazyColumn.
        // Bubble lifetime is tied to transcription only: disappears when transcription is final.
        // In lecture mode, the streaming AI translation text is shown as a second line.
        if (uiState.currentTranscription.isNotEmpty()) {
            TranscriptionBubble(
                text = uiState.currentTranscription,
                isFinal = uiState.isTranscriptionFinal,
                aiText = if (uiState.isLectureMode) uiState.streamingAiText.takeIf { it.isNotEmpty() } else null,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(bottom = 8.dp),
            )
        }
    }
}

// Hardcoded inside AiAvatar (AiAvatar.kt:96); keep in sync until AiAvatar
// exposes a size parameter.
private val AvatarSize = 120.dp

/** 隐藏态头像簇：job 徽章固定预留槽宽（出现/消失不移动头像）。 */
private val AvatarBarJobSlot = 48.dp

/** 模式徽章：大头像 `TopEnd`（26dp）与隐藏态标题栏头像按钮覆盖槽（20dp）两处复用。 */
@Composable
private fun ModeBadge(
    translate: Boolean,
    transcribe: Boolean,
    omni: Boolean,
    modifier: Modifier = Modifier,
    size: Dp = 26.dp,
    iconSize: Dp = 14.dp,
) {
    val badge = when {
        translate -> Icons.Filled.Translate to stringResource(R.string.conv_mode_translate)
        transcribe -> Icons.Filled.Subtitles to stringResource(R.string.conv_mode_transcribe)
        omni -> Icons.Filled.AutoAwesome to stringResource(R.string.conv_mode_omni)
        else -> return
    }
    Box(
        modifier = modifier
            .size(size)
            .background(color = BrandIndigo.copy(alpha = 0.7f), shape = CircleShape)
            .border(1.5.dp, Surface, CircleShape),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            imageVector = badge.first,
            contentDescription = badge.second,
            tint = TextPrimary,
            modifier = Modifier.size(iconSize),
        )
    }
}

// 断连终态压过 sessionState：会话态由服务端帧驱动，断连后冻结在最后一个 status
// （如 THINKING），不能继续冒充思考中。
// RECONNECTING 不覆盖：重连成功即由服务端 status 自愈，避免闪断折返（D2）。
internal fun mapSessionToAvatarState(uiState: ConversationUiState): AvatarState =
    if (uiState.connectionState == dev.livachat.sdk.model.ConnectionState.DISCONNECTED) {
        AvatarState.IDLE
    } else {
        when (uiState.sessionState) {
            SessionState.IDLE -> AvatarState.IDLE
            SessionState.LISTENING -> AvatarState.LISTENING
            SessionState.THINKING -> AvatarState.THINKING
            SessionState.SPEAKING -> AvatarState.SPEAKING
        }
    }

private fun mapSessionStateToIndicator(uiState: ConversationUiState): IndicatorState =
    when (uiState.connectionState) {
        dev.livachat.sdk.model.ConnectionState.DISCONNECTED -> IndicatorState.DISCONNECTED
        dev.livachat.sdk.model.ConnectionState.CONNECTING -> IndicatorState.CONNECTING
        dev.livachat.sdk.model.ConnectionState.RECONNECTING -> IndicatorState.CONNECTING
        dev.livachat.sdk.model.ConnectionState.CONNECTED -> uiState.sessionState.toIndicatorState()
    }

@Preview(showBackground = true)
@Composable
private fun ConversationScreenPreview() {
    LivaChatTheme {
        ConversationScreen(
            onNavigateToHistory = {},
            onNavigateToSettings = {},
            onOpenLink = {},
            onNavigateToTimeMachine = {},
        )
    }
}
