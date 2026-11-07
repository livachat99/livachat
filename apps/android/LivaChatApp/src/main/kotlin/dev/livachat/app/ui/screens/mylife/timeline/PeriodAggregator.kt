package dev.livachat.app.ui.screens.mylife.timeline

import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.data.remote.mylife.dto.EventType
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import dev.livachat.app.ui.util.localizeDigits
import java.time.LocalDate
import java.time.temporal.WeekFields
import java.util.Locale
import kotlin.random.Random

/** 浏览粒度（V2 设计 §5.1）；选择器平级切换，月/年卡本体下钻（§3.3.2）。 */
enum class Granularity { DAY, WEEK, MONTH, YEAR }

/**
 * 活跃时段（docs/android_timeline_hotslot.md §4.1：仅日卡展示，导航目标为 hotSlotView）。
 * startMs/endMs = 段首/段末事件时刻（bff 服务端派生并按 60min 间隔切段、超 3 段合并，§3.1），
 * 二者构成新屏取数窗口 [startMs, endMs]。
 */
data class HotSlot(
    val startMs: Long,
    val endMs: Long,
)

/** 卡片缩略图（v3：无 sessionId；tsMs 定位 + eventRef 精确锚，§4.3）。 */
data class PeriodImage(
    val url: String,      // 绝对 media URL（Repository 出口已附 token）
    val tsMs: Long,
    val eventRef: String, // bff DayImage.event_ref 非空（events.event_ref NOT NULL UNIQUE）
    val frameId: String,  // Gallery 导航 focus 锚（bff DayImage.frame_id）
    val windowId: String? = null, // 跨日轮转分组键的一部分（= windowId ?: eventRef）
)

/** 四种粒度统一的时间范围摘要卡模型（§5.1）。 */
data class PeriodDigest(
    val key: String,              // "2026-08-21" | "2026-W33" | "2026-08" | "2026"
    val granularity: Granularity,
    val rangeLabel: String,       // "8月21日 · 周四" | "8/10 – 8/16" | "2026年8月" | "2026年"
    val hotSlots: List<HotSlot>,  // 仅 DAY 非空：bff 日级 slots 直接映射（≤3、升序）
    val summaryLines: List<String>,  // 全部非空去重行（按时间倒序）；UI 按 summaryLimit 折叠
    val summaryLimit: Int,        // 默认显示条数（DAY≤3 / WEEK≤4 / MONTH≤5 / YEAR≤6，§5.1）
    val topics: List<DayTopicDto>,   // day_summary 供数；chip 只显示 topic 文本（PeriodCard 渲染）
    val images: List<PeriodImage>,   // 轮转选取、ts 升序（与 bff 同规则重建）；
                                     //   数量 cap day3/week9/month12/year15（用户 2026-09-20 定）
    /** 该 period 的真实关键帧总数（= 各天 `counts["video"]` 之和；与 Gallery 的 total 同口径）。
     *  > images.size 时卡片首行末图右上角显示计数徽标（PeriodCard）。 */
    val imageTotal: Int,
    val hasPending: Boolean,
    /** 音频段数（docs/android_timeline_audio_gallery.md §4.1 R1）：**仅 DAY 粒度**非零，
     *  >0 时该日卡在 hotSlot chips 行右侧显示音频入口；周/月/年卡恒 0（不显示入口）。 */
    val audioCount: Int = 0,
) {
    /**
     * 本卡粒度的 Gallery 请求范围（闭区间本地日，docs/android_timeline_image_gallery.md §3.2）：
     * DAY=key..key；WEEK=ISO 周周一..周日；MONTH=1 日..月末；YEAR=全年。
     * key 形状由本聚合器保证，异常 key 返回 null（调用方跳过导航）。
     */
    fun dateRange(): Pair<String, String>? = when (granularity) {
        Granularity.DAY -> key to key
        Granularity.WEEK -> mondayOfIsoWeek(key)?.let { m -> m.toString() to m.plusDays(6).toString() }
        Granularity.MONTH -> runCatching { LocalDate.parse("$key-01") }.getOrNull()
            ?.let { f -> f.toString() to f.withDayOfMonth(f.lengthOfMonth()).toString() }
        Granularity.YEAR -> runCatching { LocalDate.parse("$key-01-01") }.getOrNull()
            ?.let { f -> f.toString() to f.withDayOfYear(f.lengthOfYear()).toString() }
    }
}

/**
 * overview 全量天 → 四种粒度摘要卡片（V2 §5.1，纯逻辑可单测）。
 * v3：日卡直读 bff 日字段；周/月/年卡客户端聚合日卡（§4.2；period_summary 下一阶段供数，骨架不变）。
 * 确定性、无 LLM、无网络；输入顺序不影响输出（各组内部与组间均显式排序）。
 */
object PeriodAggregator {

    /** 周/月/年卡 topics 上限（docs/android_timeline_topics.md §3.1）；日卡全量、不设上限。 */
    const val MAX_TOPICS = 50

    fun aggregate(
        days: List<OverviewDayDto>,
        granularity: Granularity,
        scopeKey: String? = null,
        /** 展示标签 locale（docs/android_multilingual.md §3.5）；默认简体中文供既有单测缺省，生产须显式传 UI locale。 */
        locale: Locale = Locale.SIMPLIFIED_CHINESE,
    ): List<PeriodDigest> {
        // 畸形日期（外部数据容错）在入口剔除：rangeLabel/dayLabel 按数值解析日期段（toInt），
        // 非法值会抛 NumberFormatException 拖垮整屏；与 groupKey(WEEK)/parseLocalDate 的既有容错口径一致
        val filtered = (if (scopeKey != null) days.filter { it.date.startsWith(scopeKey) } else days)
            .filter { parseLocalDate(it.date) != null }
        val groups = LinkedHashMap<String, MutableList<OverviewDayDto>>()
        for (day in filtered) {
            val key = groupKey(day.date, granularity) ?: continue
            groups.getOrPut(key) { mutableListOf() }.add(day)
        }
        return groups.map { (key, groupDays) -> buildDigest(key, granularity, groupDays, locale) }
            .sortedByDescending { it.key }
    }

    private fun groupKey(date: String, granularity: Granularity): String? = when (granularity) {
        Granularity.DAY -> date
        Granularity.MONTH -> date.take(7)
        Granularity.YEAR -> date.take(4)
        Granularity.WEEK -> parseLocalDate(date)?.let { isoWeekKey(it) }
    }

    /** ISO-8601 周 key（week-year 前缀，"2026-W33"），跨年周不被切到两个年份（§10 风险 7）。 */
    private fun isoWeekKey(date: LocalDate): String {
        val weekFields = WeekFields.ISO
        val year = date.get(weekFields.weekBasedYear())
        val week = date.get(weekFields.weekOfWeekBasedYear())
        return "%04d-W%02d".format(year, week)
    }

    private fun buildDigest(
        key: String,
        granularity: Granularity,
        days: List<OverviewDayDto>,
        locale: Locale,
    ): PeriodDigest {
        // 日倒序聚合输入：summary/images/topics 按此序（周以上卡的最优显示序）
        val daysByTime = days.sortedByDescending { it.date }
        val label = rangeLabel(key, granularity, days, locale)
        // hotSlots 仅日卡展示（docs/android_timeline_hotslot.md §4.1）：日恰好每天一组，
        // 直接映射 bff 日级 slots（bff 已按五类口径分段并合并至 ≤3，客户端零推导）
        val hotSlots = if (granularity == Granularity.DAY) {
            days.first().hotSlots.map { HotSlot(it.startMs, it.endMs) }
        } else {
            emptyList()
        }
        return PeriodDigest(
            key = key,
            granularity = granularity,
            rangeLabel = label,
            hotSlots = hotSlots,
            summaryLines = summarize(daysByTime, granularity, label, locale),
            summaryLimit = summaryLimit(granularity),
            topics = selectTopics(daysByTime, granularity, key),
            images = collectImages(daysByTime, granularity),
            // video 事件 = 关键帧（一帧一条），故 counts 求和即真实帧数（与 /timeline total 同口径）
            imageTotal = days.sumOf { it.counts[EventType.VIDEO] ?: 0 },
            hasPending = days.any { it.hasPending },
            // 音频段数：一事件一段（silent 段不落事件）→ counts 即真实段数；仅日卡展示入口
            audioCount = if (granularity == Granularity.DAY) {
                days.sumOf { it.counts[EventType.AUDIO] ?: 0 }
            } else {
                0
            },
        )
    }

    private fun rangeLabel(
        key: String,
        granularity: Granularity,
        days: List<OverviewDayDto>,
        locale: Locale,
    ): String = when (granularity) {
        Granularity.DAY -> dayLabel(days.first().date, locale)
        Granularity.WEEK -> weekLabel(days.first().date, locale)
        Granularity.MONTH -> monthYearText(key.take(4).toInt(), key.substringAfter('-').toInt(), locale)
        Granularity.YEAR -> yearText(key.toInt(), locale)
    }

    private fun dayLabel(date: String, locale: Locale): String {
        val parts = date.substringAfter('-').split('-')
        val weekday = parseLocalDate(date)?.dayOfWeek?.let { weekdayText(it, locale) } ?: ""
        return "${monthDayText(parts[0].toInt(), parts[1].toInt(), locale)} · $weekday"
    }

    /** 周范围标签：ISO 周周一起始（跨年周由周一所在年计算，§10 风险 7）；ru 日.月 дд.мм（§11 缺口④）、de dd.MM. 带尾点（§12 缺口④）、fr/it/es/pt/hi/id·in/th/vi dd/MM 斜线（§13/§14/§15/§16/§18/§19/§21 缺口④）、tr dd.MM 点分无尾点（§17 缺口④）、ar dd/MM 斜线 + 阿数（§20 缺口④）。 */
    private fun weekLabel(date: String, locale: Locale): String {
        val first = parseLocalDate(date) ?: return ""
        val monday = first.with(WeekFields.ISO.dayOfWeek(), 1L)
        val sunday = monday.plusDays(6)
        return when (locale.language) {
            "ru" -> {
                fun ru(d: LocalDate) = "${d.dayOfMonth.toString().padStart(2, '0')}.${d.monthValue.toString().padStart(2, '0')}"
                "${ru(monday)} – ${ru(sunday)}"
            }
            "de" -> {
                fun de(d: LocalDate) = "${d.dayOfMonth.toString().padStart(2, '0')}.${d.monthValue.toString().padStart(2, '0')}."
                "${de(monday)} – ${de(sunday)}"
            }
            "fr", "it", "es", "pt", "hi", "id", "in", "th", "vi" -> {
                fun slash(d: LocalDate) = "${d.dayOfMonth.toString().padStart(2, '0')}/${d.monthValue.toString().padStart(2, '0')}"
                "${slash(monday)} – ${slash(sunday)}"
            }
            "tr" -> {
                fun dot(d: LocalDate) = "${d.dayOfMonth.toString().padStart(2, '0')}.${d.monthValue.toString().padStart(2, '0')}"
                "${dot(monday)} – ${dot(sunday)}"
            }
            // ar：dd/MM 斜线 + 阿拉伯-印度数字「١٧/٠٨ – ٢٣/٠٨」（§20 缺口④）
            "ar" -> {
                fun slash(d: LocalDate) = "${d.dayOfMonth.toString().padStart(2, '0')}/${d.monthValue.toString().padStart(2, '0')}"
                localizeDigits("${slash(monday)} – ${slash(sunday)}", locale)
            }
            else -> "${monday.monthValue}/${monday.dayOfMonth} – ${sunday.monthValue}/${sunday.dayOfMonth}"
        }
    }

    /**
     * 组内卡片缩略图：**轮转选取**，cap 按粒度（day 3 / week 9 / month 12 / year 15，
     * 与 §3.3 图片区行数对应）。
     *
     * 与 bff `_derive_images` 同一规则（docs/android_timeline_image_gallery.md §3.2 Q3）：
     * 跨日按 window 分组（分组键 = windowId ?: eventRef，与 bff 兜底逐字同规则）、
     * 窗口序 = (首帧 ts, 分组键)、第 r 轮每窗取第 r 张直至 cap、输出 ts 升序。
     * **契约（E2）**：客户端依赖「bff 选中的每窗帧 = 该窗 ts 升序前缀」性质
     * （bff 轮转 + 池 ≥ 最大 cap 保证）——改 bff 选图规则必须同步本函数。
     */
    private fun collectImages(days: List<OverviewDayDto>, granularity: Granularity): List<PeriodImage> {
        val cap = when (granularity) {
            Granularity.DAY -> 3
            Granularity.WEEK -> 9
            Granularity.MONTH -> 12
            Granularity.YEAR -> 15
        }
        val candidates = days.flatMap { d ->
            d.images.map { PeriodImage(it.mediaUrl, it.tsMs, it.eventRef, it.frameId, it.windowId) }
        }
        return rotateSelect(candidates, cap)
    }

    /** 轮转选图（同 bff `_derive_images`）：稳定排序保证同 ts 保持输入/轮转序。 */
    private fun rotateSelect(candidates: List<PeriodImage>, cap: Int): List<PeriodImage> {
        if (candidates.isEmpty()) return emptyList()
        val groups = LinkedHashMap<String, MutableList<PeriodImage>>()
        for (img in candidates.sortedBy { it.tsMs }) {
            // 空串 windowId 等价于缺失（bff `e.get("window_id") or e["event_ref"]` 的 Python 假值语义）
            groups.getOrPut(img.windowId?.takeIf { it.isNotEmpty() } ?: img.eventRef) { mutableListOf() }
                .add(img)
        }
        val ordered = groups.entries.sortedWith(
            compareBy({ it.value.first().tsMs }, { it.key }),
        )
        val picked = ArrayList<PeriodImage>(minOf(cap, candidates.size))
        var round = 0
        while (picked.size < cap) {
            var added = false
            for ((_, group) in ordered) {
                if (round >= group.size) continue
                picked.add(group[round])
                added = true
                if (picked.size >= cap) break
            }
            if (!added) break // 全部窗口取尽仍不足 cap → 返回全部
            round++
        }
        return picked.sortedBy { it.tsMs }
    }

    /**
     * summary：DAY 直读日 summary（空 → 不渲染 summary 块，pending 脉冲点已兜底）；
     * WEEK+ 各日 summary 非空去重、按日期倒序；全空 → 降级行「{rangeLabel}共 N 天」
     * （N = 组内天数，内联计算）。
     */
    private fun summarize(
        days: List<OverviewDayDto>,
        granularity: Granularity,
        rangeLabel: String,
        locale: Locale,
    ): List<String> {
        if (granularity == Granularity.DAY) {
            return listOfNotNull(days.firstOrNull()?.summary?.takeIf(String::isNotBlank))
        }
        val lines = days.mapNotNull { it.summary?.takeIf(String::isNotBlank) }.distinct()
        if (lines.isEmpty()) {
            val n = days.size
            val fallback = when (locale.language) {
                "zh" -> "${rangeLabel}共 $n 天"
                "ja" -> "${rangeLabel}・計 $n 日"
                "ko" -> "${rangeLabel}・총 ${n}일"
                "ru" -> "${rangeLabel} · всего $n ${ruDayWord(n)}"
                "de" -> "$rangeLabel · $n ${if (n == 1) "Tag" else "Tage"}"
                "fr" -> "$rangeLabel · $n ${if (n == 1) "jour" else "jours"}"
                "it" -> "$rangeLabel · $n ${if (n == 1) "giorno" else "giorni"}"
                "es" -> "$rangeLabel · $n ${if (n == 1) "día" else "días"}"
                "pt" -> "$rangeLabel · $n ${if (n == 1) "dia" else "dias"}"
                // tr：only other——数字后名词不带复数标记，恒同形（§17.1）
                "tr" -> "$rangeLabel · $n gün"
                // hi：两形同形——数词后名词不变格，无二分（§18.1）
                "hi" -> "$rangeLabel · कुल $n दिन"
                // id/in：仅 other——数后不变形，无二分（§19 双键）
                "id", "in" -> "$rangeLabel · total $n hari"
                // th：仅 other——数后名词不变形（§21）
                "th" -> "$rangeLabel · $n วัน"
                // vi：仅 other——数后名词不变形（§21）
                "vi" -> "$rangeLabel · $n ngày"
                // ar：六形 day word（0/1/2/3-10/11-99/其余；§20.1 缺口③ 实测 mod-100 规则）
                "ar" -> "$rangeLabel · إجمالي ${localizeDigits("$n", locale)} ${arDayWord(n)}"
                else -> "$rangeLabel · $n ${if (n == 1) "day" else "days"}"
            }
            return listOf(fallback)
        }
        return lines
    }

    /** 俄语天数词（CLDR ru cardinal：one/few/many；小数 other 不可达，n ≤ 组内天数）。 */
    private fun ruDayWord(n: Int): String = when {
        n % 10 == 1 && n % 100 != 11 -> "день"
        n % 10 in 2..4 && n % 100 !in 12..14 -> "дня"
        else -> "дней"
    }

    /**
     * 阿语天数词（CLDR ar cardinal 六形；§20.1 缺口③ 实测 mod-100 规则）：
     * 2→يومان、n%100∈3..10→أيام（few）、n%100∈11..99→يومًا（many）、
     * 其余（zero/one/other：0、1、100、101、102…）同形 يوم。
     */
    private fun arDayWord(n: Int): String = when {
        n == 2 -> "يومان"
        n % 100 in 3..10 -> "أيام"
        n % 100 in 11..99 -> "يومًا"
        else -> "يوم"
    }

    private fun summaryLimit(granularity: Granularity): Int = when (granularity) {
        Granularity.DAY -> 3
        Granularity.WEEK -> 4
        Granularity.MONTH -> 5
        Granularity.YEAR -> 6
    }

    /** topics：各日倒序 flatMap、按 topic 首见去重（detail 随同），日卡直读。 */
    private fun mergeTopics(days: List<OverviewDayDto>): List<DayTopicDto> {
        val seen = LinkedHashSet<String>()
        val out = ArrayList<DayTopicDto>(minOf(MAX_TOPICS, days.size * 5))  // 容量提示：prompt 约定 ≤5/天
        for (day in days) {
            for (t in day.topics) {
                if (seen.add(t.topic)) out.add(t)
            }
        }
        return out
    }

    /**
     * topics 展示集合（docs/android_timeline_topics.md §3.1）：日卡与 ≤[MAX_TOPICS] 的周/月/年卡保持
     * 「日倒序首见」原样；超上限按**权重优先**筛选——权重 = 期内出现次数（每次重复 +1），高权重在前，
     * 同权重内以卡片 key 为种子随机（确定性：同卡跨重组/轮询稳定）。
     */
    private fun selectTopics(
        days: List<OverviewDayDto>,
        granularity: Granularity,
        key: String,
    ): List<DayTopicDto> {
        val merged = mergeTopics(days)
        if (granularity == Granularity.DAY || merged.size <= MAX_TOPICS) return merged
        val weights = topicWeights(days)
        // shuffled(种子) 后按权重稳定排序 → 同权重保留随机序（高权重优先 + 剩余随机补齐）
        return merged.shuffled(Random(key.hashCode().toLong()))
            .sortedByDescending { weights[it.topic] ?: 0 }
            .take(MAX_TOPICS)
    }

    /** topic 权重 = 期内出现次数（每次重复 +1，首见计 1）。 */
    private fun topicWeights(days: List<OverviewDayDto>): Map<String, Int> {
        val weights = HashMap<String, Int>()
        for (day in days) {
            for (t in day.topics) weights[t.topic] = (weights[t.topic] ?: 0) + 1
        }
        return weights
    }

    private fun parseLocalDate(date: String): LocalDate? =
        runCatching { LocalDate.parse(date) }.getOrNull()
}

/** "2026-W36" → 该 ISO 周周一（1 月 4 日恒在 W01，逆推周一后加周偏移）；非法 key → null。
 * TimelineScreen 分组头与本文件 dateRange() 共用（原 TimelineScreen 私有实现上移）。 */
internal fun mondayOfIsoWeek(weekKey: String): LocalDate? {
    val year = weekKey.take(4).toIntOrNull() ?: return null
    val week = weekKey.substringAfter("-W").toIntOrNull() ?: return null
    if (week !in 1..53) return null
    return LocalDate.of(year, 1, 4)
        .with(WeekFields.ISO.dayOfWeek(), 1L)
        .plusWeeks(week - 1L)
}
