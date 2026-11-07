import XCTest
@testable import LivaChatApp

@MainActor
final class TimelineHomeViewModelTests: XCTestCase {
    private func waitUntil(
        timeout: TimeInterval = 2,
        _ condition: @escaping () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 30_000_000)
        }
        XCTFail("condition not met")
    }

    func testInitialLoadAndGranularityDrillDown() async {
        let viewModel = TimelineHomeViewModel(store: UITestMylifeStore())
        await waitUntil { viewModel.periods.count == 3 }
        XCTAssertEqual(viewModel.granularity, .day)
        XCTAssertNil(viewModel.scopeKey)
        XCTAssertEqual(viewModel.periods.map(\.key), ["2026-08-21", "2026-08-20", "2026-08-19"])

        viewModel.onGranularityChange(.month)
        await waitUntil { viewModel.periods.count == 1 }
        let month = viewModel.periods[0]
        XCTAssertEqual(month.key, "2026-08")
        XCTAssertEqual(viewModel.scrollResetToken, 1)

        viewModel.onDrillDown(month)
        XCTAssertEqual(viewModel.granularity, .day)
        XCTAssertEqual(viewModel.scopeKey, "2026-08")
        XCTAssertEqual(viewModel.periods.count, 3)
        XCTAssertEqual(viewModel.scopeLabel, MylifeDateLabels.scopeLabel("2026-08", locale: MylifeDateLabels.effectiveLocale))

        viewModel.onClearScope()
        XCTAssertNil(viewModel.scopeKey)
        XCTAssertEqual(viewModel.periods.count, 3)

        viewModel.onGranularityChange(.year)
        await waitUntil { viewModel.periods.count == 1 }
        XCTAssertEqual(viewModel.periods[0].key, "2026")
        viewModel.onDrillDown(viewModel.periods[0])
        XCTAssertEqual(viewModel.granularity, .month)
        XCTAssertEqual(viewModel.scopeKey, "2026")
        XCTAssertEqual(viewModel.periods.map(\.key), ["2026-08"])
    }

    func testSearchMinimumLengthDebounceAndResults() async {
        let viewModel = TimelineHomeViewModel(store: UITestMylifeStore())
        await waitUntil { viewModel.periods.count == 3 }

        viewModel.onQueryChange("h")
        XCTAssertEqual(viewModel.search, .idle)

        viewModel.onQueryChange("hello")
        await waitUntil {
            if case let .data(hits, total, _, _) = viewModel.search {
                return hits.count == 3 && total == 3
            }
            return false
        }

        viewModel.clearQuery()
        XCTAssertEqual(viewModel.query, "")
        XCTAssertEqual(viewModel.search, .idle)
    }

    func testTopicSearchPrefersSearchWords() async {
        let viewModel = TimelineHomeViewModel(store: UITestMylifeStore())
        await waitUntil { viewModel.periods.count == 3 }

        viewModel.onTopicSearch(MylifeDayTopic(topic: "travel", searchWords: "travel plans"))
        XCTAssertEqual(viewModel.query, "travel plans")
        await waitUntil {
            if case .data = viewModel.search { return true }
            return false
        }

        viewModel.onTopicSearch(MylifeDayTopic(topic: "bare", searchWords: "   "))
        XCTAssertEqual(viewModel.query, "bare")
    }

    func testSearchHitNavigationCarriesPendingFromLoadedWindow() async {
        let viewModel = TimelineHomeViewModel(store: UITestMylifeStore())
        await waitUntil { viewModel.periods.count == 3 }

        let ts = Int64(Date(timeIntervalSince1970: 0).timeIntervalSince1970 * 1000)
        let hit = SearchHitDTO(
            date: "2026-08-21",
            tsMs: ts,
            kind: "chat",
            snippet: "hello"
        )
        let nav = viewModel.onSearchHitClick(hit)
        XCTAssertEqual(nav?.tsMs, ts)
        XCTAssertEqual(nav?.pending, false)
        XCTAssertNil(viewModel.onSearchHitClick(
            SearchHitDTO(date: "2026-08-21", tsMs: nil, kind: "chat", snippet: "x")
        ))
    }

    func testNavigationPayloads() async {
        let viewModel = TimelineHomeViewModel(store: UITestMylifeStore())
        await waitUntil { viewModel.periods.count == 3 }

        let day = viewModel.periods[0]
        XCTAssertEqual(viewModel.audioNavigation(day)?.date, "2026-08-21")
        XCTAssertEqual(viewModel.audioNavigation(day)?.count, 2)

        let image = PeriodImage(
            url: URL(string: "https://example.com/a.jpg")!,
            tsMs: 1,
            eventRef: "r1",
            frameId: "f1",
            windowId: nil
        )
        let gallery = viewModel.imageNavigation(image, digest: day)
        XCTAssertEqual(gallery?.from, "2026-08-21")
        XCTAssertEqual(gallery?.to, "2026-08-21")
        XCTAssertEqual(gallery?.focusFrameId, "f1")

        let slot = day.hotSlots[0]
        let hotSlot = viewModel.hotSlotNavigation(slot, pending: false)
        XCTAssertEqual(hotSlot.startMs, slot.startMs)
        XCTAssertEqual(hotSlot.endMs, slot.endMs)
    }
}
