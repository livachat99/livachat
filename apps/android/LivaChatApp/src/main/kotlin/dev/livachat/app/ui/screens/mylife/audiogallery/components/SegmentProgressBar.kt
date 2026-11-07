package dev.livachat.app.ui.screens.mylife.audiogallery.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import dev.livachat.app.data.remote.mylife.dto.EnergyEnvelope
import dev.livachat.app.ui.screens.mylife.MylifeColors
import dev.livachat.app.ui.screens.mylife.audiogallery.envelopeBars
import kotlin.math.abs

/** 拖动进度条 testTag（仪器/渲染测试定位用）。 */
internal const val PROGRESS_BAR_TAG = "audioProgressBar"

/**
 * 可拖动进度条（docs/android_timeline_audio_gallery.md §4.4/§4.5）：
 * 绘制轨 4dp（`Line` 底 + `Gold` 已播）+ 7dp `Gold` 滑块；**触控带 = 整行高**（调用方给 ≥32dp）。
 *
 * 手势（单一 `awaitEachGesture`，避免多 detector 抢事件）：
 * - 横向位移超过 touch slop 才 `consume` 抢下拖动 → `onScrub`；纵向留给 `LazyColumn` 滚动；
 * - 父级已消费（列表滚动接管）→ 本轮作废、**不 seek**；
 * - 松手 / 点击（无位移）= `onScrubEnd`（拖动下发 seek、点击定位，Q9）。
 *
 * `interactive = false`（非当前播放卡）时不挂手势：seek 只作用于**正在播放的那一段**，
 * 非活动卡的轨道拖动会把位置错发给活动段（全局单播下的边界）。
 */
@Composable
internal fun SegmentProgressBar(
    fraction: Float,
    durationMs: Long,
    onScrub: (Long) -> Unit,
    onScrubEnd: (Long) -> Unit,
    modifier: Modifier = Modifier,
    interactive: Boolean = true,
    envelope: EnergyEnvelope? = null,
) {
    var widthPx by remember { mutableFloatStateOf(1f) }
    // pointerInput(Unit) 的手势协程只建一次：duration 与两个回调都必须经 rememberUpdatedState 取最新值，
    // 否则重组传入新 lambda 后手势里仍调首次组合的闭包（陈旧捕获，静默失效）
    val duration by rememberUpdatedState(durationMs)
    val currentOnScrub by rememberUpdatedState(onScrub)
    val currentOnScrubEnd by rememberUpdatedState(onScrubEnd)

    fun msAt(x: Float): Long {
        val d = duration
        return if (widthPx <= 0f || d <= 0L) 0L
        else ((x / widthPx) * d).toLong().coerceIn(0L, d)
    }

    val gesture = if (interactive && durationMs > 0L) {
        Modifier.pointerInput(Unit) {
            awaitEachGesture {
                val down = awaitFirstDown(requireUnconsumed = false)
                val startX = down.position.x
                var lastX = startX
                var dragging = false
                var takenOver = false
                while (true) {
                    val event = awaitPointerEvent()
                    val change = event.changes.firstOrNull { it.id == down.id } ?: break
                    if (change.isConsumed) {   // 父级（列表滚动）接管 → 本轮作废
                        takenOver = true
                        break
                    }
                    if (!change.pressed) break
                    lastX = change.position.x
                    if (!dragging && abs(lastX - startX) > viewConfiguration.touchSlop) {
                        dragging = true
                    }
                    if (dragging) {
                        change.consume()
                        currentOnScrub(msAt(lastX))
                    }
                }
                if (!takenOver) currentOnScrubEnd(msAt(lastX))
            }
        }
    } else {
        Modifier
    }

    // 包络柱（§10.4 定案 A 镜像柱状）：按可用宽度定条数、按 (包络, 宽度) 缓存，避免每帧重算
    val slotPx = with(LocalDensity.current) { BAR_SLOT_DP.dp.toPx() }
    val bars = remember(envelope, widthPx, slotPx) {
        envelope?.let {
            envelopeBars(it, (widthPx / slotPx).toInt().coerceIn(1, MAX_BARS))
        } ?: FloatArray(0)
    }

    Box(
        modifier = modifier
            .testTag(PROGRESS_BAR_TAG)
            .onSizeChanged { widthPx = it.width.toFloat().coerceAtLeast(1f) }
            .then(gesture),
    ) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val cy = size.height / 2f
            val f = fraction.coerceIn(0f, 1f)
            if (bars.isNotEmpty()) {
                // A 镜像柱状：中线对称的圆角柱，已播 Gold / 未播 Line（**不画中心轨道线**，
                // 中线由柱体本身构成——§10.4 微调①）
                val slot = size.width / bars.size
                val barW = slot * BAR_WIDTH_RATIO
                val half = size.height / 2f - 1.dp.toPx()
                val playedBars = bars.size * f
                val shape = CornerRadius(barW / 3f, barW / 3f)
                bars.forEachIndexed { i, h ->
                    val y = h * half
                    if (y <= 0.5f) return@forEachIndexed
                    drawRoundRect(
                        color = if (i < playedBars) MylifeColors.Gold else MylifeColors.Line,
                        topLeft = Offset(i * slot + (slot - barW) / 2f, cy - y),
                        size = Size(barW, y * 2f),
                        cornerRadius = shape,
                    )
                }
            } else {
                // 降级（无能量数据）：现状纯轨道
                val trackH = 4.dp.toPx()
                val r = trackH / 2f
                drawRoundRect(
                    color = MylifeColors.Line,
                    topLeft = Offset(0f, cy - r),
                    size = Size(size.width, trackH),
                    cornerRadius = CornerRadius(r, r),
                )
                if (f > 0f) {
                    drawRoundRect(
                        color = MylifeColors.Gold,
                        topLeft = Offset(0f, cy - r),
                        size = Size(size.width * f, trackH),
                        cornerRadius = CornerRadius(r, r),
                    )
                }
            }
            // 滑块：A 形态下柱体上下包围中线 → 加 Abyss 描边保证边界清晰（§10.4 微调②）
            val thumbR = 7.dp.toPx()
            drawCircle(
                color = MylifeColors.Abyss,
                radius = thumbR + THUMB_OUTLINE_DP.dp.toPx(),
                center = Offset(size.width * f, cy),
            )
            drawCircle(
                color = MylifeColors.Gold,
                radius = thumbR,
                center = Offset(size.width * f, cy),
            )
        }
    }
}

/** 柱宽槽（§10.4：约 1.5 dp 一条）与柱体占槽比例（定案图 ≈62%）。 */
private const val BAR_SLOT_DP = 1.5f
private const val BAR_WIDTH_RATIO = 0.62f
private const val THUMB_OUTLINE_DP = 1.5f
private const val MAX_BARS = 512
