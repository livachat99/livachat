import XCTest
@testable import LivaChatApp

final class MylifeAggregatorTests: XCTestCase {
    private let en = Locale(identifier: "en")
    private let zh = Locale(identifier: "zh-Hans")

    private func image(_ ref: String, ts: Int64, window: String? = nil) -> MylifeDayImage {
        MylifeDayImage(
            url: URL(string: "https://example.com/\(ref).jpg")!,
            tsMs: ts,
            eventRef: ref,
            frameId: "f-\(ref)",
            windowId: window
        )
    }

    private func day(
        _ date: String,
        summary: String?,
        topics: [MylifeDayTopic] = [],
        images: [MylifeDayImage] = [],
        video: Int = 0,
        audio: Int = 0,
        pending: Bool = false
    ) -> MylifeDay {
        MylifeDay(
            date: date,
            counts: [EventType.video: video, EventType.audio: audio],
            summary: summary,
            hotSlots: [MylifeHotSlot(startMs: 1, endMs: 2)],
            images: images,
            hasPending: pending,
            topics: topics
        )
    }

    func testDayDigest() {
        let topics = [MylifeDayTopic(topic: "t1", searchWords: "sw1"),
                      MylifeDayTopic(topic: "t2", searchWords: nil)]
        let days = [
            day("2026-08-21", summary: "A", topics: topics,
                images: [image("a", ts: 1), image("b", ts: 2), image("c", ts: 3), image("d", ts: 4)],
                video: 5, audio: 2, pending: true)
        ]
        let digest = MylifeAggregator.aggregate(days: days, granularity: .day, locale: en)[0]
        XCTAssertEqual(digest.key, "2026-08-21")
        XCTAssertEqual(digest.rangeLabel, "Aug 21 · Fri")
        XCTAssertEqual(digest.hotSlots.count, 1)
        XCTAssertEqual(digest.summaryLines, ["A"])
        XCTAssertEqual(digest.summaryLimit, 3)
        XCTAssertEqual(digest.topics.map(\.topic), ["t1", "t2"])
        XCTAssertEqual(digest.images.count, 3)
        XCTAssertEqual(digest.imageTotal, 5)
        XCTAssertEqual(digest.audioCount, 2)
        XCTAssertTrue(digest.hasPending)
        XCTAssertEqual(digest.dateRange()?.from, "2026-08-21")
        XCTAssertEqual(digest.dateRange()?.to, "2026-08-21")
    }

    func testWeekMonthYearDigestsAndSummaryDeduplication() {
        let days = [
            day("2026-08-21", summary: "A", topics: [MylifeDayTopic(topic: "t1", searchWords: nil)],
                images: [image("a", ts: 1), image("b", ts: 2)], video: 3, audio: 2),
            day("2026-08-20", summary: "B", topics: [MylifeDayTopic(topic: "t2", searchWords: nil)],
                images: [image("c", ts: 3)], video: 1),
            day("2026-08-19", summary: "A", topics: [MylifeDayTopic(topic: "t1", searchWords: nil)],
                images: [image("d", ts: 4), image("e", ts: 5)], video: 2)
        ]

        let week = MylifeAggregator.aggregate(days: days, granularity: .week, locale: en)[0]
        XCTAssertEqual(week.key, "2026-W34")
        XCTAssertEqual(week.rangeLabel, "8/17 – 8/23")
        XCTAssertEqual(week.summaryLines, ["A", "B"])
        XCTAssertEqual(week.summaryLimit, 4)
        XCTAssertTrue(week.hotSlots.isEmpty)
        XCTAssertEqual(week.audioCount, 0)
        XCTAssertEqual(week.imageTotal, 6)
        XCTAssertEqual(week.images.count, 5)
        XCTAssertEqual(week.dateRange()?.from, "2026-08-17")
        XCTAssertEqual(week.dateRange()?.to, "2026-08-23")

        let month = MylifeAggregator.aggregate(days: days, granularity: .month, locale: en)[0]
        XCTAssertEqual(month.key, "2026-08")
        XCTAssertEqual(month.rangeLabel, "Aug 2026")
        XCTAssertEqual(month.summaryLimit, 5)
        XCTAssertEqual(month.dateRange()?.from, "2026-08-01")
        XCTAssertEqual(month.dateRange()?.to, "2026-08-31")

        let year = MylifeAggregator.aggregate(days: days, granularity: .year, locale: en)[0]
        XCTAssertEqual(year.key, "2026")
        XCTAssertEqual(year.rangeLabel, "2026")
        XCTAssertEqual(year.summaryLimit, 6)
        XCTAssertEqual(year.dateRange()?.from, "2026-01-01")
        XCTAssertEqual(year.dateRange()?.to, "2026-12-31")
    }

    func testSummaryFallbackUsesLoadedDayCount() {
        let days = [
            day("2026-08-21", summary: nil),
            day("2026-08-20", summary: "   "),
            day("2026-08-19", summary: nil)
        ]
        let enDigest = MylifeAggregator.aggregate(days: days, granularity: .month, locale: en)[0]
        XCTAssertEqual(enDigest.summaryLines, ["Aug 2026 · 3 days"])
        let enSingular = MylifeAggregator.aggregate(
            days: [day("2026-08-21", summary: nil)],
            granularity: .month,
            locale: en
        )[0]
        XCTAssertEqual(enSingular.summaryLines, ["Aug 2026 · 1 day"])
        let zhDigest = LocalizationTestSupport.withBundle("zh-Hans") {
            MylifeAggregator.aggregate(days: days, granularity: .month, locale: zh)[0]
        }
        XCTAssertEqual(zhDigest.summaryLines, ["2026年8月共 3 天"])
        let zhHantDigest = LocalizationTestSupport.withBundle("zh-Hant") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "zh-Hant")
            )[0]
        }
        XCTAssertEqual(zhHantDigest.summaryLines, ["2026年8月共 3 天"])
        let jaDigest = LocalizationTestSupport.withBundle("ja") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "ja")
            )[0]
        }
        XCTAssertEqual(jaDigest.summaryLines, ["2026年8月・計 3 日"])
        let koDigest = LocalizationTestSupport.withBundle("ko") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "ko")
            )[0]
        }
        XCTAssertEqual(koDigest.summaryLines, ["2026년 8월・총 3일"])
        let ruDigest = LocalizationTestSupport.withBundle("ru") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "ru")
            )[0]
        }
        XCTAssertEqual(ruDigest.summaryLines, ["август 2026 · всего 3 дня"])
        let deDigest = LocalizationTestSupport.withBundle("de") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "de")
            )[0]
        }
        XCTAssertEqual(deDigest.summaryLines, ["August 2026 · 3 Tage"])
        let frDigest = LocalizationTestSupport.withBundle("fr") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "fr")
            )[0]
        }
        XCTAssertEqual(frDigest.summaryLines, ["août 2026 · 3 jours"])
        let itDigest = LocalizationTestSupport.withBundle("it") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "it")
            )[0]
        }
        XCTAssertEqual(itDigest.summaryLines, ["agosto 2026 · 3 giorni"])
        let esDigest = LocalizationTestSupport.withBundle("es") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "es")
            )[0]
        }
        XCTAssertEqual(esDigest.summaryLines, ["agosto de 2026 · 3 días"])
        let ptDigest = LocalizationTestSupport.withBundle("pt") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "pt-BR")
            )[0]
        }
        XCTAssertEqual(ptDigest.summaryLines, ["agosto de 2026 · 3 dias"])
        let hiDigest = LocalizationTestSupport.withBundle("hi") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "hi")
            )[0]
        }
        XCTAssertEqual(hiDigest.summaryLines, ["अगस्त 2026 · कुल 3 दिन"])
        let idDigest = LocalizationTestSupport.withBundle("id") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "id")
            )[0]
        }
        XCTAssertEqual(idDigest.summaryLines, ["Agustus 2026 · total 3 hari"])
        let thDigest = LocalizationTestSupport.withBundle("th") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "th")
            )[0]
        }
        XCTAssertEqual(thDigest.summaryLines, ["สิงหาคม 2026 · 3 วัน"])
        let viDigest = LocalizationTestSupport.withBundle("vi") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "vi")
            )[0]
        }
        XCTAssertEqual(viDigest.summaryLines, ["Tháng 8 năm 2026 · 3 ngày"])
        let arDigest = LocalizationTestSupport.withBundle("ar") {
            MylifeAggregator.aggregate(
                days: days,
                granularity: .month,
                locale: Locale(identifier: "ar_SA")
            )[0]
        }
        XCTAssertEqual(
            arDigest.summaryLines.map(LocalizationTestSupport.strippingBidiControls),
            ["أغسطس ٢٠٢٦ · إجمالي ٣ أيام"]
        )
    }

    func testArabicPeriodSummaryPluralBoundaries() {
        let arabic = Locale(identifier: "ar_SA")
        LocalizationTestSupport.withBundle("ar") {
            for (count, dayWord) in [(1, "يوم"), (2, "يومان"), (3, "أيام"), (11, "يومًا")] {
                let days = (0..<count).map { offset in
                    day(MylifeDateLabels.offsetDateKey(
                        year: 2026,
                        month: 8,
                        day: 21,
                        days: -offset
                    )!, summary: nil)
                }
                let digest = MylifeAggregator.aggregate(
                    days: days,
                    granularity: .month,
                    locale: arabic
                )[0]
                XCTAssertEqual(
                    digest.summaryLines.map(LocalizationTestSupport.strippingBidiControls),
                    ["أغسطس ٢٠٢٦ · إجمالي \(LocalizedNumber.integer(count, locale: arabic)) \(dayWord)"]
                )
            }
        }
    }

    func testRussianPeriodSummaryPluralBoundaries() {
        let russian = Locale(identifier: "ru")
        LocalizationTestSupport.withBundle("ru") {
            for (count, dayWord) in [(1, "день"), (2, "дня"), (5, "дней"), (11, "дней")] {
                let days = (0..<count).map { offset in
                    day(MylifeDateLabels.offsetDateKey(
                        year: 2026,
                        month: 8,
                        day: 21,
                        days: -offset
                    )!, summary: nil)
                }
                let digest = MylifeAggregator.aggregate(
                    days: days,
                    granularity: .month,
                    locale: russian
                )[0]
                XCTAssertEqual(
                    digest.summaryLines,
                    ["август 2026 · всего \(count) \(dayWord)"]
                )
            }
        }
    }

    func testScopeFiltersDays() {
        let days = [day("2026-08-21", summary: "A"), day("2026-07-21", summary: "B")]
        let scoped = MylifeAggregator.aggregate(days: days, granularity: .day, scopeKey: "2026-08", locale: en)
        XCTAssertEqual(scoped.map(\.key), ["2026-08-21"])
    }

    func testImageRotationMatchesWindowRoundRobin() {
        let candidates = [
            PeriodImage(url: URL(string: "https://e/a1")!, tsMs: 1, eventRef: "a1", frameId: "a1", windowId: "A"),
            PeriodImage(url: URL(string: "https://e/b1")!, tsMs: 2, eventRef: "b1", frameId: "b1", windowId: "B"),
            PeriodImage(url: URL(string: "https://e/a2")!, tsMs: 3, eventRef: "a2", frameId: "a2", windowId: "A"),
            PeriodImage(url: URL(string: "https://e/b2")!, tsMs: 4, eventRef: "b2", frameId: "b2", windowId: "B"),
            PeriodImage(url: URL(string: "https://e/a3")!, tsMs: 5, eventRef: "a3", frameId: "a3", windowId: "A"),
            PeriodImage(url: URL(string: "https://e/b3")!, tsMs: 6, eventRef: "b3", frameId: "b3", windowId: "B")
        ]
        let selected = MylifeAggregator.rotateSelect(candidates, cap: 4)
        XCTAssertEqual(selected.map(\.eventRef), ["a1", "b1", "a2", "b2"])
    }

    func testEmptyWindowIdFallsBackToEventRef() {
        let candidates = [
            PeriodImage(url: URL(string: "https://e/1")!, tsMs: 1, eventRef: "r1", frameId: "1", windowId: ""),
            PeriodImage(url: URL(string: "https://e/2")!, tsMs: 2, eventRef: "r1", frameId: "2", windowId: nil)
        ]
        let selected = MylifeAggregator.rotateSelect(candidates, cap: 2)
        XCTAssertEqual(selected.map(\.eventRef), ["r1", "r1"])
    }

    func testTopicSelectionKeepsOrderUntilLimit() {
        let topics = (1...50).map { MylifeDayTopic(topic: "t\($0)", searchWords: nil) }
        let days = [day("2026-08-21", summary: "A", topics: topics)]
        let digest = MylifeAggregator.aggregate(days: days, granularity: .week, locale: en)[0]
        XCTAssertEqual(digest.topics.count, 50)
        XCTAssertEqual(digest.topics.first?.topic, "t1")
        XCTAssertEqual(digest.topics.last?.topic, "t50")
    }

    func testTopicSelectionIsStableForSameKey() {
        let topics = (1...60).map { MylifeDayTopic(topic: "t\($0)", searchWords: nil) }
        let days = [day("2026-08-21", summary: "A", topics: topics)]
        let a = MylifeAggregator.aggregate(days: days, granularity: .month, locale: en)[0].topics.map(\.topic)
        let b = MylifeAggregator.aggregate(days: days, granularity: .month, locale: en)[0].topics.map(\.topic)
        XCTAssertEqual(a, b)
        XCTAssertEqual(a.count, 50)
    }

    func testStableSortPreservesInputOrderForEqualTimestamps() {
        let values = [
            PeriodImage(url: URL(string: "https://e/1")!, tsMs: 1, eventRef: "first", frameId: "1", windowId: nil),
            PeriodImage(url: URL(string: "https://e/2")!, tsMs: 1, eventRef: "second", frameId: "2", windowId: nil)
        ]
        XCTAssertEqual(MylifeAggregator.stableSort(values, by: \.tsMs).map(\.eventRef), ["first", "second"])
    }
}
