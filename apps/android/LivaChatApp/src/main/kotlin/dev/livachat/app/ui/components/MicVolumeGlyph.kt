package dev.livachat.app.ui.components

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.semantics.SemanticsPropertyKey
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.testTag
import androidx.compose.ui.unit.dp
import dev.livachat.app.media.MicLevelMeter
import dev.livachat.app.ui.theme.BrandIndigo
import dev.livachat.app.ui.theme.SurfaceVariant
import kotlinx.coroutines.flow.StateFlow

/** 点亮段数语义键——仅供测试断言（docs/android_mic_meter.md §5.4），不进 a11y 播报。 */
val LevelKey = SemanticsPropertyKey<Int>("MicVolumeGlyphLevel")

// ── 话筒下半（底座）──
// 与标准 Material mic 图标同源的 bell path（24 视口坐标）——mic 开启时底座
// 与 MicOff（关闭）下半部分形状一致，仅颜色不同（用户 v2.1 反馈）。
private const val MIC_BELL_PATH =
    "M17.3,11c0,3 -2.54,5.1 -5.3,5.1S6.7,14 6.7,11L5,11c0,3.41 2.72,6.23 6,6.72" +
        "L11,21h2v-3.28c3.28,-0.48 6,-3.3 6,-6.72h-1.7z"

// ── 话筒头 ──
// 外圈 = 圆角胶囊描边（包住 4 段横线）；4 段横线自下而上点亮。
// 话筒头整体上移、底座下移，与下半底座之间留出可见间隙（v2.4 真机反馈）。
// 几何为 24dp 视口常量（真机观感微调点）。
private const val RING_LEFT = 7.8f
private const val RING_RIGHT = 16.2f
private const val RING_TOP = 0.5f
private const val RING_BOTTOM = 13.6f
private const val RING_CORNER_RADIUS = 2.5f
private const val RING_THICKNESS = 1.7f
// 4 段横线均分话筒头内部（真机比较后定稿 4 条）：段高 1.9、间隙 0.9、起于 2.2
private const val BAR_START_Y = 2.2f
private const val BAR_HEIGHT = 1.9f
private const val BAR_GAP = 0.9f
private const val BAR_WIDTH = 5.8f
private const val BAR_CORNER_RADIUS = 0.8f

// ── 话筒下半（底座）──
// material mic bell path 原样填充（y 11..20.7）并整体下移 [BELL_DY]：
// 两侧支架与中心竖线一体成型，支架不再顶到话筒头；竖线末端与延伸段衔接。
private const val BELL_DY = 0.9f
private const val STEM_TOP_Y = 21.4f
private const val STEM_BOTTOM_Y = 23.1f
private const val STEM_WIDTH = 1.7f

/**
 * 融入音量表的麦克风图标（docs/android_mic_meter.md §5，v2.7）。
 * mic 开启时：
 * - 话筒头 = 话筒外圈（胶囊描边）+ [MicLevelMeter.LEVELS]（=4）条等大横线，
 *   自下而上点亮（低音量只亮最底一条，满格≈实心头）；
 * - 话筒下半 = material mic 底座 path 原样填充并整体下移（与话筒头间留间隙；
 *   两侧支架+杯底+中心竖线一体成型，与 MicOff 下半同源、仅颜色不同）
 *   + 竖线末端向下延伸段（底座/外圈/点亮段 = 主色，未点亮段 = SurfaceVariant）。
 *
 * 调用方传入 [flow]，collect 在本组件函数体内完成——重组范围=图标自身
 * （§2.4 叶子 collect）。电平属高频装饰信息：root clearAndSetSemantics
 * 仅保留 testTag 与 [LevelKey]，TalkBack 不播报电平。
 */
@Composable
fun MicVolumeGlyph(
    flow: StateFlow<Int>,
    modifier: Modifier = Modifier,
) {
    val level by flow.collectAsState()
    val barCount = MicLevelMeter.LEVELS
    val lit = level.coerceIn(0, barCount)
    val litColor = BrandIndigo
    val dimColor = SurfaceVariant
    // 段数恒定，循环内直调 composable 合法（按位置 remember）；动画当前值按索引
    // 就地写入预分配数组，避免每次重组新建 List（语音活跃期 ~60 次/秒的小分配）
    val barColors = remember { Array(barCount) { dimColor } }
    for (i in 0 until barCount) {
        val color by animateColorAsState(
            targetValue = if (i >= barCount - lit) litColor else dimColor,
            animationSpec = spring(dampingRatio = 0.5f),
            label = "glyphBar$i",
        )
        barColors[i] = color
    }
    // material mic 底座 path（解析一次；24 视口坐标，绘制时随密度缩放）
    val bellPath = remember {
        PathParser().parsePathString(MIC_BELL_PATH).toPath()
    }

    Canvas(
        modifier = modifier
            .size(24.dp)
            .clearAndSetSemantics {
                testTag = "micVolumeGlyph"
                set(LevelKey, lit)
            },
    ) {
        // 全图以 dp 为单位绘制：先整体缩放，path/圆角矩形尺寸即常量本身
        drawContext.canvas.save()
        drawContext.canvas.scale(density, density)
        // 1) 话筒下半底座：material mic bell path 整体下移 BELL_DY 后填充
        drawContext.canvas.save()
        drawContext.canvas.translate(0f, BELL_DY * density)
        drawPath(path = bellPath, color = litColor)
        drawContext.canvas.restore()
        // 中心竖线向下延伸（与 bell 底端衔接成一条长竖线）
        drawRoundRect(
            color = litColor,
            topLeft = Offset(12f - STEM_WIDTH / 2, STEM_TOP_Y),
            size = Size(STEM_WIDTH, STEM_BOTTOM_Y - STEM_TOP_Y),
            cornerRadius = CornerRadius(STEM_WIDTH / 2),
        )
        // 2) 话筒外圈：包住 4 段横线的圆角胶囊描边（与底座之间留出间隙）
        drawRoundRect(
            color = litColor,
            topLeft = Offset(RING_LEFT, RING_TOP),
            size = Size(RING_RIGHT - RING_LEFT, RING_BOTTOM - RING_TOP),
            cornerRadius = CornerRadius(RING_CORNER_RADIUS, RING_CORNER_RADIUS),
            style = Stroke(width = RING_THICKNESS),
        )
        // 3) 话筒头 4 段横线：自下而上点亮（center=12dp）
        for (i in 0 until barCount) {
            val y = BAR_START_Y + i * (BAR_HEIGHT + BAR_GAP)
            drawRoundRect(
                color = barColors[i],
                topLeft = Offset(12f - BAR_WIDTH / 2, y),
                size = Size(BAR_WIDTH, BAR_HEIGHT),
                cornerRadius = CornerRadius(BAR_CORNER_RADIUS, BAR_CORNER_RADIUS),
            )
        }
        drawContext.canvas.restore()
    }
}
