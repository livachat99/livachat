package dev.livachat.app.ui.screens.mylife.hotslot

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.stringResource
import coil.compose.AsyncImage
import dev.livachat.app.R
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.ui.screens.conversation.ImageViewerMath
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.theme.Spacing

/** 图片层 testTag（仪器测试点击落点，§9.6）。 */
internal const val VIEWER_IMAGE_TAG = "hotSlotViewerImage"

/**
 * 单图全屏查看器（docs/android_timeline_hotslot.md §9.3；拷贝式复用 gallery
 * FrameViewerOverlay 去 pager 版 + 跨包复用 conversation `ImageViewerMath`）：
 * 黑底 + `ContentScale.Fit` + 单击退出（fit/放大态均）+ 捏合 1x–5x + 放大态单指平移 +
 * 底部单一 bar（caption 单行 ellipsis + ⌄ 展开、缺失退化圆形图标按钮、48dp 保存）+ BackHandler。
 * 覆盖层非路由；手势挂图片层 Box，bar 为其兄弟层绘制其上（bar 恒拦截点击，不误退出）。
 */
@Composable
fun HotSlotFrameViewer(
    frame: HotSlotEvent,
    onDismiss: () -> Unit,
    /** 保存提示（null = 不显示）；三文案与 2s TTL 由 VM 管理。 */
    saveTip: String? = null,
    /** 正在保存（按钮转圈；连点由 VM 重入守卫吞掉，不置灰）。 */
    saving: Boolean = false,
    /** 点击保存（保存对象 = VM 的 `viewerFrame`，无参数）。 */
    onSaveClick: () -> Unit = {},
) {
    var scale by remember { mutableFloatStateOf(ImageViewerMath.MIN_SCALE) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }
    // caption 展开态 = 本次全屏会话级：覆盖层离场（remember 丢弃）重进回到收起
    var captionExpanded by remember { mutableStateOf(false) }

    BackHandler(enabled = true) { onDismiss() }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .onSizeChanged { viewport = it },
    ) {
        ZoomableFrame(
            frame = frame,
            scale = scale,
            offset = offset,
            viewport = viewport,
            onTap = onDismiss,
            onGesture = { pan, zoom, centroid, fitSize ->
                // 未加载/加载失败（fitSize == Size.Zero）不做变换：空白页仍可单击退出
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

        val caption = frame.caption?.takeIf { it.isNotBlank() }
        // 收起态是否被单行截断 → 决定金色 ⌄ 与可点性（⌃ = 可收起）；仅收起态测量
        var captionOverflow by remember { mutableStateOf(false) }
        val captionToggleEnabled = caption != null && (captionOverflow || captionExpanded)

        // 底部 bar：clickable 挂整行（不可展开时 disabled 仍拦截点击 → bar 区域内恒不退出，
        // 与 gallery 同构）；保存图标为子节点先消费，互不误触；无 caption 退化圆形图标按钮
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
            Row(
                modifier = Modifier
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
                // 保存到相册：48dp 触达、垂直居中；保存中不置灰（与 gallery 一致），
                // 图标换转圈、连点由 VM 守卫吞掉
                IconButton(
                    onClick = onSaveClick,
                    modifier = Modifier.padding(end = Spacing.xs),
                ) {
                    if (saving) {
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

@Composable
private fun ZoomableFrame(
    frame: HotSlotEvent,
    scale: Float,
    offset: Offset,
    viewport: IntSize,
    onTap: () -> Unit,
    onGesture: (pan: Offset, zoom: Float, centroid: Offset, fitSize: Size) -> Unit,
    modifier: Modifier = Modifier,
) {
    var intrinsic by remember { mutableStateOf<IntSize?>(null) }
    val fitSize = remember(intrinsic, viewport) {
        intrinsic?.let { ImageViewerMath.fitSize(it.width, it.height, viewport) } ?: Size.Zero
    }
    val currentScale = rememberUpdatedState(scale)
    // fitSize 延迟读取：pointerInput 协程不因异步加载完成而重启，按值捕获会永久持空 fitSize
    val currentFitSize = rememberUpdatedState(fitSize)

    Box(
        modifier = modifier
            .testTag(VIEWER_IMAGE_TAG)
            // 单击退出：检测面恒存在（未加载/加载失败也能退出）
            .pointerInput(Unit) { detectTapGestures { onTap() } }
            // 自定义检测器（对齐 gallery：不用 detectTransformGestures——后者越过 slop 后
            // 无条件消费位移，单击会被吃掉）；本屏无 Pager，fit 态单指分支不消费即无效果
            .pointerInput(Unit) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    var pastSlop = false
                    var accumPan = Offset.Zero
                    val slop = viewConfiguration.touchSlop
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        when {
                            // 双指：锚点缩放，立即应用 + 全量消费（确定性取消 tap 跟踪）
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
                            // 单指 fit 态：整体不消费 → 单击退出（本屏无翻页，拖动无效果）
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
