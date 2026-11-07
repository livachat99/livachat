package dev.livachat.app.ui.screens.mylife.timeline

import dev.livachat.app.data.remote.mylife.dto.DayImageDto
import dev.livachat.app.data.remote.mylife.dto.DayTopicDto
import dev.livachat.app.data.remote.mylife.dto.HotSlotDto
import dev.livachat.app.data.remote.mylife.dto.OverviewDayDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PeriodAggregatorTest {

    private fun day(
        date: String,
        summary: String? = null,
        hotSlots: List<HotSlotDto> = emptyList(),
        images: List<DayImageDto> = emptyList(),
        hasPending: Boolean = false,
        topics: List<DayTopicDto> = emptyList(),
        // 该日真实关键帧数（bff counts["video"]）；默认 = 候选张数（最简自洽夹具）
        videoCount: Int = images.size,
        // 该日音频段数（bff counts["audio"]，docs/android_timeline_audio_gallery.md §4.1）
        audioCount: Int = 0,
    ) = OverviewDayDto(
        date = date,
        counts = buildMap {
            if (videoCount > 0) put("video", videoCount)
            if (audioCount > 0) put("audio", audioCount)
        },
        summary = summary,
        hotSlots = hotSlots,
        images = images,
        hasPending = hasPending,
        topics = topics,
    )

    private fun slot(start: Long, end: Long = start + 60_000) =
        HotSlotDto(startMs = start, endMs = end, count = 1)

    private fun img(ts: Long, windowId: String? = null, tag: String = "$ts") =
        DayImageDto(
            frameId = "f$tag",
            tsMs = ts,
            eventRef = "ref-$tag",
            mediaUrl = "/api/mylife/media/dev/s1/video/f$tag.jpg",
            windowId = windowId,
        )

    private fun digest(date: String, granularity: Granularity): PeriodDigest =
        PeriodAggregator.aggregate(listOf(day(date)), granularity)[0]

    private fun topic(t: String, detail: String? = null) = DayTopicDto(topic = t, detail = detail)

    // ---- scope 过滤（§5.1）----

    @Test
    fun `月 scope 过滤日卡只保留该月`() {
        val days = listOf(
            day("2026-08-21"), day("2026-08-20"), day("2026-07-31"),
        )
        val result = PeriodAggregator.aggregate(days, Granularity.DAY, scopeKey = "2026-08")
        assertEquals(listOf("2026-08-21", "2026-08-20"), result.map { it.key })
    }

    @Test
    fun `年 scope 过滤月卡只保留该年`() {
        val days = listOf(
            day("2026-12-01"), day("2026-01-02"), day("2025-11-30"),
        )
        val result = PeriodAggregator.aggregate(days, Granularity.MONTH, scopeKey = "2026")
        assertEquals(listOf("2026-12", "2026-01"), result.map { it.key })
    }

    @Test
    fun `scope 无匹配数据返回空列表`() {
        val days = listOf(day("2026-08-21"))
        assertTrue(PeriodAggregator.aggregate(days, Granularity.DAY, scopeKey = "2025-01").isEmpty())
    }

    // ---- ISO 周分组边界（§5.1/§3.5/§10 风险 7）----

    @Test
    fun `跨月周归周一所在周 key 相同`() {
        // 2026-08-31 周一、2026-09-01 周二 → 同属 2026-W36
        val days = listOf(day("2026-09-01"), day("2026-08-31"))
        val result = PeriodAggregator.aggregate(days, Granularity.WEEK)
        assertEquals(listOf("2026-W36"), result.map { it.key })
        assertEquals("8/31 – 9/6", result[0].rangeLabel)
    }

    @Test
    fun `跨年周 2027-01-01 属 2026-W53`() {
        // 2026 年 1 月 1 日周四（≥周四开年 → 53 周）；2027-01-01 周五位于 2026-W53 内
        val days = listOf(day("2027-01-01"), day("2026-12-31"))
        val result = PeriodAggregator.aggregate(days, Granularity.WEEK)
        assertEquals(listOf("2026-W53"), result.map { it.key })
        assertEquals("12/28 – 1/3", result[0].rangeLabel)
    }

    // ---- 日卡直读（§4.2）----

    @Test
    fun `日卡 summary 直读透传`() {
        val days = listOf(day("2026-08-21", summary = "用户询问了三亚天气。"))
        val digest = PeriodAggregator.aggregate(days, Granularity.DAY)[0]
        assertEquals(listOf("用户询问了三亚天气。"), digest.summaryLines)
    }

    @Test
    fun `日卡空 summary 不渲染 summary 块`() {
        val days = listOf(day("2026-08-21", summary = null))
        val digest = PeriodAggregator.aggregate(days, Granularity.DAY)[0]
        assertTrue(digest.summaryLines.isEmpty())
    }

    @Test
    fun `日卡 hasPending 直读`() {
        assertTrue(PeriodAggregator.aggregate(listOf(day("2026-08-21", hasPending = true)), Granularity.DAY)[0].hasPending)
        assertFalse(PeriodAggregator.aggregate(listOf(day("2026-08-21")), Granularity.DAY)[0].hasPending)
    }

    @Test
    fun `日卡 hotSlots 直映射 bff 日级 slots`() {
        // bff 已按五类口径分段并合并至 ≤3（docs/android_timeline_hotslot.md §4.1），客户端 1:1 映射
        val days = listOf(day("2026-08-21", hotSlots = listOf(slot(1000L, 2000L), slot(5000L, 6000L))))
        val digest = PeriodAggregator.aggregate(days, Granularity.DAY)[0]
        assertEquals(listOf(1000L to 2000L, 5000L to 6000L), digest.hotSlots.map { it.startMs to it.endMs })
    }

    @Test
    fun `日卡 images 轮转后 ts 升序`() {
        // 3 帧互为独立窗口（无 windowId → eventRef 兜底）→ 轮 1 全取，输出 ts 升序
        val days = listOf(day("2026-08-21", images = listOf(img(5000L), img(1000L), img(3000L))))
        val images = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        assertEquals(listOf(1000L, 3000L, 5000L), images.map { it.tsMs })
        assertEquals(listOf("ref-1000", "ref-3000", "ref-5000"), images.map { it.eventRef })
        assertEquals(listOf("f1000", "f3000", "f5000"), images.map { it.frameId })
    }

    @Test
    fun `日卡 images cap 3 轮 1 取满即停`() {
        // 12 个独立窗口 → 轮 1 取满 3（ts 最小的 3 窗），轮 2 不发生
        val days = listOf(day("2026-08-21", images = (0 until 12).map { img(1000L + it) }))
        val images = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        assertEquals((1000L..1002L).toList(), images.map { it.tsMs })
    }

    @Test
    fun `轮转窗口数少于 cap 逐轮补满（dev 9-19 形状：3 窗 3_9_4 帧，周卡 cap 9）`() {
        val w1 = listOf(100L, 400L, 700L).map { img(it, "w1") }
        val w2 = listOf(200L, 500L, 800L, 1100L, 1400L, 1700L, 2000L, 2300L, 2600L).map { img(it, "w2") }
        val w3 = listOf(300L, 600L, 900L, 1200L).map { img(it, "w3") }
        val days = listOf(day("2026-08-21", images = w1 + w2 + w3))
        // 单日数据喂周卡（Q7）：窗口序 w1(100)/w2(200)/w3(300) → 轮 1..3 各窗 1 张 = 9
        val images = PeriodAggregator.aggregate(days, Granularity.WEEK)[0].images
        assertEquals(listOf(100L, 200L, 300L, 400L, 500L, 600L, 700L, 800L, 900L), images.map { it.tsMs })
    }

    @Test
    fun `跨日同窗归一组内轮转`() {
        // w1 跨两天（10..17 + 90）、另有 6 个单帧窗口；若非归组（90 被当作新窗口）
        // 则轮 1 会提前纳入 90、挤掉 w1 的第 3 张（12）—— 断言排除 90 且含 12
        val days = listOf(
            day("2026-08-10", images = (10L..17L).map { img(it, "w1") } + (20L..70L step 10).map { img(it) }),
            day("2026-08-11", images = listOf(img(90L, "w1"))),
        )
        val images = PeriodAggregator.aggregate(days, Granularity.WEEK)[0].images
        // 8 窗口（w1 + 6 单帧 + w1 跨日归组）→ 轮 1 7 张、轮 2 取 w1 第 2 张、轮 3 取 w1 第 3 张 = 9
        assertEquals(listOf(10L, 11L, 12L, 20L, 30L, 40L, 50L, 60L, 70L), images.map { it.tsMs })
    }

    @Test
    fun `分组键兜底 null 与空串 windowId 均按 eventRef 独立成组`() {
        // 若兜底失效（空串被当作真实键，把 ref-20/ref-21 并成一组）→ 轮 1 会选出 30 而非 21
        val images = listOf(img(10L, "w1"), img(11L, "w1"), img(12L, "w1")) +
            listOf(img(20L, ""), img(21L, ""), img(30L), img(31L))
        val days = listOf(day("2026-08-21", images = images))
        val picked = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        // cap 3：轮 1 取 w1#1 / ref-20 / ref-21 即满
        assertEquals(listOf(10L, 20L, 21L), picked.map { it.tsMs })
    }

    @Test
    fun `全部窗口取尽仍不足 cap 返回全部`() {
        val days = listOf(day("2026-08-21", images = listOf(img(100L, "w1"), img(200L, "w1"), img(300L, "w2"))))
        val images = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        assertEquals(listOf(100L, 200L, 300L), images.map { it.tsMs })
    }

    @Test
    fun `同 ts 输出保持轮转序 窗口序以分组键决胜`() {
        // 两窗口首帧同 ts：窗口序 (ts, 键) → w-a 先于 w-b（与 bff 决胜规则一致）
        val days = listOf(day("2026-08-21", images = listOf(
            img(100L, "w-b", tag = "b"),
            img(100L, "w-a", tag = "a"),
        )))
        val images = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        assertEquals(listOf("fa", "fb"), images.map { it.frameId })
    }

    @Test
    fun `重建等价性 bff 池截断形状下选集同规则`() {
        // bff 输出形状（ts 升序 + windowId，池截断：w1 只给前 15 帧、w2 只有 3 帧）
        // day cap 3 → 轮 1 取 w1+w2 各 1、轮 2 取 w1 第 2 张即满 → 排除 2 与 100 之后的 w2 帧
        val w1 = (0L until 15L).map { img(it, "w1") }
        val w2 = (100L..102L).map { img(it, "w2") }
        val days = listOf(day("2026-08-21", images = w1 + w2))
        val images = PeriodAggregator.aggregate(days, Granularity.DAY)[0].images
        assertEquals(listOf(0L, 1L, 100L), images.map { it.tsMs })
    }

    // ---- imageTotal（计数徽标口径，§3.2 / F7）----

    @Test
    fun `imageTotal 为各天 video 计数之和（跨日）`() {
        val days = listOf(
            day("2026-08-10", images = listOf(img(1L), img(2L), img(3L)), videoCount = 16),
            day("2026-08-11", images = listOf(img(4L)), videoCount = 4),
        )
        val week = PeriodAggregator.aggregate(days, Granularity.WEEK)[0]
        assertEquals(20, week.imageTotal)
        assertTrue("实际帧数 > 卡片张数 → 显示徽标", week.imageTotal > week.images.size)
    }

    @Test
    fun `imageTotal 等于卡片张数时不显示徽标`() {
        val days = listOf(day("2026-08-21", images = listOf(img(1L), img(2L), img(3L)), videoCount = 3))
        val digest = PeriodAggregator.aggregate(days, Granularity.DAY)[0]
        assertEquals(3, digest.imageTotal)
        assertFalse("无溢出 → 不显示徽标", digest.imageTotal > digest.images.size)
    }

    @Test
    fun `counts 缺失时 imageTotal 为 0`() {
        val days = listOf(day("2026-08-21", images = listOf(img(1L)), videoCount = 0))
        val digest = PeriodAggregator.aggregate(days, Granularity.DAY)[0]
        assertEquals(0, digest.imageTotal)
        assertFalse(digest.imageTotal > digest.images.size)
    }

    @Test
    fun `日卡 topics 直读`() {
        val days = listOf(day("2026-08-21", topics = listOf(topic("AI", "详1"), topic("旅行", "详2"))))
        val topics = PeriodAggregator.aggregate(days, Granularity.DAY)[0].topics
        assertEquals(listOf("AI", "旅行"), topics.map { it.topic })
        assertEquals(listOf("详1", "详2"), topics.map { it.detail })
    }

    // ---- 周/月/年：hotSlots 恒空（docs/android_timeline_hotslot.md §4.1）----

    @Test
    fun `周月年卡 hotSlots 恒为空`() {
        val days = listOf(
            day("2026-08-10", hotSlots = listOf(slot(1000L, 2000L))),
            day("2026-08-11", hotSlots = listOf(slot(4000L, 5000L))),
        )
        assertTrue(PeriodAggregator.aggregate(days, Granularity.WEEK).all { it.hotSlots.isEmpty() })
        assertTrue(PeriodAggregator.aggregate(days, Granularity.MONTH).all { it.hotSlots.isEmpty() })
        assertTrue(PeriodAggregator.aggregate(days, Granularity.YEAR).all { it.hotSlots.isEmpty() })
    }

    // ---- 周聚合：summary / images / topics（§4.2）----

    @Test
    fun `周 summary 非空去重按日期倒序且 summaryLimit 4`() {
        val days = listOf(
            day("2026-08-10", summary = "早上的对话"),
            day("2026-08-11", summary = "早上的对话"),
            day("2026-08-12", summary = "下午的对话"),
        )
        val digest = PeriodAggregator.aggregate(days, Granularity.WEEK)[0]
        assertEquals(listOf("下午的对话", "早上的对话"), digest.summaryLines)
        assertEquals(4, digest.summaryLimit)
    }

    @Test
    fun `周全空 summary 降级为范围标签共 N 天`() {
        val days = listOf(day("2026-08-10"), day("2026-08-11"), day("2026-08-12"))
        val digest = PeriodAggregator.aggregate(days, Granularity.WEEK)[0]
        assertEquals(listOf("8/10 – 8/16共 3 天"), digest.summaryLines)
    }

    @Test
    fun `周卡 images cap 9 且 ts 升序`() {
        // 12 个独立窗口（跨两日）→ 轮 1 取满 9（ts 最小 9 个）
        val days = (10..11).map { d ->
            day("2026-08-%02d".format(d), images = (0 until 6).map { i -> img((d * 100 + i).toLong()) })
        }
        val images = PeriodAggregator.aggregate(days, Granularity.WEEK)[0].images
        assertEquals(
            listOf(1000L, 1001L, 1002L, 1003L, 1004L, 1005L, 1100L, 1101L, 1102L),
            images.map { it.tsMs },
        )
    }

    @Test
    fun `周 topics 日倒序首见去重（≤50 全量不随机）`() {
        val days = listOf(
            day("2026-08-12", topics = listOf(topic("AI", "详1"), topic("美食", "详2"))),
            day("2026-08-11", topics = listOf(topic("AI", "详0"), topic("旅行", "详3"))),
            day("2026-08-10", topics = listOf(topic("z", "详4"), topic("y", "详5"), topic("x", "详6"))),
        )
        val topics = PeriodAggregator.aggregate(days, Granularity.WEEK)[0].topics
        // 日倒序首见去重：AI(详1) 先于 08-10 的 AI(详0)；Q1 起 ≤100 不再截断（原 take(5)）
        assertEquals(listOf("AI", "美食", "旅行", "z", "y", "x"), topics.map { it.topic })
        assertEquals(listOf("详1", "详2", "详3", "详4", "详5", "详6"), topics.map { it.detail })
    }

    @Test
    fun `畸形日期被剔除不崩（外部数据容错）`() {
        val days = listOf(day("2026-08-21"), day("2026-08-%02d"), day("not-a-date"))
        assertEquals(
            listOf("2026-08-21"),
            PeriodAggregator.aggregate(days, Granularity.DAY).map { it.key },
        )
        // 月粒度同理（原实现在 rangeLabel 里 toInt() 解析日期段 → 非法值崩溃）
        assertEquals(
            listOf("2026-08"),
            PeriodAggregator.aggregate(days, Granularity.MONTH).map { it.key },
        )
    }

    @Test
    fun `日卡 topics 全量透出（超 5 不截断）`() {
        val names = (1..7).map { "t$it" }
        val topics = PeriodAggregator.aggregate(
            listOf(day("2026-08-21", topics = names.map { topic(it) })),
            Granularity.DAY,
        )[0].topics
        assertEquals(names, topics.map { it.topic })
    }

    @Test
    fun `年卡 topics 超 50 按权重优先取 50 且同 key 稳定`() {
        // 12 天 × 5 个唯一 topic（60 个）+ 跨天重复主题：热点A(12 次)/热点B(8 次)/热点C(3 次)
        val days = (1..12).map { d ->
            val singles = (0 until 5).map { i -> topic("u${d}_$i") }
            val repeats = buildList {
                add(topic("热点A"))
                if (d <= 8) add(topic("热点B"))
                if (d <= 3) add(topic("热点C"))
            }
            day("2026-08-%02d".format(d), topics = singles + repeats)
        }
        val first = PeriodAggregator.aggregate(days, Granularity.YEAR)[0].topics
        // 输入序打乱后同 key 结果不变（组内排序 + 种子随机均为确定性）
        val second = PeriodAggregator.aggregate(days.shuffled(), Granularity.YEAR)[0].topics

        assertEquals(50, first.size)                                     // 上限 50
        assertEquals(first.map { it.topic }, second.map { it.topic })    // 同卡稳定
        assertEquals(listOf("热点A", "热点B", "热点C"), first.take(3).map { it.topic })  // 权重优先（12/8/3）
        assertEquals(47, first.drop(3).map { it.topic }.toSet().size)    // 其余名额为互不重复的单次 topic
        val all = days.sortedByDescending { it.date }.flatMap { it.topics }.map { it.topic }
        assertTrue(all.containsAll(first.map { it.topic }))              // 选中集合 ⊆ 全量
        assertNotEquals(all.distinct().take(50), first.map { it.topic }) // 非"取前 50"（权重+随机生效）
    }

    @Test
    fun `年卡 topics 恰 50 保持日倒序首见序（不重排）`() {
        val days = (1..10).map { d ->
            day("2026-08-%02d".format(d), topics = (0 until 5).map { i -> topic("t${d}_$i") })
        }
        val topics = PeriodAggregator.aggregate(days, Granularity.YEAR)[0].topics
        assertEquals(50, topics.size)
        assertEquals(
            (10 downTo 1).flatMap { d -> (0 until 5).map { "t${d}_$it" } },
            topics.map { it.topic },
        )
    }

    // ---- 月/年配额与降级（§4.2）----

    @Test
    fun `月卡 images cap 12`() {
        val days = (1..4).map { d -> day("2026-08-0$d", images = (0 until 4).map { i -> img((d * 100 + i).toLong()) }) }
        assertEquals(12, PeriodAggregator.aggregate(days, Granularity.MONTH)[0].images.size)
    }

    @Test
    fun `年卡 images cap 15`() {
        val days = (1..5).map { m -> day("2026-0$m-01", images = (0 until 4).map { i -> img((m * 100 + i).toLong()) }) }
        assertEquals(15, PeriodAggregator.aggregate(days, Granularity.YEAR)[0].images.size)
    }

    // ---- dateRange（Gallery 请求范围，§3.2）----

    @Test
    fun `dateRange 四粒度`() {
        assertEquals("2026-08-21" to "2026-08-21", digest("2026-08-21", Granularity.DAY).dateRange())
        // 2026-09-01 属 2026-W36（周一 08-31）
        assertEquals("2026-08-31" to "2026-09-06", digest("2026-09-01", Granularity.WEEK).dateRange())
        assertEquals("2026-08-01" to "2026-08-31", digest("2026-08-21", Granularity.MONTH).dateRange())
        assertEquals("2026-01-01" to "2026-12-31", digest("2026-08-21", Granularity.YEAR).dateRange())
    }

    @Test
    fun `dateRange 跨年周与闰月边界`() {
        // 2027-01-01 属 2026-W53 → 周一 2026-12-28
        assertEquals("2026-12-28" to "2027-01-03", digest("2027-01-01", Granularity.WEEK).dateRange())
        assertEquals("2028-02-01" to "2028-02-29", digest("2028-02-10", Granularity.MONTH).dateRange())
    }

    @Test
    fun `月卡全空 summary 降级共 N 天`() {
        val days = listOf(day("2026-08-10"), day("2026-08-11"))
        assertEquals(listOf("2026年8月共 2 天"), PeriodAggregator.aggregate(days, Granularity.MONTH)[0].summaryLines)
    }

    @Test
    fun `月卡全空 summary 降级 ja 计 N 日（§9 缺口③）`() {
        val days = listOf(day("2026-08-10"), day("2026-08-11"))
        assertEquals(
            listOf("2026年8月・計 2 日"),
            PeriodAggregator.aggregate(days, Granularity.MONTH, locale = java.util.Locale.JAPAN)[0].summaryLines,
        )
        // zh 回归不受影响（缺省 locale）
        assertEquals(listOf("2026年8月共 2 天"), PeriodAggregator.aggregate(days, Granularity.MONTH)[0].summaryLines)
    }

    @Test
    fun `月卡全空 summary 降级 ko 총 N일（§10 缺口③）`() {
        val days = listOf(day("2026-08-10"), day("2026-08-11"))
        assertEquals(
            listOf("2026년 8월・총 2일"),
            PeriodAggregator.aggregate(days, Granularity.MONTH, locale = java.util.Locale.KOREA)[0].summaryLines,
        )
    }

    @Test
    fun `月卡全空 summary 降级 ru всего N день-дня-дней 三形（§11 缺口③）`() {
        val ru = java.util.Locale("ru")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = ru,
        )[0].summaryLines[0]
        assertEquals("август 2026 · всего 1 день", line(1))
        assertEquals("август 2026 · всего 2 дня", line(2))
        assertEquals("август 2026 · всего 5 дней", line(5))
        assertEquals("август 2026 · всего 11 дней", line(11))
        assertEquals("август 2026 · всего 12 дней", line(12))
        assertEquals("август 2026 · всего 13 дней", line(13))
        assertEquals("август 2026 · всего 14 дней", line(14))
        assertEquals("август 2026 · всего 21 день", line(21))
        assertEquals("август 2026 · всего 22 дня", line(22))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 ru 日-月零填充（§11 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17.08 – 23.08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("ru"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03.08 – 09.08）
        assertEquals(
            "03.08 – 09.08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("ru"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `月卡全空 summary 降级 de Tag-Tage（§12 缺口③）`() {
        val de = java.util.Locale.GERMAN
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = de,
        )[0].summaryLines[0]
        assertEquals("August 2026 · 1 Tag", line(1))
        assertEquals("August 2026 · 2 Tage", line(2))
        assertEquals("August 2026 · 5 Tage", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 de 日-月带尾点（§12 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17.08. – 23.08.",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale.GERMAN)[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03.08. – 09.08.）
        assertEquals(
            "03.08. – 09.08.",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale.GERMAN)[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `月卡全空 summary 降级 fr jour-jours（§13 缺口③）`() {
        val fr = java.util.Locale.FRENCH
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = fr,
        )[0].summaryLines[0]
        assertEquals("août 2026 · 1 jour", line(1))
        assertEquals("août 2026 · 2 jours", line(2))
        assertEquals("août 2026 · 5 jours", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 fr 日-月斜线零填充（§13 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale.FRENCH)[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale.FRENCH)[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `月卡全空 summary 降级 it giorno-giorni（§14 缺口③）`() {
        val it = java.util.Locale.ITALIAN
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = it,
        )[0].summaryLines[0]
        assertEquals("agosto 2026 · 1 giorno", line(1))
        assertEquals("agosto 2026 · 2 giorni", line(2))
        assertEquals("agosto 2026 · 5 giorni", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 it 日-月斜线零填充（§14 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale.ITALIAN)[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale.ITALIAN)[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 it mar 消歧（§14 缺口⑤）`() {
        // 2026-03-17 为三月内的周二 → 「17 mar · martedì」（星期 FULL 消除 mar×mar 歧义）
        val days = listOf(day("2026-03-17"))
        assertEquals(
            "17 mar · martedì",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale.ITALIAN)[0].rangeLabel,
        )
        // zh 缺省回归锁：三月周二仍为「3月17日 · 周二」
        assertEquals("3月17日 · 周二", PeriodAggregator.aggregate(days, Granularity.DAY)[0].rangeLabel)
    }

    @Test
    fun `月卡全空 summary 降级 es día-días（§15 缺口③）`() {
        val es = java.util.Locale("es")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = es,
        )[0].summaryLines[0]
        assertEquals("agosto de 2026 · 1 día", line(1))
        assertEquals("agosto de 2026 · 2 días", line(2))
        assertEquals("agosto de 2026 · 5 días", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 es 日-月斜线零填充（§15 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("es"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("es"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 es mar 消歧（§15 缺口⑤）`() {
        // 2026-03-17 为三月内的周二 → 「17 mar · martes」（星期 FULL 消除 mar×mar 歧义）
        val days = listOf(day("2026-03-17"))
        assertEquals(
            "17 mar · martes",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("es"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 pt dia-dias（§16 缺口③）`() {
        val pt = java.util.Locale("pt", "BR")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = pt,
        )[0].summaryLines[0]
        assertEquals("agosto de 2026 · 1 dia", line(1))
        assertEquals("agosto de 2026 · 2 dias", line(2))
        assertEquals("agosto de 2026 · 5 dias", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 pt 日-月斜线零填充（§16 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("pt", "BR"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("pt", "BR"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 pt 星期保持 SHORT（§16 碰撞空集）`() {
        // 2026-03-17 三月周二 → 「17 mar. · ter.」（葡语无 mar×mar 碰撞，星期保持 SHORT）
        val days = listOf(day("2026-03-17"))
        assertEquals(
            "17 mar. · ter.",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("pt", "BR"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 tr gün 恒同形（§17 缺口③）`() {
        val tr = java.util.Locale("tr")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = tr,
        )[0].summaryLines[0]
        // only other：1/2/5 天恒同形（数字后名词不带复数标记）
        assertEquals("Ağustos 2026 · 1 gün", line(1))
        assertEquals("Ağustos 2026 · 2 gün", line(2))
        assertEquals("Ağustos 2026 · 5 gün", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 tr 点分零填充（§17 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17.08 – 23.08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("tr"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03.08 – 09.08）
        assertEquals(
            "03.08 – 09.08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("tr"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 tr 星期保持 SHORT（§17 碰撞空集）`() {
        // 2026-03-17 三月周二 → 「17 Mar · Sal」（土语无碰撞，星期保持 SHORT 大写）
        val days = listOf(day("2026-03-17"))
        assertEquals(
            "17 Mar · Sal",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("tr"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 hi कुल N दिन 恒同形（§18 缺口③）`() {
        val hi = java.util.Locale("hi")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = hi,
        )[0].summaryLines[0]
        // 数词后名词不变格：1/2/5 天恒同形
        assertEquals("अगस्त 2026 · कुल 1 दिन", line(1))
        assertEquals("अगस्त 2026 · कुल 2 दिन", line(2))
        assertEquals("अगस्त 2026 · कुल 5 दिन", line(5))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 hi 日-月斜线零填充（§18 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("hi"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("hi"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 hi 星期保持 SHORT（§18 碰撞空集）`() {
        // 2026-08-21 周五 → 「21 अग॰ · शुक्र」（印语无碰撞，星期保持 SHORT 天城文）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "21 अग॰ · शुक्र",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("hi"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 id total N hari 恒同形（§19 缺口③）`() {
        val id = java.util.Locale("id")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = id,
        )[0].summaryLines[0]
        // 仅 other：1/2/5 天恒同形
        assertEquals("Agustus 2026 · total 1 hari", line(1))
        assertEquals("Agustus 2026 · total 2 hari", line(2))
        assertEquals("Agustus 2026 · total 5 hari", line(5))
        // 旧码 Locale("in")（归一为 id）同断言——双键防御回归锁
        assertEquals(
            "Agustus 2026 · total 2 hari",
            PeriodAggregator.aggregate(
                (1..2).map { day("2026-08-%02d".format(it)) },
                Granularity.MONTH,
                locale = java.util.Locale("in"),
            )[0].summaryLines[0],
        )
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `周卡标签 id 日-月斜线零填充（§19 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("id"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "03/08 – 09/08",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("id"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `日卡标签 id 星期保持 SHORT（§19 碰撞空集）`() {
        // 2026-08-21 周五 → 「21 Agu · Jum」（印语无碰撞，星期保持 SHORT 大写）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "21 Agu · Jum",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("id"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 th วัน 恒同形（§21 缺口③）`() {
        val th = java.util.Locale("th")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = th,
        )[0].summaryLines[0]
        // 仅 other：1/2/5 天恒同形；年份为 Gregorian 2026，非佛历
        assertEquals("สิงหาคม 2026 · 1 วัน", line(1))
        assertEquals("สิงหาคม 2026 · 2 วัน", line(2))
        assertEquals("สิงหาคม 2026 · 5 วัน", line(5))
    }

    @Test
    fun `月卡全空 summary 降级 vi ngày 恒同形（§21 缺口③）`() {
        val vi = java.util.Locale("vi")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = vi,
        )[0].summaryLines[0]
        assertEquals("Tháng 8 năm 2026 · 1 ngày", line(1))
        assertEquals("Tháng 8 năm 2026 · 2 ngày", line(2))
        assertEquals("Tháng 8 năm 2026 · 5 ngày", line(5))
    }

    @Test
    fun `周卡标签 th vi 日-月斜线零填充（§21 缺口④）`() {
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("th"))[0].rangeLabel,
        )
        assertEquals(
            "17/08 – 23/08",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("vi"))[0].rangeLabel,
        )
    }

    @Test
    fun `日卡标签 th vi 星期本地化（§21）`() {
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "21 ส.ค. · ศ.",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("th"))[0].rangeLabel,
        )
        assertEquals(
            "21 thg 8 · Thứ Sáu",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("vi"))[0].rangeLabel,
        )
    }

    @Test
    fun `日卡标签 ar 日先月后 + 阿数 + 星期 SHORT（§20 缺口①⑤）`() {
        // 2026-08-21 周五 → 「٢١ أغسطس · الجمعة」（SHORT==FULL、碰撞空集）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "٢١ أغسطس · الجمعة",
            PeriodAggregator.aggregate(days, Granularity.DAY, locale = java.util.Locale("ar"))[0].rangeLabel,
        )
    }

    @Test
    fun `月卡全空 summary 降级 ar 六形 day word + 阿数（§20 缺口③）`() {
        val ar = java.util.Locale("ar")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (1..n).map { day("2026-08-%02d".format(it)) },
            Granularity.MONTH,
            locale = ar,
        )[0].summaryLines[0]
        assertEquals("أغسطس ٢٠٢٦ · إجمالي ١ يوم", line(1))
        assertEquals("أغسطس ٢٠٢٦ · إجمالي ٢ يومان", line(2))
        assertEquals("أغسطس ٢٠٢٦ · إجمالي ٥ أيام", line(5))
        assertEquals("أغسطس ٢٠٢٦ · إجمالي ١١ يومًا", line(11))
        assertEquals("أغسطس ٢٠٢٦ · إجمالي ٣١ يومًا", line(31))
        // zh 回归不受影响（缺省 locale）
        assertEquals("2026年8月共 2 天", PeriodAggregator.aggregate(
            (1..2).map { day("2026-08-%02d".format(it)) }, Granularity.MONTH,
        )[0].summaryLines[0])
    }

    @Test
    fun `年卡全空 summary ar mod-100 边界 0-1-2-3-11-99-100-103-111 形（§20 缺口③）`() {
        val ar = java.util.Locale("ar")
        fun line(n: Int) = PeriodAggregator.aggregate(
            (0 until n).map {
                day(java.time.LocalDate.of(2026, 1, 1).plusDays(it.toLong()).toString())
            },
            Granularity.YEAR,
            locale = ar,
        )[0].summaryLines[0]
        // one/other 同形 يوم；two يومان；3..10 أيام（few）；11..99 يومًا（many）；100+ 落 other
        assertEquals("٢٠٢٦ · إجمالي ١ يوم", line(1))
        assertEquals("٢٠٢٦ · إجمالي ٢ يومان", line(2))
        assertEquals("٢٠٢٦ · إجمالي ٣ أيام", line(3))
        assertEquals("٢٠٢٦ · إجمالي ١٠ أيام", line(10))
        assertEquals("٢٠٢٦ · إجمالي ١١ يومًا", line(11))
        assertEquals("٢٠٢٦ · إجمالي ٩٩ يومًا", line(99))
        assertEquals("٢٠٢٦ · إجمالي ١٠٠ يوم", line(100))
        assertEquals("٢٠٢٦ · إجمالي ١٠١ يوم", line(101))
        assertEquals("٢٠٢٦ · إجمالي ١٠٢ يوم", line(102))
        assertEquals("٢٠٢٦ · إجمالي ١٠٣ أيام", line(103))
        assertEquals("٢٠٢٦ · إجمالي ١١١ يومًا", line(111))
    }

    @Test
    fun `周卡标签 ar 日-月斜线零填充 + 阿数（§20 缺口④）`() {
        // 2026-08-21 周五 → 所在 ISO 周 = 08-17（周一）– 08-23（周日）
        val days = listOf(day("2026-08-21"))
        assertEquals(
            "١٧/٠٨ – ٢٣/٠٨",
            PeriodAggregator.aggregate(days, Granularity.WEEK, locale = java.util.Locale("ar"))[0].rangeLabel,
        )
        // 个位数日零填充（2026-08-03 周一 → 03/08 – 09/08）
        assertEquals(
            "٠٣/٠٨ – ٠٩/٠٨",
            PeriodAggregator.aggregate(listOf(day("2026-08-03")), Granularity.WEEK, locale = java.util.Locale("ar"))[0].rangeLabel,
        )
        // 其余语言 M/D 行为不变（zh 缺省回归锁）
        assertEquals("8/17 – 8/23", PeriodAggregator.aggregate(days, Granularity.WEEK)[0].rangeLabel)
    }

    @Test
    fun `summaryLimit 按粒度递增`() {
        val days = listOf(day("2026-08-10", summary = "s"))
        assertEquals(3, PeriodAggregator.aggregate(days, Granularity.DAY)[0].summaryLimit)
        assertEquals(4, PeriodAggregator.aggregate(days, Granularity.WEEK)[0].summaryLimit)
        assertEquals(5, PeriodAggregator.aggregate(days, Granularity.MONTH)[0].summaryLimit)
        assertEquals(6, PeriodAggregator.aggregate(days, Granularity.YEAR)[0].summaryLimit)
    }

    @Test
    fun `组内任一日 pending 标记 hasPending`() {
        val days = listOf(day("2026-08-10", hasPending = true), day("2026-08-11"))
        assertTrue(PeriodAggregator.aggregate(days, Granularity.WEEK)[0].hasPending)
    }

    // ---- rangeLabel / 排序（§3.3）----

    @Test
    fun `日卡标签含星期几`() {
        // 2026-08-21 为周五（2026-01-01 周四 + 232 天 = +1 天）
        val days = listOf(day("2026-08-21"))
        assertEquals("8月21日 · 周五", PeriodAggregator.aggregate(days, Granularity.DAY)[0].rangeLabel)
    }

    @Test
    fun `月卡年卡标签`() {
        val days = listOf(day("2026-08-21"))
        assertEquals("2026年8月", PeriodAggregator.aggregate(days, Granularity.MONTH)[0].rangeLabel)
        assertEquals("2026年", PeriodAggregator.aggregate(days, Granularity.YEAR)[0].rangeLabel)
    }

    @Test
    fun `结果按 key 倒序与输入顺序无关`() {
        val days = listOf(day("2026-08-21"), day("2026-08-01"), day("2026-07-15"))
        val result = PeriodAggregator.aggregate(days.shuffled(), Granularity.DAY)
        assertEquals(listOf("2026-08-21", "2026-08-01", "2026-07-15"), result.map { it.key })
        val months = PeriodAggregator.aggregate(days.shuffled(), Granularity.MONTH)
        assertEquals(listOf("2026-08", "2026-07"), months.map { it.key })
    }

    // ---- audioCount（docs/android_timeline_audio_gallery.md §4.1 R1）----

    @Test
    fun `audioCount 日卡取当日段数`() {
        val days = listOf(day("2026-09-21", audioCount = 2), day("2026-09-22", audioCount = 14))
        val result = PeriodAggregator.aggregate(days, Granularity.DAY)
        assertEquals(14, result[0].audioCount)   // 09-22（倒序首位）
        assertEquals(2, result[1].audioCount)
    }

    @Test
    fun `audioCount 仅日卡非零（周月年恒 0）`() {
        val days = listOf(day("2026-09-21", audioCount = 2), day("2026-09-22", audioCount = 3))
        assertEquals(0, PeriodAggregator.aggregate(days, Granularity.WEEK)[0].audioCount)
        assertEquals(0, PeriodAggregator.aggregate(days, Granularity.MONTH)[0].audioCount)
        assertEquals(0, PeriodAggregator.aggregate(days, Granularity.YEAR)[0].audioCount)
    }

    @Test
    fun `audioCount 无 audio 日为 0`() {
        val days = listOf(day("2026-09-22"))
        assertEquals(0, PeriodAggregator.aggregate(days, Granularity.DAY)[0].audioCount)
    }
}
