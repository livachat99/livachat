@file:OptIn(ExperimentalFoundationApi::class)

package dev.livachat.app.ui.screens.mylife.gallery

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.ui.screens.conversation.ImageViewerMath
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.util.localizeDigits

/** 查看器翻页触达已加载末尾的提前量（A2）。 */
private const val VIEWER_PREFETCH = 3

/**
 * 全屏帧查看器（docs/android_timeline_image_gallery.md §3.5，拷贝式复用
 * conversation 骨架 + 复用 `ImageViewerMath`）：黑底 + HorizontalPager + 单击退出 +
 * 捏合 1x–5x + 放大平移 + 顶部页码 + 底部单一 bar（caption + 保存图标）。
 * 覆盖层非路由（BackHandler 先关查看器）；页面内容为 URL 帧图（AsyncImage）。
 */
@Composable
fun FrameViewerOverlay(
    frames: List<FrameDto>,
    initialPage: Int,
    onDismiss: () -> Unit,
    /** 翻页接近已加载末尾时触发（Gallery 侧 loadMore，A2）。 */
    onNearEnd: () -> Unit,
    /** 保存到相册提示（null = 不显示）；文案与 2s TTL 由 VM 管理。 */
    saveTip: String? = null,
    /** 正在保存的帧 id（按钮转圈；连点由 VM 重入守卫吞掉）。 */
    savingFrameId: String? = null,
    /** 点击保存：参数为当前页帧 id（pager 在覆盖层内部，调用方无从得知 currentPage）。 */
    onSaveClick: (frameId: String) -> Unit = {},
) {
    if (frames.isEmpty()) return
    val pagerState = rememberPagerState(
        initialPage = initialPage.coerceIn(0, frames.size - 1),
    ) { frames.size }
    val locale = LocalConfiguration.current.locales[0]

    // 变换状态提升到 overlay 层：pager 的 userScrollEnabled 需要读 scale；
    // 翻页落定后重置，每张图独立从 fit 态开始（与 conversation 同款）
    var scale by remember { mutableFloatStateOf(ImageViewerMath.MIN_SCALE) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }
    // caption 展开态（点击底部文字块切换）：本次全屏会话级——点一次后翻页保持展开，
    // 覆盖层离场（退出全屏浏览）即随 remember 丢弃，重新进入回到默认收起
    var captionExpanded by remember { mutableStateOf(false) }

    LaunchedEffect(pagerState.settledPage) {
        scale = ImageViewerMath.MIN_SCALE
        offset = Offset.Zero
    }

    // 翻页触达已加载末尾 → 预取下一页（列表增长即页数增长，当前页索引不漂移）
    LaunchedEffect(pagerState.currentPage, frames.size) {
        if (pagerState.currentPage >= frames.size - VIEWER_PREFETCH) onNearEnd()
    }

    BackHandler(enabled = true) { onDismiss() }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .onSizeChanged { viewport = it },
    ) {
        HorizontalPager(
            state = pagerState,
            // 放大态框架层禁用翻页（第二道保险）；fit 态事件本就不被本页消费（第一道）
            userScrollEnabled = scale <= ImageViewerMath.MIN_SCALE,
            modifier = Modifier.fillMaxSize(),
        ) { page ->
            ZoomableFramePage(
                frame = frames[page],
                scale = scale,
                offset = offset,
                viewport = viewport,
                onTap = onDismiss,
                onGesture = { pan, zoom, centroid, fitSize ->
                    // 未加载完成/加载失败（fitSize == Size.Zero）时不做变换：scale 保持 1，
                    // userScrollEnabled 不翻转，空白页仍可翻页/单击退出
                    if (fitSize != Size.Zero) {
                        val oldScale = scale
                        val newScale = ImageViewerMath.clampScale(oldScale * zoom)
                        offset = ImageViewerMath
                            .offsetAfterZoom(offset, centroid, viewport, oldScale, newScale, pan)
                            .let { ImageViewerMath.clampOffset(it, fitSize, newScale, viewport) }
                        scale = newScale
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
        }

        val frame = frames.getOrNull(pagerState.currentPage)
        val caption = frame?.caption?.takeIf { it.isNotBlank() }
        // 收起态是否被单行截断 → 决定金色 ⌄ 提示与可点性（⌃ 表示可收起）；仅收起态测量
        var captionOverflow by remember(pagerState.currentPage) { mutableStateOf(false) }
        val captionToggleEnabled = caption != null && (captionOverflow || captionExpanded)

        // 顶部页码胶囊：仅 "n / N"（单帧无胶囊，对齐 D7）；时间全查看器不显示（2026-09-20 用户定）
        if (frames.size > 1) {
            Text(
                // 页码数字经 localizeDigits（ar 阿数，docs/android_multilingual.md §20 缺口⑤）
                text = localizeDigits("${pagerState.currentPage + 1} / ${frames.size}", locale),
                color = MylifeColors.Star,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .padding(top = Spacing.lg)
                    // 半透明黑底：白底帧图上文字否则不可读（background 不消费手势）
                    // alpha 0.35 = conversation 查看器保存按钮圆底同款（用户 2026-09-20 指定）
                    .background(Color.Black.copy(alpha = 0.35f), RoundedCornerShape(50))
                    .padding(horizontal = Spacing.md, vertical = Spacing.xs),
            )
        }

        // 底部 bar（单一组件：caption + 右侧保存图标，2026-09-20 定）：内容自适应宽度、居中；
        // 文本列可点切换展开/收起（会话级），图标列点保存——内层 clickable 先消费，互不误触；
        // bar 是 Pager 兄弟绘制其上 → 不误退出/不翻页；无 caption 时退化为圆形图标按钮
        Column(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(start = Spacing.lg, end = Spacing.lg, bottom = Spacing.lg),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            saveTip?.let { tip ->
                Surface(
                    color = Color.Black.copy(alpha = 0.75f),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Text(
                        text = tip,
                        color = MylifeColors.Star,
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
                    )
                }
                Spacer(modifier = Modifier.height(Spacing.sm))
            }
            if (frame != null) {
                Row(
                    modifier = Modifier
                        // bar 0.6（文字最密；35% 叠纯白帧图对比度仅 ~2:1，用户 2026-09-20 定）
                        .background(
                            Color.Black.copy(alpha = 0.6f),
                            if (caption == null) CircleShape else RoundedCornerShape(12.dp),
                        )
                        .clickable(enabled = captionToggleEnabled) { captionExpanded = !captionExpanded },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    if (caption != null) {
                        Row(
                            verticalAlignment = Alignment.Top,
                            modifier = Modifier
                                .padding(start = Spacing.md, top = Spacing.sm, bottom = Spacing.sm)
                                .weight(1f, fill = false),
                        ) {
                            Text(
                                text = caption,
                                color = MylifeColors.Star.copy(alpha = 0.85f),
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = if (captionExpanded) Int.MAX_VALUE else 1,
                                overflow = TextOverflow.Ellipsis,
                                textAlign = TextAlign.Center,
                                onTextLayout = { layout ->
                                    if (!captionExpanded) captionOverflow = layout.hasVisualOverflow
                                },
                                modifier = Modifier.weight(1f, fill = false),
                            )
                            if (captionToggleEnabled) {
                                Text(
                                    text = if (captionExpanded) "⌃" else "⌄",
                                    color = MylifeColors.Gold,
                                    style = MaterialTheme.typography.bodySmall,
                                    modifier = Modifier.padding(start = 6.dp),
                                )
                            }
                        }
                    }
                    // 保存到相册：48dp 触达、垂直居中、无内层圆底；保存中不置灰
                    // （禁用会让 tap 穿透到页 → 误退出），图标换转圈、连点由 VM 守卫吞掉
                    IconButton(
                        onClick = { onSaveClick(frame.frameId) },
                        modifier = Modifier.padding(end = Spacing.xs),
                    ) {
                        if (frame.frameId == savingFrameId) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(18.dp),
                                color = MylifeColors.Star,
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(Icons.Filled.Download, stringResource(R.string.common_save_image), tint = MylifeColors.Star)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ZoomableFramePage(
    frame: FrameDto,
    scale: Float,
    offset: Offset,
    viewport: IntSize,
    onTap: () -> Unit,
    onGesture: (pan: Offset, zoom: Float, centroid: Offset, fitSize: Size) -> Unit,
    modifier: Modifier = Modifier,
) {
    // 每页各自 remember intrinsic（滑回已加载页 painter 重建期间保留上次值，避免 fitSize 清零）；
    // scale/offset 由 settledPage 重置（overlay 层），与 conversation 语义一致
    var intrinsic by remember(frame.frameId) { mutableStateOf<IntSize?>(null) }
    val fitSize = remember(intrinsic, viewport) {
        intrinsic?.let { ImageViewerMath.fitSize(it.width, it.height, viewport) } ?: Size.Zero
    }
    val currentScale = rememberUpdatedState(scale)
    // fitSize 延迟读取：pointerInput 协程不因异步加载完成而重启，按值捕获会永久持空 fitSize
    val currentFitSize = rememberUpdatedState(fitSize)

    Box(
        modifier = modifier
            // 单击退出：检测面恒存在（未加载页也能退出/翻页）
            .pointerInput(Unit) { detectTapGestures { onTap() } }
            // 自定义检测器（对齐 conversation：不用 detectTransformGestures——后者越过 slop
            // 后无条件消费位移，fit 态单指横滑会被吃掉、Pager 永远翻不了页）
            .pointerInput(frame.frameId) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    var pastSlop = false
                    var accumPan = Offset.Zero
                    val slop = viewConfiguration.touchSlop
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        when {
                            // 双指：锚点缩放，立即应用 + 全量消费（确定性取消 tap 与 Pager 拖拽跟踪）
                            pressed >= 2 -> {
                                onGesture(
                                    event.calculatePan(), event.calculateZoom(),
                                    event.calculateCentroid(), currentFitSize.value,
                                )
                                event.changes.forEach { it.consume() }
                            }
                            // 单指放大态：平移立即应用；消费延迟到越过 touchSlop（单击可靠触发退出）
                            pressed == 1 && currentScale.value > ImageViewerMath.MIN_SCALE -> {
                                onGesture(event.calculatePan(), 1f, Offset.Zero, currentFitSize.value)
                                if (!pastSlop) {
                                    accumPan += event.calculatePan()
                                    if (accumPan.getDistance() > slop) pastSlop = true
                                }
                                if (pastSlop) {
                                    event.changes.forEach { if (it.positionChanged()) it.consume() }
                                }
                            }
                            // 单指 fit 态：整体不消费 → HorizontalPager 翻页 / 单击退出
                        }
                    } while (event.changes.any { it.pressed })
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        AsyncImage(
            model = frame.mediaUrl,
            contentDescription = null,
            contentScale = ContentScale.Fit,
            onSuccess = { state ->
                // Coil 2.x：SuccessResult.drawable 的 intrinsic 尺寸（px；
                // Drawable#getWidth 不在 android.jar 公开桩内，用 intrinsic）
                val drawable = state.result.drawable
                val size = IntSize(drawable.intrinsicWidth, drawable.intrinsicHeight)
                if (size != intrinsic) intrinsic = size
            },
            modifier = Modifier
                .fillMaxSize()
                .graphicsLayer {
                    scaleX = scale
                    scaleY = scale
                    translationX = offset.x
                    translationY = offset.y
                },
        )
    }
}
