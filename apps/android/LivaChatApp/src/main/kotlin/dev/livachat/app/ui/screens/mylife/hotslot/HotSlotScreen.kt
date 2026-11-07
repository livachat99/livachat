package dev.livachat.app.ui.screens.mylife.hotslot

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.components.EmptyState
import dev.livachat.app.ui.screens.conversation.AgentJobPayload
import dev.livachat.app.ui.screens.conversation.AgentJobResultHost
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.MylifeTheme
import dev.livachat.app.ui.screens.mylife.hotslot.components.ErrorHint
import dev.livachat.app.ui.screens.mylife.hotslot.components.HotSlotRowItem
import dev.livachat.app.ui.screens.mylife.hotslot.components.LoadingHint
import dev.livachat.app.ui.theme.Spacing
import java.time.Instant

/** 触底加载提前量（行数）：可见项进入末 N 行即预取下一页（对齐 FrameGrid）。 */
private const val LOAD_MORE_THRESHOLD = 4

/**
 * hotSlotView（独立全屏路由，docs/android_timeline_hotslot.md §4.3）：取数窗口 [startMs, endMs]
 * 的五类事件按 ts 升序单流渲染（chat/agent_job/transcribe/translate 文本行 + video 帧 3 列网格）。
 * 自带 `MylifeTheme` + Abyss 底色（不经过宿主，故不复制星空背景，对齐 FrameGalleryScreen）。
 * pending（日卡 hasPending 透传的路由快照为初值，§11 P1① 由 VM 随 overview 轮询单向收敛）
 * 驱动顶栏「分析中…」与空态文案（Q11）；
 * 帧图可点 → 单图全屏查看器覆盖层 + 保存到相册权限流（§9）。
 */
@Composable
fun HotSlotScreen(
    startMs: Long,
    endMs: Long,
    pending: Boolean,
    onNavigateBack: () -> Unit,
    onOpenLink: (String) -> Unit,
    viewModel: HotSlotViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()
    LaunchedEffect(startMs, endMs, pending) { viewModel.load(startMs, endMs, pending) }

    val uiLocale = LocalConfiguration.current.locales[0]
    val transcriptSuffix = stringResource(R.string.conv_msg_suffix_transcript)
    val originalSuffix = stringResource(R.string.conv_msg_suffix_original)
    val translationSuffix = stringResource(R.string.conv_msg_suffix_translation)
    val rows = remember(uiState.state) {
        (uiState.state as? HotSlotState.Data)?.let { data ->
            buildHotSlotRows(data.events, uiLocale, transcriptSuffix, originalSuffix, translationSuffix)
        }.orEmpty()
    }
    val loadingMore = (uiState.state as? HotSlotState.Data)?.loadingMore == true
    val atListEnd by remember(rows.size) {
        derivedStateOf {
            val last = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
            rows.isNotEmpty() && last >= rows.size - LOAD_MORE_THRESHOLD
        }
    }
    // 触底预取：**每个「新触发」只发一次**——失败后 `loadingMore` 回落不会立刻重发
    // （否则断网下 OkHttp 秒抛 → 立即重试风暴；docs/android_timeline_audio_gallery.md §9.11、
    // 既有债务条目 docs/android_timeline_topics.md §8）；终止（越窗/游标耗尽）时 VM 侧 no-op。
    // 重新武装 = 滚离阈值区再回来，或上一页成功追加（rows.size 变化，保证短页自动续填）
    var lastAttemptSize by remember { mutableIntStateOf(-1) }
    var leftEndSinceAttempt by remember { mutableStateOf(true) }
    LaunchedEffect(atListEnd, rows.size, loadingMore) {
        if (!atListEnd) {
            leftEndSinceAttempt = true
            return@LaunchedEffect
        }
        val freshTrigger = rows.size != lastAttemptSize || leftEndSinceAttempt
        if (!loadingMore && freshTrigger) {
            lastAttemptSize = rows.size
            leftEndSinceAttempt = false
            viewModel.loadMore()
        }
    }

    // 保存到相册权限（仅 API ≤ 28；§9.3，照搬 FrameGalleryScreen.kt:69-98）：懒请求 +
    // 在飞守卫 + pending 用 rememberSaveable（权限框弹出期间重建不丢，回调仍能命中）；
    // 回调以 VM 的 viewerFrame 为准——单图无翻页，不存在 gallery"回调晚于翻页"竞态
    val context = LocalContext.current
    var savePermissionPending by rememberSaveable { mutableStateOf(false) }
    val saveImagePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        savePermissionPending = false // 两条路径都清空：否则在飞守卫把保存入口永久锁死
        if (granted) viewModel.saveViewerFrame() else viewModel.onSavePermissionDenied()
    }

    fun handleSaveFrame() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.saveViewerFrame()
        } else if (!savePermissionPending) {
            // 弹窗在飞：忽略期间点击（对在飞请求重复 launch 同一 launcher 的行为不值得依赖）
            savePermissionPending = true
            saveImagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }

    MylifeTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(MylifeColors.Abyss)
                .statusBarsPadding(),
        ) {
            Column(modifier = Modifier.fillMaxSize().navigationBarsPadding()) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Spacing.sm, vertical = Spacing.xs),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    IconButton(onClick = onNavigateBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = MylifeColors.Dust)
                    }
                    Text(
                        text = formatHotSlotTitle(startMs, LocalConfiguration.current.locales[0]),
                        style = MaterialTheme.typography.titleMedium,
                        color = MylifeColors.Gold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (uiState.pending) {
                        Text(
                            text = stringResource(R.string.mylife_analyzing),
                            style = MaterialTheme.typography.labelMedium,
                            color = MylifeColors.Gold.copy(alpha = 0.8f),
                            modifier = Modifier.padding(end = Spacing.sm),
                        )
                    }
                }

                when (val state = uiState.state) {
                    HotSlotState.Loading -> LoadingHint(stringResource(R.string.mylife_loading), Modifier.weight(1f))

                    HotSlotState.Empty -> if (uiState.pending) {
                        // Q11：pending 空态不落"无内容"文案（has_pending 为日级语义，
                        // 本 slot 内容可能已齐），提供刷新入口
                        PendingEmptyHint(onRetry = viewModel::retry, modifier = Modifier.weight(1f))
                    } else {
                        EmptyState(
                            icon = Icons.Filled.Info,
                            title = stringResource(R.string.mylife_hotslot_empty),
                            modifier = Modifier.weight(1f).fillMaxWidth(),
                        )
                    }

                    is HotSlotState.Error -> ErrorHint(
                        message = state.message,
                        onRetry = viewModel::retry,
                        modifier = Modifier.weight(1f),
                    )

                    is HotSlotState.Data -> LazyColumn(
                        state = listState,
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                        // 覆盖层不做全量手势消费（消费移动会破坏页内 tap 检测），纵向拖动会漏到本列表：
                        // 打开任一覆盖层时禁滚（D10 守卫，同 conversation/History）
                        userScrollEnabled = uiState.viewerFrame == null &&
                            uiState.viewerAgentJobKey == null,
                        contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.xs),
                    ) {
                        items(rows, key = { it.key }) { row ->
                            HotSlotRowItem(
                                row,
                                onFrameClick = viewModel::onFrameClick,
                                onLinkClick = onOpenLink,
                                onAgentJobClick = { key, offset -> viewModel.onAgentJobClick(key, offset) },
                            )
                        }
                        if (state.loadingMore) {
                            item { LoadingHint(stringResource(R.string.mylife_loading)) }
                        }
                    }
                }
            }

            // agent job 结果全屏查看器（覆盖层非路由；宿主内含身份解析 + 未命中守卫，§12.3.3）
            AgentJobResultHost(
                viewId = uiState.viewerAgentJobKey,
                initialCharOffset = uiState.agentJobInitialCharOffset,
                resolve = { key ->
                    rows.filterIsInstance<HotSlotRow.Text>()
                        .firstOrNull { it.key == key }
                        ?.let { AgentJobPayload(it.body, Instant.ofEpochMilli(it.tsMs)) }
                },
                onDismiss = viewModel::onAgentJobViewerDismiss,
                onLinkClick = onOpenLink,
                // rows 是分页窗口：Q11 轮询重取会截回首页，未命中 ≠ 事件消失（§12.9）
                keepLastPayloadOnMiss = true,
            )

            // 帧全屏查看器（§9.3；覆盖层非路由，BackHandler 先关查看器再退路由）。
            // 必须是外层 Box 的直接子节点——Column 内的 fillMaxSize 子项会吃光高度、列表塌 0
            uiState.viewerFrame?.let { frame ->
                HotSlotFrameViewer(
                    frame = frame,
                    onDismiss = viewModel::onViewerDismiss,
                    saveTip = uiState.imageSaveTip,
                    saving = uiState.saving,
                    onSaveClick = ::handleSaveFrame,
                )
            }
        }
    }
}

/** pending 空态（Q11）：正文不承诺该 slot 未完成，仅提示整体分析中 + 刷新入口。 */
@Composable
internal fun PendingEmptyHint(onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(stringResource(R.string.mylife_analyzing_content), style = MaterialTheme.typography.bodyMedium, color = MylifeColors.Dust)
        Spacer(modifier = Modifier.height(Spacing.md))
        OutlinedButton(onClick = onRetry) {
            Text(stringResource(R.string.mylife_refresh), color = MylifeColors.Gold)
        }
    }
}
