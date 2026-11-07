@file:OptIn(ExperimentalFoundationApi::class)

package dev.livachat.app.ui.screens.conversation

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.gestures.calculateCentroid
import androidx.compose.foundation.gestures.calculatePan
import androidx.compose.foundation.gestures.calculateZoom
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Surface
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.positionChanged
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import dev.livachat.app.R
import dev.livachat.app.domain.model.ChatMessage
import dev.livachat.app.ui.components.decodeAiImageBitmap
import dev.livachat.app.ui.theme.LivaChatTypography
import dev.livachat.app.ui.theme.Spacing
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.util.localizeDigits
import dev.livachat.sdk.model.AiImage

/**
 * 全屏图片查看器覆盖层：捏合锚点缩放（1x..5x）/ 放大态拖动平移 / 单击退出 /
 * fit 态左右滑动翻页。手势仲裁见 [ZoomableImagePage]。
 */
@Composable
fun ImageViewerOverlay(
    /** 当前会话全部带图 AI 消息（会话顺序；image 必非 null）。 */
    images: List<ChatMessage>,
    /** 入口消息在 images 中的下标（调用方已保证命中）。 */
    initialPage: Int,
    onDismiss: () -> Unit,
    /** 保存提示文案（非 null 时底部胶囊显示，TTL 由调用方管理）。 */
    saveTip: String? = null,
    /** 点击保存按钮：参数为当前页消息 id（pagerState 在覆盖层内部，调用方无从得知）。 */
    onSaveClick: (messageId: String) -> Unit = {},
) {
    val pagerState = rememberPagerState(initialPage = initialPage) { images.size }
    val locale = LocalConfiguration.current.locales[0]

    // 变换状态提升到 overlay 层：pager 的 userScrollEnabled 需要读 scale；
    // 翻页落定后重置，每张图独立从 fit 态开始
    var scale by remember { mutableFloatStateOf(ImageViewerMath.MIN_SCALE) }
    var offset by remember { mutableStateOf(Offset.Zero) }
    var viewport by remember { mutableStateOf(IntSize.Zero) }

    LaunchedEffect(pagerState.settledPage) {
        scale = ImageViewerMath.MIN_SCALE
        offset = Offset.Zero
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
            ZoomableImagePage(
                image = images[page].image!!,
                scale = scale,
                offset = offset,
                viewport = viewport,
                onTap = onDismiss,
                onGesture = { pan, zoom, centroid, fitSize ->
                    // 该页解码失败（fitSize == Size.Zero）时不做变换：scale 保持 1，
                    // 从而 userScrollEnabled 不翻转，空白页仍可翻页/单击退出
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

        if (images.size > 1) {
            Text(
                // 页码数字经 localizeDigits（ar 阿数，docs/android_multilingual.md §20 缺口⑤）
                text = localizeDigits("${pagerState.currentPage + 1} / ${images.size}", locale),
                color = TextPrimary,
                style = LivaChatTypography.labelLarge,
                modifier = Modifier
                    .align(Alignment.TopCenter)
                    .statusBarsPadding()
                    .padding(top = Spacing.lg)
                    // 半透明黑底：白底图片上文字否则不可读（对齐 gallery 查看器 2026-09-20；
                    // background 纯绘制不消费手势，单击退出/翻页不受影响）
                    // alpha 0.35 = 本页保存按钮圆底同款（用户 2026-09-20 指定）
                    .background(Color.Black.copy(alpha = 0.35f), RoundedCornerShape(50))
                    .padding(horizontal = Spacing.md, vertical = Spacing.xs),
            )
        }

        // 保存当前页到相册：按钮是 Pager 的兄弟且绘制在其上，Main pass 先派发 →
        // 按钮消费 down → 页内 detectTapGestures 取消，点按钮不会误退出查看器
        IconButton(
            onClick = { onSaveClick(images[pagerState.currentPage].id) },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .navigationBarsPadding()
                .padding(Spacing.lg)
                .background(Color.Black.copy(alpha = 0.35f), CircleShape),
        ) {
            Icon(
                imageVector = Icons.Filled.Download,
                contentDescription = stringResource(R.string.common_save_image),
                tint = TextPrimary,
            )
        }

        if (saveTip != null) {
            Surface(
                color = Color.Black.copy(alpha = 0.75f),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .navigationBarsPadding()
                    .padding(bottom = Spacing.lg),
            ) {
                Text(
                    text = saveTip,
                    color = TextPrimary,
                    style = LivaChatTypography.labelLarge,
                    modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.sm),
                )
            }
        }
    }
}

@Composable
private fun ZoomableImagePage(
    image: AiImage,
    scale: Float,
    offset: Offset,
    viewport: IntSize,
    onTap: () -> Unit,
    onGesture: (pan: Offset, zoom: Float, centroid: Offset, fitSize: Size) -> Unit,
    modifier: Modifier = Modifier,
) {
    // 解码失败不早退：手势面（tap/手势检测器）必须始终存在，否则该页无法单击退出
    val bitmap = remember(image.data, image.orientation) { decodeAiImageBitmap(image) }
    val imageBitmap = remember(bitmap) { bitmap?.asImageBitmap() }
    val fitSize = remember(imageBitmap, viewport) {
        if (imageBitmap == null) Size.Zero
        else ImageViewerMath.fitSize(imageBitmap.width, imageBitmap.height, viewport)
    }
    val currentScale = rememberUpdatedState(scale)
    // fitSize 必须同样延迟读取：pointerInput 协程仅在 imageBitmap 变化时重启，而首帧组合时
    // viewport 尚未布局（fitSize == Size.Zero），按值捕获会让手势检测器永久持陈旧值
    val currentFitSize = rememberUpdatedState(fitSize)

    Box(
        modifier = modifier
            // 单击退出：未提供 onDoubleTap 时 tap 在抬手即触发，无双击等待延迟
            .pointerInput(Unit) { detectTapGestures { onTap() } }
            // 自定义检测器（不用 detectTransformGestures——后者越过 slop 后无条件消费一切
            // 位移，fit 态单指横滑会被吃掉、Pager 永远翻不了页）
            .pointerInput(imageBitmap) {
                awaitEachGesture {
                    awaitFirstDown(requireUnconsumed = false)
                    var pastSlop = false
                    var accumPan = Offset.Zero
                    val slop = viewConfiguration.touchSlop
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.count { it.pressed }
                        when {
                            // 双指：锚点缩放，立即应用 + 全量消费（含落指帧）——
                            // 确定性取消 tap（多指非单击）与 Pager 拖拽跟踪，避免双指滚动泄漏成翻页抖动
                            pressed >= 2 -> {
                                onGesture(
                                    event.calculatePan(), event.calculateZoom(),
                                    event.calculateCentroid(), currentFitSize.value,
                                )
                                event.changes.forEach { it.consume() }
                            }
                            // 单指放大态：平移立即应用；消费延迟到越过 touchSlop——
                            // 单击的亚像素噪声不被消费，tap 检测在放大态依然可靠触发退出
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
        if (imageBitmap != null) {
            Image(
                bitmap = imageBitmap,
                contentDescription = stringResource(R.string.chat_ai_image),
                contentScale = ContentScale.Fit,
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
}

/** 缩放/平移纯函数（JVM 可测）。offset 语义：内容中心相对视口中心的屏幕像素位移。 */
internal object ImageViewerMath {
    /** 下限 = fit：不允许缩到比「完整看到整张图」更小，缩到底经 clampOffset 归位清零。 */
    const val MIN_SCALE = 1f

    /** 上限：快照为低分辨率 JPEG（900ms 节流 + q70），5x 上采样已接近可辨上限。 */
    const val MAX_SCALE = 5f

    /** 图片在视口内 ContentScale.Fit 的显示尺寸（px）；未布局/非法输入退化为 Size.Zero。 */
    fun fitSize(imgW: Int, imgH: Int, viewport: IntSize): Size {
        if (imgW <= 0 || imgH <= 0 || viewport.width <= 0 || viewport.height <= 0) return Size.Zero
        val fit = minOf(
            viewport.width.toFloat() / imgW,
            viewport.height.toFloat() / imgH,
        )
        return Size(imgW * fit, imgH * fit)
    }

    fun clampScale(scale: Float): Float = scale.coerceIn(MIN_SCALE, MAX_SCALE)

    /**
     * 双指中心锚点缩放 + 平移：
     * v = centroid - 视口中心；r = newScale / oldScale；
     * offset' = v * (1 - r) + offset * r + pan
     * 性质：centroid 处的内容点在缩放前后屏幕位置不变。
     */
    fun offsetAfterZoom(
        offset: Offset,
        centroid: Offset,
        viewport: IntSize,
        oldScale: Float,
        newScale: Float,
        pan: Offset,
    ): Offset {
        if (oldScale <= 0f) return offset + pan
        val center = Offset(viewport.width / 2f, viewport.height / 2f)
        val v = centroid - center
        val r = newScale / oldScale
        return v * (1f - r) + offset * r + pan
    }

    /**
     * 平移夹取：displayed = fitSize * scale；maxX = max(0, (displayed.w - viewport.w) / 2)，同 Y。
     * scale == 1 时 displayed == fitSize ≤ viewport → offset 恒为 0（缩到底自动归位居中）。
     */
    fun clampOffset(offset: Offset, fitSize: Size, scale: Float, viewport: IntSize): Offset {
        val maxX = maxOf(0f, (fitSize.width * scale - viewport.width) / 2f)
        val maxY = maxOf(0f, (fitSize.height * scale - viewport.height) / 2f)
        // -maxX 在 maxX == 0f 时是 -0.0f，与 +0.0f 位模式不同（Offset 相等性是位级）→ 归一化
        val x = offset.x.coerceIn(-maxX, maxX).let { if (it == 0f) 0f else it }
        val y = offset.y.coerceIn(-maxY, maxY).let { if (it == 0f) 0f else it }
        return Offset(x, y)
    }
}
