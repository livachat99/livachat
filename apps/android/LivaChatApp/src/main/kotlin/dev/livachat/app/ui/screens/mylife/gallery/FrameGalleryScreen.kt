package dev.livachat.app.ui.screens.mylife.gallery

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.rememberLazyGridState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import dev.livachat.app.R
import dev.livachat.app.ui.components.EmptyState
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.MylifeTheme
import dev.livachat.app.ui.screens.mylife.gallery.components.ErrorHint
import dev.livachat.app.ui.screens.mylife.gallery.components.FrameGrid
import dev.livachat.app.ui.screens.mylife.gallery.components.LoadingHint
import dev.livachat.app.ui.theme.Spacing
import kotlinx.coroutines.delay

private const val FOCUS_HIGHLIGHT_MS = 2000L

/**
 * Frame Gallery（独立全屏路由，docs/android_timeline_image_gallery.md §3.4）：
 * 3 列方图网格（该范围全部关键帧，ts 升序）+ 顶栏（返回/label/张数/分析中）+ 全屏查看器。
 * 自带 `MylifeTheme` + Abyss 底色（不经过宿主，故不复制星空，§3.7 注）。
 */
@Composable
fun FrameGalleryScreen(
    from: String,
    to: String,
    label: String,
    focusFrameId: String?,
    pending: Boolean,
    onNavigateBack: () -> Unit,
    viewModel: FrameGalleryViewModel = hiltViewModel(),
) {
    val uiState by viewModel.uiState.collectAsState()
    val gridState = rememberLazyGridState()
    var highlightedIndex by remember { mutableStateOf<Int?>(null) }
    var focusConsumed by remember { mutableStateOf(false) }

    // 保存到相册权限（仅 API ≤ 28；§3.5，照 conversation §9.4）：懒请求 + 在飞守卫 +
    // pending 用 rememberSaveable（权限框弹出期间重建不丢，回调仍能命中）
    val context = LocalContext.current
    var pendingSaveFrameId by rememberSaveable { mutableStateOf<String?>(null) }
    val saveImagePermissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val frameId = pendingSaveFrameId
        pendingSaveFrameId = null // 两条路径都清空：否则在飞守卫把保存入口永久锁死
        if (frameId != null) {
            if (granted) {
                viewModel.saveFrameById(frameId)
            } else {
                viewModel.onSavePermissionDenied()
            }
        }
    }

    fun handleSaveFrame(frameId: String) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED
        ) {
            viewModel.saveFrameById(frameId)
        } else if (pendingSaveFrameId == null) {
            // 弹窗在飞：忽略期间点击（对在飞请求重复 launch 同一 launcher 的行为不值得依赖）
            pendingSaveFrameId = frameId
            saveImagePermissionLauncher.launch(Manifest.permission.WRITE_EXTERNAL_STORAGE)
        }
    }

    LaunchedEffect(from, to) { viewModel.load(from, to) }

    val frames = (uiState.state as? FrameGalleryState.Data)?.frames.orEmpty()

    // focus 定位（B2）：首屏加载完成后命中则 scrollToItem + 短暂高亮；未命中（帧在
    // 后续页）降级为停在顶部——不做"翻页直到找到"，避免大 period 下无界请求（E1 已接受降级）
    LaunchedEffect(uiState.state) {
        if (focusConsumed || uiState.state !is FrameGalleryState.Data) return@LaunchedEffect
        focusConsumed = true
        val index = focusFrameId?.let { id -> frames.indexOfFirst { it.frameId == id } } ?: -1
        if (index >= 0) {
            gridState.scrollToItem(index)
            highlightedIndex = index
        }
    }

    // 高亮自动清除：独立于 state 变化（loadMore 等会让上面的 effect 重启，吞掉 delay → 高亮残留）
    LaunchedEffect(highlightedIndex) {
        if (highlightedIndex != null) {
            delay(FOCUS_HIGHLIGHT_MS)
            highlightedIndex = null
        }
    }

    // 返回键（§9.4.2）：删除中吞掉（Q9.5 全拦截）；编辑模式先退编辑模式，再按才退画廊。
    // 组合点在查看器覆盖层之前注册 → 查看器打开时其 BackHandler 后注册、优先关查看器
    BackHandler(enabled = uiState.editMode || uiState.deleting) {
        if (!uiState.deleting) viewModel.exitEditMode()
    }

    MylifeTheme {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(MylifeColors.Abyss)
                .statusBarsPadding(),
        ) {
            Column(modifier = Modifier.fillMaxSize()) {
                // 顶栏三区（§9.4.1）：左=返回+label、中=计数（pending 前缀）、右=编辑/退出。
                // 左/右 zone 等权：中区真居中；label 在左 zone 内 Ellipsis，不压中区
                val headerData = uiState.state as? FrameGalleryState.Data
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = Spacing.sm, vertical = Spacing.xs),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(
                        modifier = Modifier.weight(1f),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        IconButton(
                            onClick = onNavigateBack,
                            // 删除在飞拦截返回（Q9.5）：BackHandler 只拦系统返回，箭头是另一条
                            // popBackStack 路径——放行会取消在飞删除（服务端可能已删）+ 丢响应
                            enabled = !uiState.deleting,
                        ) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, stringResource(R.string.common_back), tint = MylifeColors.Dust)
                        }
                        Text(
                            text = label,
                            style = MaterialTheme.typography.titleMedium,
                            color = MylifeColors.Gold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    if (headerData != null) {
                        Text(
                            text = if (pending) {
                                pluralStringResource(R.plurals.mylife_analyzing_frames, headerData.total, headerData.total)
                            } else {
                                pluralStringResource(R.plurals.mylife_frames_count, headerData.total, headerData.total)
                            },
                            style = MaterialTheme.typography.labelMedium,
                            color = MylifeColors.Dust,
                        )
                    }
                    Box(modifier = Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) {
                        val canEdit = headerData != null && headerData.frames.isNotEmpty()
                        if (uiState.editMode || canEdit) {
                            TextButton(
                                onClick = {
                                    if (uiState.editMode) viewModel.exitEditMode()
                                    else viewModel.enterEditMode()
                                },
                                enabled = !uiState.deleting,
                            ) {
                                Text(
                                    text = if (uiState.editMode) stringResource(R.string.mylife_edit_exit) else stringResource(R.string.mylife_edit),
                                    color = MylifeColors.Gold,
                                )
                            }
                        }
                    }
                }

                when (val state = uiState.state) {
                    FrameGalleryState.Loading -> LoadingHint(stringResource(R.string.mylife_loading), Modifier.weight(1f))
                    FrameGalleryState.Empty -> EmptyState(
                        icon = Icons.Filled.Info,
                        title = stringResource(R.string.mylife_gallery_empty_title),
                        subtitle = stringResource(R.string.mylife_analysis_pending),
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )
                    is FrameGalleryState.Error -> ErrorHint(
                        message = state.message,
                        onRetry = { viewModel.load(from, to) },
                        modifier = Modifier.weight(1f),
                    )
                    is FrameGalleryState.Data -> FrameGrid(
                        frames = state.frames,
                        loadingMore = state.loadingMore,
                        gridState = gridState,
                        onFrameClick = viewModel::onFrameClick,
                        onLoadMore = viewModel::loadMore,
                        highlightedIndex = highlightedIndex,
                        editMode = uiState.editMode,
                        selectedFrameIds = uiState.selectedFrameIds,
                        onToggleSelect = viewModel::toggleSelect,
                        modifier = Modifier
                            .weight(1f)
                            // 非编辑态无底部操作栏，网格自行避让系统导航栏；
                            // 编辑态的底栏已自带 navigationBarsPadding，避免重复留白。
                            .then(
                                if (uiState.editMode) Modifier
                                else Modifier.navigationBarsPadding(),
                            ),
                    )
                }

                // 编辑模式底栏（§9.4.1）：0 选禁用；删除中 = 转圈 + 「删除中…」禁用
                if (uiState.editMode) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .navigationBarsPadding()
                            .padding(vertical = Spacing.sm),
                        contentAlignment = Alignment.Center,
                    ) {
                        OutlinedButton(
                            onClick = viewModel::requestDelete,
                            enabled = uiState.selectedFrameIds.isNotEmpty() && !uiState.deleting,
                            border = BorderStroke(1.dp, MylifeColors.Red),
                            colors = ButtonDefaults.outlinedButtonColors(
                                contentColor = MylifeColors.Red,
                            ),
                        ) {
                            if (uiState.deleting) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(18.dp),
                                    color = MylifeColors.Red,
                                    strokeWidth = 2.dp,
                                )
                                Spacer(modifier = Modifier.width(Spacing.sm))
                                Text(stringResource(R.string.mylife_deleting))
                            } else {
                                Text(stringResource(R.string.mylife_delete_count, uiState.selectedFrameIds.size))
                            }
                        }
                    }
                }
            }

            // 全屏查看器（覆盖层非路由；BackHandler 先关查看器，再退出 Gallery 路由）
            uiState.viewerIndex?.let { index ->
                FrameViewerOverlay(
                    frames = frames,
                    initialPage = index,
                    onDismiss = viewModel::onViewerDismiss,
                    onNearEnd = viewModel::loadMore,
                    saveTip = uiState.imageSaveTip,
                    savingFrameId = uiState.savingFrameId,
                    onSaveClick = ::handleSaveFrame,
                )
            }

            // 删除提示（§9.4.1：屏幕级底部居中覆盖层——独立于底栏，成功 tip 在退出编辑
            // 模式、底栏消失后仍需可见；编辑模式时上移避开底栏）
            uiState.deleteTip?.let { tip ->
                Box(
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .navigationBarsPadding()
                        .padding(bottom = if (uiState.editMode) 84.dp else 32.dp),
                ) {
                    Surface(
                        color = Color.Black.copy(alpha = 0.75f),
                        shape = RoundedCornerShape(16.dp),
                    ) {
                        Text(
                            text = tip,
                            color = MylifeColors.Star,
                            style = MaterialTheme.typography.labelLarge,
                            modifier = Modifier.padding(
                                horizontal = Spacing.md,
                                vertical = Spacing.sm,
                            ),
                        )
                    }
                }
            }

            // 删除确认弹窗（Q9.1；对齐 HistoryScreen「清空会话」弹窗样式：title/正文/按钮全居中）
            if (uiState.deleteConfirm) {
                AlertDialog(
                    onDismissRequest = viewModel::dismissDeleteConfirm,
                    title = {
                        Text(
                            text = stringResource(R.string.mylife_delete_photos),
                            style = MaterialTheme.typography.titleMedium,
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    },
                    text = {
                        Text(
                            text = pluralStringResource(R.plurals.mylife_delete_confirm, uiState.selectedFrameIds.size, uiState.selectedFrameIds.size),
                            textAlign = TextAlign.Center,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    },
                    confirmButton = {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.Center,
                        ) {
                            TextButton(onClick = viewModel::dismissDeleteConfirm) { Text(stringResource(R.string.common_cancel)) }
                            TextButton(onClick = viewModel::deleteSelected) {
                                Text(stringResource(R.string.common_delete), color = MylifeColors.Red)
                            }
                        }
                    },
                )
            }
        }
    }
}
