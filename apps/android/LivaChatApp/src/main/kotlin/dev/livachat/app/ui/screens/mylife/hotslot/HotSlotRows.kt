package dev.livachat.app.ui.screens.mylife.hotslot

import dev.livachat.app.data.remote.mylife.dto.EventType
import dev.livachat.app.ui.screens.mylife.timeline.monthDayText
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.ui.util.localizeDigits
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** 文本行角色白名单（Q10）：仅 user/assistant 可渲染，其余 role 整行丢弃。 */
private const val ROLE_USER = "user"
private const val ROLE_ASSISTANT = "assistant"

/** 分钟桶（§10.3 ㊲：IANA 偏移均为整分钟，故 UTC 桶与设备本地分钟边界重合）。 */
private const val MINUTE_MS = 60_000L

private val TIME_FORMAT = DateTimeFormatter.ofPattern("HH:mm")

/** 气泡方向（§10.3 D4=A/㉞）：显式三态，展示字面量（「Agent任务」）归组件持有。 */
enum class BubbleSide { USER, AI, AGENT }

/**
 * LazyColumn 行模型（docs/android_timeline_hotslot.md §10.3）：文本行 / 帧行（≤3 张）。
 * `timeLabel` = 分钟桶首行的 `HH:mm`（同分钟其余行为 null，需求 2）；`tsMs` 仅锚点定位用。
 */
sealed interface HotSlotRow {
    val key: String
    val timeLabel: String?

    /** 气泡行：transcribe 正文带 (转录)、translate 带 (原文)/(译文) 后缀。 */
    data class Text(
        override val key: String,
        override val timeLabel: String?,
        val tsMs: Long,
        val side: BubbleSide,
        val body: String,
    ) : HotSlotRow

    /** 连续帧行（≤3 张；末行不足 3 张由组件等宽占位）。 */
    data class Frames(
        override val key: String,
        override val timeLabel: String?,
        val frames: List<HotSlotEvent>,
    ) : HotSlotRow
}

/**
 * 事件流 → 行列表（纯函数，可单测）：连续 video 事件（中间无文本行插入）按 ts 升序
 * `chunked(3)` 每行 3 张；文本行按类型/role 派生 side 与后缀，白名单外 role 不渲染（Q10）。
 * 分钟标签只随**已产出**行的分钟桶变化出现（丢弃行不消耗标签）；帧 chunk 以首帧定桶。
 * 输入需已按 ts 升序（Repository 出口保证）。
 */
internal fun buildHotSlotRows(
    events: List<HotSlotEvent>,
    locale: Locale,
    transcriptSuffix: String,
    originalSuffix: String,
    translationSuffix: String,
): List<HotSlotRow> {
    val rows = ArrayList<HotSlotRow>()
    val frames = ArrayList<HotSlotEvent>()
    var lastMinute: Long? = null

    /** 该时刻是否需要出分钟标签（并推进桶游标）。 */
    fun labelFor(tsMs: Long): String? {
        val bucket = tsMs / MINUTE_MS
        if (bucket == lastMinute) return null
        lastMinute = bucket
        return formatRowTime(tsMs, locale)
    }

    fun flushFrames() {
        if (frames.isEmpty()) return
        frames.chunked(3).forEach { chunk ->
            val head = chunk.first()
            rows.add(
                HotSlotRow.Frames(
                    key = "f-${head.eventRef ?: head.tsMs}",
                    timeLabel = labelFor(head.tsMs),
                    frames = chunk,
                ),
            )
        }
        frames.clear()
    }

    for (event in events) {
        if (event.type == EventType.VIDEO) {
            frames.add(event)
            continue
        }
        // 非帧事件打断连续帧分组（即使该行因白名单被丢弃）
        flushFrames()
        val text = event.text ?: continue
        val content = when (event.type) {
            EventType.CHAT -> event.sideOfRole()?.let { it to text }

            EventType.TRANSCRIBE -> event.sideOfRole()?.let { it to "$text $transcriptSuffix" }

            EventType.TRANSLATE -> event.sideOfRole()?.let {
                val suffix = if (event.role == ROLE_USER) " $originalSuffix" else " $translationSuffix"
                it to "$text$suffix"
            }

            EventType.AGENT_JOB -> BubbleSide.AGENT to text

            else -> null
        }
        if (content != null) {
            val (side, body) = content
            rows.add(HotSlotRow.Text(keyFor(event), labelFor(event.tsMs), event.tsMs, side, body))
        }
    }
    flushFrames()
    return rows
}

private fun HotSlotEvent.sideOfRole(): BubbleSide? = when (role) {
    ROLE_USER -> BubbleSide.USER
    ROLE_ASSISTANT -> BubbleSide.AI
    else -> null
}

private fun keyFor(event: HotSlotEvent): String = "t-${event.eventRef ?: event.tsMs}"

/**
 * 锚点行解析（docs/android_timeline_topics.md §3.3.3）：含 anchorTs 的行优先（帧行任意帧命中即整行）；
 * 锚点被口径剔除时退化为首个「行尾 ts ≥ anchorTs」的行（事件 ts 升序 → 该行覆盖锚点及其之后）；
 * 仍无 → -1（不滚动）。行尾：Text 即其 ts，Frames 为末帧 ts（chunk 内升序）。
 */
internal fun anchorRowIndex(rows: List<HotSlotRow>, anchorTs: Long): Int {
    val exact = rows.indexOfFirst { row ->
        when (row) {
            is HotSlotRow.Text -> row.tsMs == anchorTs
            is HotSlotRow.Frames -> row.frames.any { it.tsMs == anchorTs }
        }
    }
    if (exact >= 0) return exact
    return rows.indexOfFirst { row ->
        when (row) {
            is HotSlotRow.Text -> row.tsMs >= anchorTs
            is HotSlotRow.Frames -> (row.frames.lastOrNull()?.tsMs ?: Long.MIN_VALUE) >= anchorTs
        }
    }
}

/** 设备本地时区 `HH:mm`（与 chip 同口径）；数字经 [localizeDigits]（ar 阿数，§20 缺口⑤）。 */
internal fun formatRowTime(tsMs: Long, locale: Locale): String =
    localizeDigits(
        TIME_FORMAT.format(Instant.ofEpochMilli(tsMs).atZone(ZoneId.systemDefault()).toLocalTime()),
        locale,
    )

/** 标题「M月d日 09:12」——只显示开始时间（Q5 定案）；数字经 [localizeDigits]（ar 阿数，§20 缺口⑤）。 */
internal fun formatHotSlotTitle(startMs: Long, locale: Locale): String {
    val dateTime = Instant.ofEpochMilli(startMs).atZone(ZoneId.systemDefault())
    return localizeDigits(
        "${monthDayText(dateTime.monthValue, dateTime.dayOfMonth, locale)} ${TIME_FORMAT.format(dateTime.toLocalTime())}",
        locale,
    )
}
