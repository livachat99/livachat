package dev.livachat.app.ui.screens.history

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.domain.model.MessageRole
import dev.livachat.app.domain.model.isAgentJobResult
import dev.livachat.app.ui.components.MessageBubble
import dev.livachat.app.ui.screens.conversation.AgentJobPayload
import dev.livachat.app.ui.screens.conversation.AgentJobResultHost
import dev.livachat.app.ui.screens.conversation.ImageViewerOverlay
import dev.livachat.app.ui.theme.Background
import dev.livachat.app.ui.theme.LivaChatTheme
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryDetailScreen(
    sessionId: String,
    onNavigateBack: () -> Unit,
    onOpenLink: (String) -> Unit,
    viewModel: HistoryDetailViewModel = hiltViewModel(),
) {
    val messages by viewModel.messages.collectAsState()
    val imageViewerMessageId by viewModel.imageViewerMessageId.collectAsState()
    val imageSaveTip by viewModel.imageSaveTip.collectAsState()
    val agentJobResultViewId by viewModel.agentJobResultViewId.collectAsState()
    val agentJobResultInitialCharOffset by viewModel.agentJobResultInitialCharOffset.collectAsState()
    var showDeleteConfirm by remember { mutableStateOf(false) }
    val context = LocalContext.current

    LaunchedEffect(sessionId) {
        viewModel.loadSession(sessionId)
    }

    // ── 查看器保存到相册：权限懒请求（仅 API ≤ 28；拷贝自 ConversationScreen）──
    // 入口（进详情/打开查看器）不检查不申请，仅点击保存时按需申请一次。
    // rememberSaveable（非裸 remember）：权限框在飞期间重建后，回调仍能拿到暂存 id
    var pendingSaveMessageId by rememberSaveable { mutableStateOf<String?>(null) }
    val saveImagePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val messageId = pendingSaveMessageId
        pendingSaveMessageId = null   // 两条路径都清空：否则在飞守卫把保存入口永久锁死
        if (messageId != null) {
            if (granted) {
                viewModel.saveViewerImage(messageId)
            } else {
                // 拒绝只提示（不做"去设置"引导）；id 已清空，可再次点击重试
                viewModel.onSavePermissionDenied()
            }
        }
    }

    fun handleSaveViewerImage(messageId: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.saveViewerImage(messageId)
        } else if (pendingSaveMessageId == null) {
            // 弹窗在飞：忽略期间点击（对在飞请求重复 launch 同一 launcher 的行为不值得依赖）
            pendingSaveMessageId = messageId
            saveImagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize().background(Background).navigationBarsPadding()) {
            TopAppBar(
                title = { Text(stringResource(R.string.history_detail_title), color = TextPrimary) },
                navigationIcon = {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = TextPrimary)
                    }
                },
                actions = {
                    IconButton(onClick = { showDeleteConfirm = true }) {
                        Icon(Icons.Filled.Delete, stringResource(R.string.common_delete), tint = TextPrimary)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = Background),
            )

            LazyColumn(
                modifier = Modifier.fillMaxSize().padding(horizontal = Spacing.md),
                // 查看器覆盖层不做全量手势消费（消费移动会破坏页内 tap 检测），纵向拖动会漏到
                // 本列表；打开查看器时禁用用户滚动（与 conversation 同款守卫）
                userScrollEnabled = imageViewerMessageId == null && agentJobResultViewId == null,
            ) {
                items(messages, key = { it.id }) { message ->
                    MessageBubble(
                        message = message.copy(isFinal = true),
                        showCursor = false,
                        onLinkClick = onOpenLink,
                        onImageClick = { viewModel.openImageViewer(message.id) },
                        // 仅 agent job 结果气泡可点（§2.1 判定）；历史页无会话副作用
                        onJobResultClick = if (message.isAgentJobResult()) {
                            { charOffset -> viewModel.openAgentJobResult(message.id, charOffset) }
                        } else {
                            null
                        },
                    )
                }
            }
        }

        // ── Full-screen agent job result（点击 job 结果气泡进入；宿主内含身份解析 + 未命中守卫，§12.3.2）──
        AgentJobResultHost(
            viewId = agentJobResultViewId,
            initialCharOffset = agentJobResultInitialCharOffset,
            resolve = { id ->
                messages.firstOrNull { it.id == id }?.let { AgentJobPayload(it.text, it.timestamp) }
            },
            onDismiss = { viewModel.closeAgentJobResult() },
            onLinkClick = onOpenLink,
        )

        // ── Full-screen image viewer（点击气泡图片进入；与 conversation 同款覆盖层）──
        val viewerId = imageViewerMessageId
        if (viewerId != null) {
            // 实时派生：过滤条件与气泡渲染条件逐字一致（image != null && 非用户）
            val viewerImages = messages.filter {
                it.image != null && it.role != MessageRole.USER
            }
            val initialPage = viewerImages.indexOfFirst { it.id == viewerId }
            if (initialPage >= 0) {
                ImageViewerOverlay(
                    images = viewerImages,
                    initialPage = initialPage,
                    onDismiss = { viewModel.closeImageViewer() },
                    saveTip = imageSaveTip,
                    onSaveClick = { messageId -> handleSaveViewerImage(messageId) },
                )
            } else {
                // 守卫：id 未命中带图消息（正常路径不应发生）→ 自动关窗，防状态悬空
                LaunchedEffect(viewerId) {
                    viewModel.closeImageViewer()
                }
            }
        }
    }

    // Delete confirmation（标题居中缩小字号；正文与按钮并排居中——同对话历史页惯例）
    if (showDeleteConfirm) {
        AlertDialog(
            onDismissRequest = { showDeleteConfirm = false },
            title = {
                Text(
                    stringResource(R.string.history_delete_title),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    style = MaterialTheme.typography.titleMedium,
                )
            },
            text = {
                Text(
                    stringResource(R.string.history_detail_delete_message),
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                )
            },
            confirmButton = {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.Center,
                ) {
                    TextButton(onClick = { showDeleteConfirm = false }) {
                        Text(stringResource(R.string.common_cancel))
                    }
                    TextButton(onClick = {
                        viewModel.deleteSession(sessionId)
                        showDeleteConfirm = false
                        onNavigateBack()
                    }) {
                        Text(stringResource(R.string.common_delete), color = dev.livachat.app.ui.theme.Error)
                    }
                }
            },
        )
    }
}

@Preview(showBackground = true)
@Composable
private fun HistoryDetailScreenPreview() {
    LivaChatTheme {
        Box(modifier = Modifier.background(Background).fillMaxSize()) {
            Text("History Detail Preview", color = TextPrimary)
        }
    }
}
