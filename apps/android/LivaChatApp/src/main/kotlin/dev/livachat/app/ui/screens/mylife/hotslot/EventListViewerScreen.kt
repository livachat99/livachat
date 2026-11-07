package dev.livachat.app.ui.screens.mylife.hotslot

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.platform.testTag
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
import kotlinx.coroutines.delay

/** 取数窗口半径：被点事件前后各 10 分钟（docs/android_timeline_topics.md §3.3.3，需求 4）。 */
internal const val EVENT_WINDOW_MS = 10 * 60_000L

/** 锚点高亮时长（2s，与 Gallery 帧网格 focus 高亮同量级）。 */
private const val ANCHOR_HIGHLIGHT_MS = 2000L

/** 触底加载提前量（行数，对齐 HotSlotScreen 的 LOAD_MORE_THRESHOLD）。 */
private const val LOAD_MORE_THRESHOLD = 4

/** 锚点高亮行 testTag（仪器断言用）。 */
internal const val ANCHOR_HIGHLIGHT_TAG = "anchorHighlight"

/**
 * eventListViewer（docs/android_timeline_topics.md §3.3.3）：搜索命中事件的前后 10 分钟。
 * 取数/行模型/行组件/帧查看器与 hotSlotView 逐件相同（复用 `HotSlotViewModel` + 同族组件）；
 * 唯一新增交互 = 锚点行定位 + 高亮（含重度日锚点追击，R1 约定）。
 */
@Composable
fun EventListViewerScreen(
    tsMs: Long,
    pending: Boolean,
    onNavigateBack: () -> Unit,
    onOpenLink: (String) -> Unit,
    viewModel: HotSlotViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val listState = rememberLazyListState()
    val startMs = tsMs - EVENT_WINDOW_MS
    val endMs = tsMs + EVENT_WINDOW_MS
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
    // 既有债务条目 docs/android_timeline_topics.md §8）。重新武装 = 滚离阈值区再回来，
    // 或上一页成功追加（rows.size 变化，保证短页自动续填）
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

    // 锚点追击（约定 2：响应式触发，Q11 重取后自动重入）。键用整个 state 而非 (events, exhausted)：
    // 首页 loadingMore=true→false 的中间态会挡掉首次追击，而 events 仍是同一列表实例 → 只以
    // events/exhausted 为键时 effect 不重入、追击不再启动（内部 chasing/loadingMore guard 幂等）。
    LaunchedEffect(uiState.state, tsMs) { viewModel.ensureLoadedUntil(tsMs) }

    // 锚点定位：只做一次（防续页/重取后重复滚动）；rows 变化后重试直到解析出锚行
    var located by remember { mutableStateOf(false) }
    var highlightKey by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(rows, tsMs) {
        if (located || rows.isEmpty()) return@LaunchedEffect
        val index = anchorRowIndex(rows, tsMs)
        if (index < 0) return@LaunchedEffect   // 追击未覆盖：等下一次 rows 变化
        // 瞬时跳转（对齐 Gallery focus）：重度日锚点可深至百余行，animate 会长时间滚过中间行
        listState.scrollToItem(index)
        located = true
        highlightKey = rows[index].key
    }
    // 高亮自动清除：独立于 rows 变化（否则续页/重取重启上面的 effect 吞掉 delay → 高亮残留）
    LaunchedEffect(highlightKey) {
        if (highlightKey != null) {
            delay(ANCHOR_HIGHLIGHT_MS)
            highlightKey = null
        }
    }

    // 保存到相册权限流（照搬 HotSlotScreen：懒请求 + 在飞守卫 + pending 用 rememberSaveable）
    val context = LocalContext.current
    var savePermissionPending by rememberSaveable { mutableStateOf(false) }
    val saveImagePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        savePermissionPending = false
        if (granted) viewModel.saveViewerFrame() else viewModel.onSavePermissionDenied()
    }

    fun handleSaveFrame() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.saveViewerFrame()
        } else if (!savePermissionPending) {
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
                        text = formatHotSlotTitle(tsMs, LocalConfiguration.current.locales[0]),
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
                        // 打开任一覆盖层时禁滚（D10 守卫，同 conversation/History；程序化滚动不受影响，
                        // 故锚点定位/追击照常）
                        userScrollEnabled = uiState.viewerFrame == null &&
                            uiState.viewerAgentJobKey == null,
                        contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.xs),
                    ) {
                        items(rows, key = { it.key }) { row ->
                            // 锚点高亮载体（G1）：Box(border) 只绘制不拦截点击，共享行件零改动
                            Box(
                                modifier = if (row.key == highlightKey) {
                                    Modifier
                                        .testTag(ANCHOR_HIGHLIGHT_TAG)
                                        .border(2.dp, MylifeColors.Gold, RoundedCornerShape(8.dp))
                                } else {
                                    Modifier
                                },
                            ) {
                                HotSlotRowItem(
                                    row,
                                    onFrameClick = viewModel::onFrameClick,
                                    onLinkClick = onOpenLink,
                                    onAgentJobClick = { key, offset -> viewModel.onAgentJobClick(key, offset) },
                                )
                            }
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

            // 帧全屏查看器（覆盖层非路由；HotSlotFrameViewer 自带 BackHandler 先关查看器）
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
