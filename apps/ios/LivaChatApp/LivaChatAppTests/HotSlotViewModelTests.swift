import Foundation
import XCTest
@testable import LivaChatApp

@MainActor
final class HotSlotViewModelTests: XCTestCase {
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

    func testLoadsFixtureEventsAndPending() async {
        let store = UITestMylifeStore()
        let slot = try! XCTUnwrap(store.overviewWindow?.days.first?.hotSlots.first)
        let viewModel = HotSlotViewModel(
            store: store,
            startMs: slot.startMs,
            endMs: slot.endMs,
            titleTsMs: slot.startMs,
            pending: true
        )

        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }
        guard case let .data(events, _, _) = viewModel.state else { return XCTFail() }
        XCTAssertEqual(events.count, 3)
        XCTAssertEqual(events[0].type, EventType.chat)
        XCTAssertEqual(events[1].type, EventType.agentJob)
        XCTAssertTrue(viewModel.pending)
        XCTAssertEqual(viewModel.titleTsMs, slot.startMs)
    }

    func testOverlayStateMachine() async {
        let store = UITestMylifeStore()
        let slot = try! XCTUnwrap(store.overviewWindow?.days.first?.hotSlots.first)
        let viewModel = HotSlotViewModel(
            store: store,
            startMs: slot.startMs,
            endMs: slot.endMs,
            titleTsMs: slot.startMs,
            pending: false
        )
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }

        viewModel.onAgentJobClick(rowKey: "t-uitest-agent")
        XCTAssertEqual(viewModel.viewerAgentJobKey, "t-uitest-agent")
        XCTAssertNil(viewModel.viewerFrame)
        viewModel.dismissAgentJob()
        XCTAssertNil(viewModel.viewerAgentJobKey)

        let frame = HotSlotEvent(
            tsMs: 1,
            type: EventType.video,
            role: nil,
            text: nil,
            frameId: "f",
            mediaUrl: URL(string: "https://example.com/f.jpg"),
            caption: "cap",
            eventRef: "v"
        )
        viewModel.onFrameClick(frame)
        XCTAssertEqual(viewModel.viewerFrame, frame)
        XCTAssertNil(viewModel.viewerAgentJobKey)
        viewModel.dismissViewer()
        XCTAssertNil(viewModel.viewerFrame)
    }

    func testDismissingViewerCancelsSaveWithoutFailureTip() async {
        let store = UITestMylifeStore(downloadHandler: { _ in
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            throw URLError(.cancelled)
        })
        let slot = try! XCTUnwrap(store.overviewWindow?.days.first?.hotSlots.first)
        let viewModel = HotSlotViewModel(
            store: store,
            startMs: slot.startMs,
            endMs: slot.endMs,
            titleTsMs: slot.startMs,
            pending: false
        )
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }

        let frame = HotSlotEvent(
            tsMs: 1,
            type: EventType.video,
            role: nil,
            text: nil,
            frameId: "f",
            mediaUrl: URL(string: "https://example.com/f.jpg"),
            caption: nil,
            eventRef: "v"
        )
        viewModel.onFrameClick(frame)
        viewModel.saveViewerFrame()
        viewModel.dismissViewer()
        try? await Task.sleep(nanoseconds: 200_000_000)
        XCTAssertNil(viewModel.imageSaveTip)
        XCTAssertFalse(viewModel.saving)
    }

    func testAnchorModeCreatesWindowAroundTimestamp() async {
        let store = UITestMylifeStore()
        let slot = try! XCTUnwrap(store.overviewWindow?.days.first?.hotSlots.first)
        let anchor = slot.startMs + 60_000
        let viewModel = HotSlotViewModel(
            store: store,
            startMs: anchor - 600_000,
            endMs: anchor + 600_000,
            titleTsMs: anchor,
            pending: false,
            anchorTs: anchor
        )
        XCTAssertTrue(viewModel.isAnchorMode)
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }
        XCTAssertEqual(viewModel.anchorTs, anchor)
    }
}
