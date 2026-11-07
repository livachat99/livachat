import LivaChatSDK
import XCTest
@testable import LivaChatApp

@MainActor
private final class EventBox {
    var events: [LivaChatEvent] = []
}

@MainActor
final class ConversationEventAdapterTests: XCTestCase {
    private func waitUntil(
        timeout: TimeInterval = 1,
        condition: @MainActor () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    func testForwardsEventsUntilStopped() async {
        var continuation: AsyncStream<LivaChatEvent>.Continuation!
        let events = AsyncStream<LivaChatEvent> { continuation = $0 }
        let box = EventBox()
        let adapter = ConversationEventAdapter { event in
            box.events.append(event)
        }

        adapter.start(events)
        continuation.yield(.connectionStateChanged(.connecting))
        await waitUntil { box.events.count == 1 }
        XCTAssertEqual(box.events.count, 1)

        adapter.stop()
        continuation.yield(.connectionStateChanged(.connected))
        try? await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(box.events.count, 1)
        continuation.finish()
    }

    func testSecondStartIsIgnored() async {
        var continuation: AsyncStream<LivaChatEvent>.Continuation!
        let events = AsyncStream<LivaChatEvent> { continuation = $0 }
        let box = EventBox()
        let adapter = ConversationEventAdapter { event in
            box.events.append(event)
        }

        adapter.start(events)
        adapter.start(events)
        continuation.yield(.connectionStateChanged(.connecting))
        await waitUntil { box.events.count == 1 }
        XCTAssertEqual(box.events.count, 1)

        adapter.stop()
        continuation.finish()
    }
}
