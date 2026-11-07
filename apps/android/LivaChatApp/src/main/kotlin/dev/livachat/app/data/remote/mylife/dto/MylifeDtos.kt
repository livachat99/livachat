package dev.livachat.app.data.remote.mylife.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// 字段逐字段对齐 apps/web/mylife/src/api/types.ts（与 services/bff/common/schemas.py 对齐）。
// 枚举型字段（type/kind/status）用 String 宽松解析：BFF 直通字段集合稳定但值可能演进，
// kotlinx enum 对未知值抛异常，String + 常量分派不整包失败（对齐 LvdataStore 容错语义）。

object EventType {
    const val CHAT = "chat"
    const val AUDIO = "audio"
    const val VIDEO = "video"
    const val WINDOW = "window"
    const val AGENT_JOB = "agent_job"
    const val TRANSCRIBE = "transcribe"   // hotSlotView 展示类型（docs/android_timeline_hotslot.md §4.3）
    const val TRANSLATE = "translate"
}

/** 活跃时段（§9.1.2 服务端派生）：startMs = 段首事件时刻（§3.2 精确匹配）。 */
@Serializable
data class HotSlotDto(
    @SerialName("start_ms") val startMs: Long,
    @SerialName("end_ms") val endMs: Long,
    @SerialName("count") val count: Int,
)

/** 日卡缩略图（§9.1.2：window 分组轮转选取 → ts 升序；media_url 相对路径待出口绝对化）。 */
@Serializable
data class DayImageDto(
    @SerialName("frame_id") val frameId: String,
    @SerialName("ts_ms") val tsMs: Long,
    @SerialName("event_ref") val eventRef: String,
    @SerialName("media_url") val mediaUrl: String,
    // 帧的窗口归属（跨日轮转分组键 = windowId ?: eventRef）；旧 bff 缺省 null
    @SerialName("window_id") val windowId: String? = null,
)

/** day_summary JSON topics 元素（{topic, detail, search_words}）；detail 透传备用不渲染；
 * search_words 是可直接搜到该主题重点事件的原文片段（docs/android_timeline_topics.md §10）。 */
@Serializable
data class DayTopicDto(
    @SerialName("topic") val topic: String,
    @SerialName("detail") val detail: String? = null,
    @SerialName("search_words") val searchWords: String? = null,
)

@Serializable
data class OverviewDayDto(
    @SerialName("date") val date: String,
    @SerialName("counts") val counts: Map<String, Int> = emptyMap(),
    @SerialName("summary") val summary: String? = null,
    @SerialName("hot_slots") val hotSlots: List<HotSlotDto> = emptyList(),
    @SerialName("images") val images: List<DayImageDto> = emptyList(),
    @SerialName("has_pending") val hasPending: Boolean = false,
    @SerialName("topics") val topics: List<DayTopicDto> = emptyList(),
) {
    val totalCount: Int get() = counts.values.sum()
}

@Serializable
data class OverviewDto(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("timezone") val timezone: String,
    // §8 分页：days = **已加载窗口**（非全量）；nextCursor 非空 = 还有更早的日卡
    @SerialName("days") val days: List<OverviewDayDto> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
    // 该时区天卡总数（分页窗口下 > days.size）；旧 bff 缺省 0
    @SerialName("total") val total: Int = 0,
)

// -- timeline（Gallery 帧数据源，docs/android_timeline_image_gallery.md §3.6）----------

/** /timeline 响应（事件 payload 扁平展开；未知键忽略）。 */
@Serializable
data class TimelineResponseDto(
    @SerialName("days") val days: List<TimelineDayDto> = emptyList(),
    @SerialName("next_cursor") val nextCursor: String? = null,
    @SerialName("total") val total: Int = 0,
)

@Serializable
data class TimelineDayDto(
    @SerialName("date") val date: String,
    @SerialName("events") val events: List<TimelineEventDto> = emptyList(),
)

/**
 * /timeline 事件：Gallery 消费 mediaUrl/frameId/windowId/caption；hotSlotView 另消费
 * type/role/text/summary/agentName（docs/android_timeline_hotslot.md §4.3）。
 * 字段全默认值：缺字段容错，不整包失败。
 */
@Serializable
data class TimelineEventDto(
    @SerialName("ts_ms") val tsMs: Long,
    @SerialName("type") val type: String = "",
    @SerialName("role") val role: String? = null,
    @SerialName("text") val text: String? = null,
    @SerialName("summary") val summary: String? = null,
    @SerialName("agent_name") val agentName: String? = null,
    @SerialName("job_key") val jobKey: String? = null,
    @SerialName("media_url") val mediaUrl: String? = null,
    @SerialName("frame_id") val frameId: String? = null,
    @SerialName("window_id") val windowId: String? = null,
    @SerialName("caption") val caption: String? = null,
    @SerialName("event_ref") val eventRef: String? = null,
    // 语音段字段（audio 事件；docs/android_timeline_audio_gallery.md §4.2）：
    // summary 是列拼接串（转写+摘要+情绪），audioSummary 才是 LLM 摘要（bff T1 新增字段）
    @SerialName("transcript") val transcript: String? = null,
    @SerialName("audio_summary") val audioSummary: String? = null,
    // Double 而非 Int：写入侧 {**parsed} 可让其为浮点，Int 解析抛异常会整包失败（§4.2）
    @SerialName("duration_s") val durationS: Double? = null,
    // 能量包络（§10 A1′；audio 事件独有，其余类型恒 null）：量化原值 + 还原区间
    @SerialName("energy") val energy: String? = null,
    @SerialName("energy_v") val energyV: Int? = null,
    @SerialName("frame_ms") val frameMs: Int? = null,
    @SerialName("db_min") val dbMin: Double? = null,
    @SerialName("db_max") val dbMax: Double? = null,
)

/** Gallery 帧（/timeline?types=video 映射产物；mediaUrl 已绝对化附 token）。 */
data class FrameDto(
    val tsMs: Long,
    val mediaUrl: String,
    val frameId: String,
    val windowId: String? = null,
    val caption: String? = null,
    val eventRef: String? = null, // 删除键（§9 编辑模式）；预留：分享/引用锚
)

/**
 * 语音能量包络（docs/android_timeline_audio_gallery.md §10 A1′）：**量化原值**（uint8，0..255）
 * + 还原区间——绝对值 dBFS 可由 `dbMin + v/255*(dbMax-dbMin)` 还原，渲染侧再做归一化/聚合。
 * 注意 `values` 是 ByteArray：本类的 equals/hashCode 不比较内容（数组语义），断言请逐字段比。
 */
data class EnergyEnvelope(
    val frameMs: Int,
    val dbMin: Float,
    val dbMax: Float,
    val values: ByteArray,
)

/**
 * 语音段（/timeline?types=audio 映射产物，docs/android_timeline_audio_gallery.md §4.2）：
 * mediaUrl 已绝对化附 token；`eventRef` 是唯一键（events 表唯一约束）→ 去重 + 列表 key + 播放器身份。
 * `energy` 缺失/版本不识别 → null（渲染侧降级为纯轨道，§10.4）。
 */
data class AudioSegment(
    val eventRef: String,
    val tsMs: Long,
    val mediaUrl: String,
    val transcript: String?,
    val summary: String?,
    val durationMs: Long,
    val energy: EnergyEnvelope? = null,
)

/**
 * hotSlotView 行模型（/timeline 五类事件映射产物，docs/android_timeline_hotslot.md §4.3）：
 * 空文本行与无帧 video 已在 Repository 出口丢弃；mediaUrl 已绝对化附 token；
 * video 帧携带逐帧分析 caption（§9 查看器 bar；可空**或空串**——缺失判定在 UI 层 isNotBlank）。
 */
data class HotSlotEvent(
    val tsMs: Long,
    val type: String,
    val role: String? = null,
    val text: String? = null,
    val frameId: String? = null,
    val mediaUrl: String? = null,
    val caption: String? = null,
    val eventRef: String? = null,
)

@Serializable
data class SearchHitDto(
    val date: String,
    @SerialName("ts_ms") val tsMs: Long? = null,  // FTS 命中恒有 ts；可空仅作防御/兼容旧服务端
    val kind: String,                              // 宽松字符串，不建枚举（对齐 V1 容错语义）
    val snippet: String,
)

@Serializable
data class SearchResponseDto(
    val query: String,
    val total: Int = 0,
    @SerialName("next_cursor") val nextCursor: String? = null,
    val hits: List<SearchHitDto> = emptyList(),
)

// -- 删除事件（Gallery 编辑模式，docs/android_timeline_image_gallery.md §9.4.3）--------------

@Serializable
data class RemoveRequestDto(
    @SerialName("event_refs") val eventRefs: List<String>,
)

/** 200 恒含基础字段（部分成功语义）：每 ref 恰出现于 [removed] 或 [skipped] 之一。 */
@Serializable
data class RemoveResponseDto(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("removed") val removed: List<String> = emptyList(),
    @SerialName("window_events_removed") val windowEventsRemoved: List<String> = emptyList(),
    @SerialName("images_removed") val imagesRemoved: List<String> = emptyList(),
    @SerialName("images_pending") val imagesPending: List<String> = emptyList(),
    @SerialName("days_recomputed") val daysRecomputed: List<DaysRecomputedDto> = emptyList(),
    @SerialName("skipped") val skipped: List<SkippedRefDto> = emptyList(),
    @SerialName("vectors_pending") val vectorsPending: Boolean? = null,
)

@Serializable
data class DaysRecomputedDto(
    @SerialName("timezone") val timezone: String = "",
    @SerialName("local_date") val localDate: String = "",
)

/** reason = `not_found`（不存在/不可见/已被并发删除）或 `not_video`。 */
@Serializable
data class SkippedRefDto(
    @SerialName("event_ref") val eventRef: String = "",
    @SerialName("reason") val reason: String = "",
)

// -- agent job cancel（docs/android_agent_job_status.md §7.1）--------------------

@Serializable
data class CancelAgentJobRequestDto(
    @SerialName("job_id") val jobId: String,
)

/** status = cancelled（取消生效）/ completed（自然完成竞态）/ failed（其它终态）。 */
@Serializable
data class CancelAgentJobResponseDto(
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("job_id") val jobId: String,
    @SerialName("status") val status: String,
)
