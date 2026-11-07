package dev.livachat.app.ui.screens.permission

import android.Manifest
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.DisposableEffect
import com.google.accompanist.permissions.ExperimentalPermissionsApi
import com.google.accompanist.permissions.isGranted
import com.google.accompanist.permissions.rememberPermissionState
import dev.livachat.app.R
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.SurfaceVariant
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TextSecondary

@OptIn(ExperimentalPermissionsApi::class)
@Composable
fun PermissionScreen(
    onComplete: () -> Unit,
    viewModel: PermissionViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val event by viewModel.event.collectAsState()

    val context = androidx.compose.ui.platform.LocalContext.current
    val cameraPermission = rememberPermissionState(Manifest.permission.CAMERA)

    // 是否真正向系统询问过 mic：询问过且系统不再允许 → 不再发弹窗，直接提示
    var micAskedBefore by rememberSaveable { mutableStateOf(false) }

    fun micGrantedByOs(): Boolean =
        context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED

    fun micCanAskAgain(): Boolean {
        val activity = context as? android.app.Activity ?: return false
        return activity.shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
    }

    // 与系统状态同步（保留原有"未授权且系统不再询问即视为永久拒绝"的判定，含启动首帧）
    fun syncMicWithOs() {
        val granted = micGrantedByOs()
        if (granted) {
            // 从系统设置授权返回后也要视为"未再询问"，撤销后系统仍可再次弹窗
            micAskedBefore = false
        }
        viewModel.onMicPermissionResult(
            granted = granted,
            permanentlyDenied = !granted && !micCanAskAgain(),
        )
    }

    val micLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            micAskedBefore = false
            viewModel.onMicPermissionResult(granted = true)
        } else {
            micAskedBefore = true
            val permanentlyDenied = !micCanAskAgain()
            viewModel.onMicPermissionResult(
                granted = false,
                permanentlyDenied = permanentlyDenied,
            )
            // 请求结果返回后再决定是否提示：拒绝 → 提示
            viewModel.setMicRequiredHint(show = true)
        }
    }

    fun requestMic() {
        if (micGrantedByOs()) {
            micAskedBefore = false
            viewModel.onMicPermissionResult(granted = true)
            return
        }
        if (micAskedBefore && !micCanAskAgain()) {
            // 曾询问且系统已不再弹窗：直接提示，引导去系统设置
            viewModel.setMicRequiredHint(show = true)
            return
        }
        micAskedBefore = true
        // 请求结果返回前不显示提示
        viewModel.setMicRequiredHint(show = false)
        micLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    fun syncPermissionsWithOs() {
        syncMicWithOs()
        viewModel.onCameraPermissionResult(
            granted = context.checkSelfPermission(Manifest.permission.CAMERA) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED,
        )
    }

    // 首次组合与每次回到前台时与系统授权状态同步（覆盖从系统设置返回后的授权/撤销）
    LaunchedEffect(Unit) { syncPermissionsWithOs() }
    val lifecycleOwner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = androidx.lifecycle.LifecycleEventObserver { _, event ->
            if (event == androidx.lifecycle.Lifecycle.Event.ON_RESUME) {
                syncPermissionsWithOs()
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    LaunchedEffect(cameraPermission.status) {
        if (cameraPermission.status.isGranted) {
            viewModel.onCameraPermissionResult(true)
        }
    }

    LaunchedEffect(event) {
        if (event == PermissionEvent.NavigateToConversation) {
            viewModel.clearEvent()
            onComplete()
        }
    }

    PermissionContent(
        uiState = uiState,
        onRequestMic = { requestMic() },
        onRequestCamera = { cameraPermission.launchPermissionRequest() },
        onStartChat = { viewModel.onStartChat() },
        onGoToSettings = {
            val intent = android.content.Intent(
                android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                android.net.Uri.parse("package:${context.packageName}"),
            )
            context.startActivity(intent)
        },
    )
}

@OptIn(ExperimentalPermissionsApi::class)
@Composable
private fun PermissionContent(
    uiState: PermissionUiState,
    onRequestMic: () -> Unit,
    onRequestCamera: () -> Unit,
    onStartChat: () -> Unit,
    onGoToSettings: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Background),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .systemBarsPadding()
                .padding(Spacing.lg)
                .verticalScroll(rememberScrollState()),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Spacer(modifier = Modifier.height(Spacing.xxl))

            Text(
                text = stringResource(R.string.perm_dialog_title),
                style = MaterialTheme.typography.titleLarge,
                color = TextPrimary,
                textAlign = TextAlign.Center,
            )

            Spacer(modifier = Modifier.height(Spacing.xl))

            // Mic permission card
            PermissionCard(
                icon = Icons.Filled.Mic,
                title = stringResource(R.string.perm_mic_title),
                description = stringResource(R.string.perm_mic_desc),
                isGranted = uiState.micGranted,
                onRequest = onRequestMic,
            )

            Spacer(modifier = Modifier.height(Spacing.lg))

            // Camera permission card
            PermissionCard(
                icon = Icons.Filled.CameraAlt,
                title = stringResource(R.string.perm_screen_camera_title),
                description = stringResource(R.string.perm_camera_desc),
                isGranted = uiState.cameraGranted,
                onRequest = onRequestCamera,
            )

            Spacer(modifier = Modifier.height(Spacing.xl))

            // Main CTA: "开始聊天"——mic 未授权时经 ViewModel 拦截并拉起系统授权
            Button(
                onClick = {
                    if (!uiState.micGranted) {
                        onRequestMic()
                    }
                    onStartChat()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(56.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = BrandIndigo,
                    contentColor = TextPrimary,
                ),
                shape = RoundedCornerShape(16.dp),
            ) {
                Text(
                    text = stringResource(R.string.perm_start_chat),
                    style = MaterialTheme.typography.bodyLarge,
                )
            }

            if (uiState.showMicRequiredHint) {
                Spacer(modifier = Modifier.height(Spacing.sm))
                Text(
                    text = stringResource(R.string.perm_mic_required_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = dev.livachat.app.ui.theme.Error,
                    textAlign = TextAlign.Center,
                )
            }

            if (uiState.showGoToSettings) {
                Spacer(modifier = Modifier.height(Spacing.sm))
                TextButton(onClick = onGoToSettings) {
                    Text(stringResource(R.string.perm_go_to_settings))
                }
            }

            Spacer(modifier = Modifier.height(Spacing.xxl))
        }
    }
}

@Composable
private fun PermissionCard(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    description: String,
    isGranted: Boolean,
    onRequest: () -> Unit,
) {
    Surface(
        color = SurfaceVariant,
        shape = RoundedCornerShape(16.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(Spacing.md),
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .background(BrandIndigo.copy(alpha = 0.18f), CircleShape),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        imageVector = icon,
                        contentDescription = null,
                        modifier = Modifier.size(22.dp),
                        tint = BrandIndigo,
                    )
                }
                Spacer(modifier = Modifier.width(Spacing.md))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                    Text(
                        text = description,
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary,
                    )
                }
                if (isGranted) {
                    Icon(
                        imageVector = Icons.Filled.CheckCircle,
                        contentDescription = stringResource(R.string.perm_granted),
                        tint = dev.livachat.app.ui.theme.Success,
                        modifier = Modifier.size(24.dp),
                    )
                } else {
                    TextButton(onClick = onRequest) {
                        Text(stringResource(R.string.perm_grant_action))
                    }
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun PermissionScreenPreview() {
    LivaChatTheme {
        // Preview with placeholder states
        PermissionContent(
            uiState = PermissionUiState(micGranted = false, cameraGranted = false),
            onRequestMic = {},
            onRequestCamera = {},
            onStartChat = {},
            onGoToSettings = {},
        )
    }
}
