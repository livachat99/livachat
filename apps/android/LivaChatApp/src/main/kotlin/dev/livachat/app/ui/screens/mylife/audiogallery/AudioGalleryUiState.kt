package dev.livachat.app.ui.screens.mylife.audiogallery

import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import dev.livachat.app.data.remote.mylife.dto.EnergyEnvelope
import dev.livachat.app.media.AudioPlayer
import dev.livachat.app.ui.util.localizeDigits
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** 段正文取转写的最小字数（不含此数则回退摘要；§4.4）。 */
internal const val MIN_TRANSCRIPT_CHARS = 5

private val TIME_FORMAT = DateTimeFormatter.ofPattern("HH:mm")

/**
 * 每段的时间标签（§4.3，纯函数可单测）：**每个 segment 都有自己的 `HH:mm`**，与列表一一对应；
 * 顺序即输入顺序（Repository 出口已按 `tsMs` 升序保证）。2026-09-24 由「桶变化才出标签」改为逐段。
 * 数字经 [localizeDigits]（ar 阿数，§20 缺口⑤）。
 */
internal fun segmentTimeLabels(
    segments: List<AudioSegment>,
    zone: ZoneId = ZoneId.systemDefault(),
    locale: Locale,
): List<String> = segments.map { s ->
    localizeDigits(TIME_FORMAT.format(Instant.ofEpochMilli(s.tsMs).atZone(zone).toLocalTime()), locale)
}

/**
 * 段正文（§4.4，2026-09-24 定）：**单区**——转写字数 > [MIN_TRANSCRIPT_CHARS] 显示转写，
 * 否则显示摘要（短转写/空转写如纯 ASMR 段回退）；两者皆无 → null（不渲染正文区）。
 */
internal fun segmentBodyText(transcript: String?, summary: String?): String? =
    transcript?.takeIf { it.length > MIN_TRANSCRIPT_CHARS } ?: summary

/** 时码 `M:SS`（播放条右侧；负值/未知按 0 处理）。 */
internal fun formatTimecode(ms: Long): String {
    val total = (ms.coerceAtLeast(0L)) / 1000
    return "%d:%02d".format(total / 60, total % 60)
}

/** 归一化窗口：段峰值往下 ENERGY_FLOOR_DB 作为最低可见高度（§10.4）。 */
internal const val ENERGY_FLOOR_DB = 40f

/**
 * 能量包络 → B 根柱的显示高度（0..1，§10.4）：
 * 第 b 根取对应帧区间的 **max**（保起音），再按段峰值归一，floor = max(峰值 − [ENERGY_FLOOR_DB], dbMin)
 * （绝对下界保证平直段不给满高、安静段让真静音贴底；§10.10）。
 * **不假设 `values.size == durationMs/20`**——索引按比例映射并 clamp（实测 60s 段为 2999 帧）。
 * 空包络 / barCount ≤ 0 / 全柱为 0 → 空数组（调用方据此降级为纯轨道）。
 */
internal fun envelopeBars(envelope: EnergyEnvelope, barCount: Int): FloatArray {
    val n = envelope.values.size
    if (n == 0 || barCount <= 0) return FloatArray(0)
    val span = (envelope.dbMax - envelope.dbMin).coerceAtLeast(1e-3f)
    fun dbAt(index: Int): Float {
        val v = envelope.values[index.coerceIn(0, n - 1)].toInt() and 0xFF
        return envelope.dbMin + v / 255f * span
    }
    val peakPerBar = FloatArray(barCount) { b ->
        val from = (b.toLong() * n / barCount).toInt()
        val to = (((b + 1).toLong() * n / barCount).toInt() - 1).coerceAtLeast(from)
        var m = Float.NEGATIVE_INFINITY
        for (i in from..to.coerceAtMost(n - 1)) m = maxOf(m, dbAt(i))
        m
    }
    val peak = peakPerBar.max()
    // floor 取「相对（峰值 − 40 dB）」与「绝对（量化下界 dbMin = −70 dBFS）」的**高者**：
    // 正常段（帧峰值 ≥ −30 dBFS，实测 50/55 段）二者等价、渲染不变；安静段（帧峰值更低）
    // 让真静音贴底而不是悬在半高；平直段（全帧 = dbMin）全柱归 0 ⇒ 由下方守卫降级为纯轨道。
    val floor = maxOf(peak - ENERGY_FLOOR_DB, envelope.dbMin)
    val span2 = (peak - floor).coerceAtLeast(1e-3f)
    val heights = FloatArray(barCount) { b -> ((peakPerBar[b] - floor) / span2).coerceIn(0f, 1f) }
    // 全零（无可展示动态范围，如平直/极安静段）→ 返回空数组：调用方降级为纯轨道，
    // 而不是渲染出一条空白带或误导性的满高柱（§10.10）
    return if (heights.all { it <= 0f }) FloatArray(0) else heights
}

/** AudioGallery 数据态（docs/android_timeline_audio_gallery.md §4.7）。 */
sealed interface AudioGalleryState {
    data object Loading : AudioGalleryState
    data object Empty : AudioGalleryState
    data class Error(val message: String) : AudioGalleryState

    /** total = 服务端计数；segments 只增不减（追加式，按 eventRef 去重）。 */
    data class Data(
        val segments: List<AudioSegment>,
        val total: Int,
        val nextCursor: String?,
        val loadingMore: Boolean = false,
    ) : AudioGalleryState
}

data class AudioGalleryUiState(
    val state: AudioGalleryState = AudioGalleryState.Loading,
    /** 顶栏段数：首屏成功前 = 路由段数快照，成功后 = 响应 total（§4.7）。 */
    val headerCount: Int = 0,
    /** 路由快照 pending（顶栏「分析中…」），随 overview 轮询单向收敛（§4.7）。 */
    val pending: Boolean = false,
    /** 播放器全局态（Q8 单播）：`player.key == 本卡 eventRef` 才是「正在播放的那张」。 */
    val player: AudioPlayer.State = AudioPlayer.State(),
    /** 拖动中的本地 scrub 位置（非空 = 拖动中，位置回写被抑制；§4.5 E7）。 */
    val scrubMs: Long? = null,
    /** 正在拖动的是哪一段（eventRef）：只有该卡的滑块跟随 scrub 位置（§4.5 修订）。 */
    val scrubRef: String? = null,
)
