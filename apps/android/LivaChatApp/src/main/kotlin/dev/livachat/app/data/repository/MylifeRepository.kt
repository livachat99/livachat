package dev.livachat.app.data.repository

import android.util.Log
import dev.livachat.app.data.remote.mylife.MylifeApi
import dev.livachat.app.data.remote.mylife.dto.AudioSegment
import dev.livachat.app.data.remote.mylife.dto.EnergyEnvelope
import dev.livachat.app.data.remote.mylife.dto.EventType
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDto
import dev.livachat.app.data.remote.mylife.dto.RemoveResponseDto
import dev.livachat.app.data.remote.mylife.dto.SearchResponseDto
import dev.livachat.app.data.remote.mylife.dto.TimelineEventDto
import dev.livachat.sdk.LivaChatConfig
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.HttpUrl
import java.time.Instant
import java.util.Base64
import java.util.TimeZone
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

/** Gallery 帧分页结果（frames 已 ts 升序、mediaUrl 已绝对化附 token）。 */
data class FramePage(
    val frames: List<FrameDto>,
    val total: Int,
    val nextCursor: String?,
)

/** 音频画廊分页结果（segments 已 ts 升序、mediaUrl 已绝对化附 token）。 */
data class AudioPage(
    val segments: List<AudioSegment>,
    val total: Int,
    val nextCursor: String?,
)

/**
 * hotSlotView 分页结果（events 已窗口过滤、ts 升序、mediaUrl 绝对化；跨页 event_ref 去重由 VM 追加时完成）。
 * `reachedWindowEnd` = 本页出现越过窗口上界的事件 → 调用方停止翻页（§4.3 精确语义）。
 */
data class HotSlotPage(
    val events: List<HotSlotEvent>,
    val nextCursor: String?,
    val reachedWindowEnd: Boolean,
)

/**
 * 仅内存缓存（对齐 Web react-query 内存语义，设计文档 D8）。
 * 媒体 URL（BFF 返回相对路径）在此出口统一转绝对并附带 ?token=，UI 零感知（§5.5）。
 * pending 60s 轮询（§6.4）在此共享：任一消费视图（Timeline/热点视图）都不依赖
 * 自身 VM 启动轮询，分析完成后 overview 自动更新（对齐 Web useOverview refetchInterval）。
 */
@Singleton
class MylifeRepository @Inject constructor(
    private val api: MylifeApi,
    @Named("mylifeApiBase") private val baseUrl: HttpUrl,
    private val config: LivaChatConfig,
    private val scope: CoroutineScope,
) {
    // ---- overview（§8：keyset 分页窗口；days = 已加载集合，非全量）----

    private val _overview = MutableStateFlow<OverviewDto?>(null)
    val overview: StateFlow<OverviewDto?> = _overview.asStateFlow()

    /**
     * 取一页 overview 并与已加载窗口合并（docs/bff_service_interfaces.md §8.2.4/E10）：
     * - `cursor = null` → 自最新起（首屏 / 下拉刷新 / 粒度切换 / 轮询重取）；
     * - `cursor = <scope 末日次日>` → 窗口定位（drilled scope 刷新，§8.3 E12 备注）；
     * 响应 `timezone` 变化（§8.3 E6：日界整体变了）→ 丢弃旧窗口重来。
     */
    suspend fun refreshOverview(
        force: Boolean = false,
        limit: Int = OVERVIEW_PAGE_DAYS,
        cursor: String? = null,
    ): OverviewDto {
        if (!force && cursor == null && _overview.value != null) return _overview.value!!
        return fetchOverviewPage(limit = limit, cursor = cursor)
    }

    /** 加载更早一页（游标 = 当前窗口前沿，页大小由调用方按粒度给）；无更多 → false（不发请求）。 */
    suspend fun loadMoreOverview(limit: Int): Boolean {
        val cursor = _overview.value?.nextCursor ?: return false
        fetchOverviewPage(limit = limit, cursor = cursor)
        return true
    }

    /**
     * 取一页并落窗。**tz 变化（E6）**：旧窗口的日界整体作废，此时若取的是「更早页 / 定位页」
     * （cursor 非空），直接以该页为窗口会让**最新日卡消失** → 改为一律回第一页。
     */
    private suspend fun fetchOverviewPage(limit: Int, cursor: String?): OverviewDto {
        // 每次刷新现取系统时区（timezone-support.md §4：跨时区旅行自然生效）
        val page = api.overview(timezone = TimeZone.getDefault().id, limit = limit, cursor = cursor)
        val prev = _overview.value
        if (cursor != null && prev != null && prev.timezone != page.timezone) {
            return fetchOverviewPage(limit = limit, cursor = null)
        }
        return applyOverviewPage(page)
    }

    /**
     * 按 `date` 去重合并（新页优先）+ 游标取更深的一方；tz 变化则丢弃旧窗口（E6）。
     * 用 `update{}`（CAS）而非读后写：轮询在 `Dispatchers.Default`、VM 取数在 Main，
     * 并发丢一次合并会让窗口与游标不一致（游标比窗口新→翻页空转）。
     */
    private fun applyOverviewPage(page: OverviewDto): OverviewDto {
        val absolutized = page.absolutizeImages()
        _overview.update { prev ->
            if (prev == null || prev.timezone != absolutized.timezone) {
                absolutized
            } else {
                val byDate = LinkedHashMap<String, OverviewDayDto>(
                    absolutized.days.size + prev.days.size)
                for (d in absolutized.days) byDate[d.date] = d
                for (d in prev.days) byDate.putIfAbsent(d.date, d)
                absolutized.copy(
                    days = byDate.values.sortedByDescending { it.date },
                    nextCursor = deeperCursor(prev.nextCursor, absolutized.nextCursor),
                )
            }
        }
        return _overview.value!!
    }

    /** 更深的一方：null = 已达最早（更深）；否则取日期较小者（§8.2.4）。 */
    private fun deeperCursor(a: String?, b: String?): String? =
        if (a == null || b == null) null else minOf(a, b)

    /** overview 的 images.media_url（相对路径）在此出口统一转绝对并附 token（§5.5 媒体出口统一语义）。 */
    private fun OverviewDto.absolutizeImages(): OverviewDto {
        if (days.none { d -> d.images.any { i -> i.mediaUrl.isNotEmpty() } }) return this
        return copy(
            days = days.map { d ->
                if (d.images.isEmpty()) d
                else d.copy(images = d.images.map { i -> i.copy(mediaUrl = i.mediaUrl.absolutize()) })
            },
        )
    }

    /**
     * 轮询/徽章窗口（最新 [MAX_POLL_DAYS] 天）内是否有 pending/failed 待重试（日粒度语义 §9.1.2）。
     *
     * §8 E10/R4：分页后「窗口」= 已加载集合的最新 N 天（`days` 为 date 降序）——深尾
     * （> [MAX_POLL_DAYS]）不参与轮询，靠触底/下拉刷新更新；`has_pending` 仍是逐日字段。
     */
    fun hasPendingAnalysis(): Boolean = pendingInWindow(_overview.value)

    private fun pendingInWindow(dto: OverviewDto?): Boolean =
        dto?.days.orEmpty().take(MAX_POLL_DAYS).any { it.hasPending }

    private fun pollWindowDays(): Int =
        (_overview.value?.days?.size ?: 0).coerceIn(1, MAX_POLL_DAYS)

    init {
        // 无 pending 即挂起等待（对齐 Web useOverview refetchInterval 的 60s + 即停语义，§6.4）。
        // 本 scope（Dispatchers.Default）的裸网络入口只有轮询与 flushPendingRemovalRefresh
        // （Gallery 删除后的延迟强刷）：异常必须就地兜底，否则逃逸到线程 uncaught handler
        // 直接崩进程（真机复现：server 抖动期 TLS 握手失败 → FATAL）。失败记日志、按既有
        // 60s 节奏自然重试，不中断轮询。
        scope.launch {
            while (isActive) {
                try {
                    overview.first { pendingInWindow(it) }
                    delay(60_000)
                    if (pendingInWindow(_overview.value)) {
                        // §8 E10：重取「已加载窗口」而非只刷第一页——只刷首页会让窗口内更早的
                        // pending 日永不更新 → 徽章常亮 + 轮询不停止（死循环）
                        refreshOverview(force = true, limit = pollWindowDays())
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Log.w(TAG, "overview poll failed; retrying in 60s", e)
                }
            }
        }
    }

    // ---- search（§5.3）：无缓存直通，分页状态由 TimelineViewModel 持有 ----
    suspend fun search(query: String, cursor: String? = null): SearchResponseDto =
        api.search(query, cursor = cursor)

    // ---- Gallery 帧（docs/android_timeline_image_gallery.md §3.6）----

    /**
     * 该日期范围内全部关键帧（Gallery 数据源）。服务端分页自最早帧推进（store ORDER BY
     * ts_ms 升序），页内重排为 ts 升序后由 VM 追加；`media_url` 空条目跳过（bff
     * `/timeline` 未按 frame_id 过滤，C4/E8）。
     */
    suspend fun frames(
        from: String,
        to: String,
        cursor: String? = null,
        limit: Int = 40,
    ): FramePage {
        val resp = api.timeline(
            dateFrom = from,
            dateTo = to,
            limit = limit,
            cursor = cursor,
            timezone = TimeZone.getDefault().id,
        )
        val frames = resp.days
            .flatMap { it.events }
            .mapNotNull { ev ->
                val url = ev.mediaUrl?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
                val frameId = ev.frameId?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
                FrameDto(
                    tsMs = ev.tsMs,
                    mediaUrl = url.absolutize(),
                    frameId = frameId,
                    windowId = ev.windowId,
                    caption = ev.caption,
                    eventRef = ev.eventRef,
                )
            }
            .sortedBy { it.tsMs }
        return FramePage(frames = frames, total = resp.total, nextCursor = resp.nextCursor)
    }

    /** 帧图字节（查看器保存到相册）：一切失败收敛为 null（不抛）；协程取消照常上抛。 */
    suspend fun downloadFrame(url: String): ByteArray? =
        try {
            api.downloadBytes(url)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            null
        }

    /**
     * 删除 video 事件（Gallery 编辑模式，docs/android_timeline_image_gallery.md §9.4.3）：
     * 直通无缓存；部分成功语义在响应体（removed/skipped），业务/网络异常照常上抛由 VM 收敛为 tip。
     */
    suspend fun removeEvents(eventRefs: List<String>): RemoveResponseDto =
        api.removeEvents(eventRefs)

    /** Gallery 删除成功后的延迟刷新标记（§9 Q9.3）：不在画廊内刷新，退出画廊时 flush。 */
    private var removalRefreshPending = false

    /**
     * 退出画廊（FrameGalleryViewModel.onCleared）时调用：有标记 → 清标记 + 在自有 scope 内强刷
     * 已加载窗口（VM 销毁后仍完成；异常就地兜底——同轮询纪律，失败静默，下次进入自然重取）。
     */
    fun flushPendingRemovalRefresh() {
        if (!removalRefreshPending) return
        removalRefreshPending = false
        scope.launch {
            try {
                refreshOverview(force = true, limit = pollWindowDays())
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "overview refresh after removal failed", e)
            }
        }
    }

    fun markPendingRemovalRefresh() {
        removalRefreshPending = true
    }

    // ---- 音频画廊取数（docs/android_timeline_audio_gallery.md §4.2）----

    /**
     * 该本地日全部语音段（AudioGallery 数据源）：复用 `/timeline?types=audio`，bff 零新增端点。
     * `media_url`/`event_ref` 缺失的条目丢弃（对齐 frames() 的 C4/E8 容错）；出口统一绝对化附 token。
     */
    suspend fun audioSegments(
        date: String,
        cursor: String? = null,
        limit: Int = AUDIO_PAGE_LIMIT,
    ): AudioPage {
        val resp = api.timeline(
            dateFrom = date,
            dateTo = date,
            types = EventType.AUDIO,
            limit = limit,
            cursor = cursor,
            timezone = TimeZone.getDefault().id,
        )
        val segments = resp.days
            .flatMap { it.events }
            .mapNotNull { ev ->
                val url = ev.mediaUrl?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
                val ref = ev.eventRef?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
                AudioSegment(
                    eventRef = ref,
                    tsMs = ev.tsMs,
                    mediaUrl = url.absolutize(),
                    transcript = ev.transcript?.takeIf { it.isNotBlank() },
                    summary = ev.audioSummary?.takeIf { it.isNotBlank() },
                    durationMs = ((ev.durationS ?: 0.0) * 1000).toLong(),
                    energy = decodeEnergy(ev),
                )
            }
            .sortedBy { it.tsMs }
        return AudioPage(segments = segments, total = resp.total, nextCursor = resp.nextCursor)
    }

    /**
     * 能量包络解码（§10 A1′）：`energy_v` 非 1（含缺失）/ 任一区间字段缺失 / base64 解码失败
     * / 空串 → null ⇒ 渲染侧降级为纯轨道。base64 用 `java.util.Base64`（API 26+，可 JVM 单测）。
     */
    private fun decodeEnergy(ev: TimelineEventDto): EnergyEnvelope? {
        if (ev.energyV != 1) return null
        val b64 = ev.energy?.takeIf { it.isNotEmpty() } ?: return null
        val frameMs = ev.frameMs ?: return null
        val dbMin = ev.dbMin ?: return null
        val dbMax = ev.dbMax ?: return null
        val values = try {
            Base64.getDecoder().decode(b64)
        } catch (_: IllegalArgumentException) {
            return null
        }
        if (values.isEmpty() || dbMax <= dbMin) return null
        return EnergyEnvelope(frameMs, dbMin.toFloat(), dbMax.toFloat(), values)
    }

    // ---- hotSlotView 取数（docs/android_timeline_hotslot.md §4.3）----

    /**
     * hot slot 窗口 [startMs, endMs] 的一页五类事件（首页与增量页同入口）。
     *
     * 取数日由 slot ms 在**当前**设备时区现推（§3.3 时区约束：不得沿用 overview 的 date 键，
     * 该键是请求时刻的时区产物，跨时区后 bff 按请求时区重算的日窗口可能不含本窗口；
     * 由 ms 现推恒覆盖，跨时区时最多 2 天）。页内做窗口过滤（含端点）与空行剔除，
     * 跨页 event_ref 去重由调用方（VM）持有 seen 集合完成（OFFSET 游标遇录制中插入会位移）。
     */
    suspend fun hotSlotEvents(startMs: Long, endMs: Long, cursor: String? = null): HotSlotPage {
        val zone = TimeZone.getDefault()
        val resp = api.timeline(
            dateFrom = localDateOf(startMs, zone),
            dateTo = localDateOf(endMs, zone),
            types = HOT_SLOT_TYPES,
            limit = HOT_SLOT_PAGE_LIMIT,
            cursor = cursor,
            timezone = zone.id,
        )
        val page = resp.days.flatMap { it.events }
        val items = page
            .filter { it.tsMs in startMs..endMs }
            .mapNotNull { it.toHotSlotEvent() }
            .sortedBy { it.tsMs }
        return HotSlotPage(
            events = items,
            nextCursor = resp.nextCursor,
            // 页按 ts 升序（store.py:227）→ 越过窗口上界即后续页必全超窗，调用方停止翻页
            reachedWindowEnd = page.any { it.tsMs > endMs },
        )
    }

    /** 事件 → hotSlotView 行：video 无帧跳过；文本行空白跳过（agent_job 先回落 agent_name）。 */
    private fun TimelineEventDto.toHotSlotEvent(): HotSlotEvent? {
        if (type == EventType.VIDEO) {
            val url = mediaUrl?.takeIf { it.isNotEmpty() } ?: return null
            val fid = frameId?.takeIf { it.isNotEmpty() } ?: return null
            return HotSlotEvent(
                tsMs = tsMs, type = type, frameId = fid,
                mediaUrl = url.absolutize(), caption = caption, eventRef = eventRef,
            )
        }
        val body = when (type) {
            EventType.AGENT_JOB -> listOfNotNull(text, summary, agentName)
                .firstOrNull { it.isNotBlank() }
            else -> text?.takeIf { it.isNotBlank() }
        } ?: return null
        return HotSlotEvent(tsMs = tsMs, type = type, role = role, text = body, eventRef = eventRef)
    }

    // ---- 媒体 URL 绝对化（§5.5）----
    private fun String.absolutize(): String {
        val resolved = baseUrl.resolve(this)
            ?: return this
        return if (resolved.queryParameter("token") != null) {
            resolved.toString()
        } else {
            resolved.newBuilder().addQueryParameter("token", config.token).build().toString()
        }
    }

    private companion object {
        const val TAG = "MylifeRepository"

        /** overview 首屏/DAY·WEEK 粒度页大小（§8.2.4）。 */
        const val OVERVIEW_PAGE_DAYS = 30

        /** overview 轮询窗口上限（§8 E10/R4：深尾不参与 60s 轮询）。 */
        const val MAX_POLL_DAYS = 400

        /** hotSlotView 五类（docs/android_timeline_hotslot.md §3.2 = 分段口径集合）。 */
        const val HOT_SLOT_TYPES = "chat,agent_job,video,transcribe,translate"

        /** bff /timeline limit 上限（router.py:32）。 */
        const val HOT_SLOT_PAGE_LIMIT = 500

        /** 音频画廊页大小（对齐图片 Gallery 的 40）。 */
        const val AUDIO_PAGE_LIMIT = 40
    }
}

/**
 * 事件时刻 → 请求时区本地日（DATE 字符串）。hotSlot 取数日与搜索命中 pending 归属共用口径；
 * 承重 bff 日窗口语义——不得改用 UTC 日（跨时区时 bff 按请求时区重算的日窗口须与之对齐）。
 */
internal fun localDateOf(tsMs: Long, zone: TimeZone = TimeZone.getDefault()): String =
    Instant.ofEpochMilli(tsMs).atZone(zone.toZoneId()).toLocalDate().toString()
