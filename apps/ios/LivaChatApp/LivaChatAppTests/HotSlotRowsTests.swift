import XCTest
@testable import LivaChatApp

final class HotSlotRowsTests: XCTestCase {
    private let locale = Locale(identifier: "en")

    private func event(
        _ ts: Int64,
        _ type: String,
        role: String? = nil,
        text: String? = nil,
        ref: String,
        frameId: String? = nil
    ) -> HotSlotEvent {
        HotSlotEvent(
            tsMs: ts,
            type: type,
            role: role,
            text: text,
            frameId: frameId,
            mediaUrl: frameId.flatMap { URL(string: "https://example.com/\($0).jpg") },
            caption: nil,
            eventRef: ref
        )
    }

    func testRowsMinuteLabelsRolesSuffixesAndFrames() {
        let events = [
            event(0, EventType.chat, role: "user", text: "hi", ref: "c1"),
            event(0, EventType.chat, role: "assistant", text: "yo", ref: "c2"),
            event(61_000, EventType.transcribe, role: "user", text: "t", ref: "t1"),
            event(62_000, EventType.translate, role: "user", text: "orig", ref: "tr1"),
            event(62_000, EventType.translate, role: "assistant", text: "trans", ref: "tr2"),
            event(63_000, EventType.agentJob, text: "agent", ref: "a1"),
            event(120_000, EventType.video, ref: "v1", frameId: "f1"),
            event(121_000, EventType.video, ref: "v2", frameId: "f2"),
            event(122_000, EventType.video, ref: "v3", frameId: "f3"),
            event(123_000, EventType.video, ref: "v4", frameId: "f4"),
            event(124_000, EventType.chat, role: "system", text: "drop", ref: "s1"),
            event(125_000, EventType.video, ref: "v5", frameId: "f5")
        ]

        let rows = buildHotSlotRows(
            events: events,
            locale: locale,
            transcriptSuffix: "(transcript)",
            originalSuffix: "(original)",
            translationSuffix: "(translation)"
        )

        XCTAssertEqual(rows.count, 9)
        XCTAssertNotNil(rows[0].timeLabel)
        XCTAssertNil(rows[1].timeLabel)
        XCTAssertNotNil(rows[2].timeLabel)
        XCTAssertNil(rows[3].timeLabel)
        XCTAssertNil(rows[4].timeLabel)
        XCTAssertNil(rows[5].timeLabel)

        guard case let .text(_, _, _, side1, body1) = rows[0] else { return XCTFail() }
        XCTAssertEqual(side1, .user)
        XCTAssertEqual(body1, "hi")
        guard case let .text(_, _, _, side2, body2) = rows[1] else { return XCTFail() }
        XCTAssertEqual(side2, .ai)
        XCTAssertEqual(body2, "yo")
        guard case let .text(_, _, _, _, body3) = rows[2] else { return XCTFail() }
        XCTAssertEqual(body3, "t (transcript)")
        guard case let .text(_, _, _, _, body4) = rows[3] else { return XCTFail() }
        XCTAssertEqual(body4, "orig (original)")
        guard case let .text(_, _, _, side5, body5) = rows[4] else { return XCTFail() }
        XCTAssertEqual(side5, .ai)
        XCTAssertEqual(body5, "trans (translation)")
        guard case let .text(_, _, _, side6, body6) = rows[5] else { return XCTFail() }
        XCTAssertEqual(side6, .agent)
        XCTAssertEqual(body6, "agent")

        guard case let .frames(_, _, chunk1) = rows[6] else { return XCTFail() }
        XCTAssertEqual(chunk1.map(\.eventRef), ["v1", "v2", "v3"])
        guard case let .frames(_, label2, chunk2) = rows[7] else { return XCTFail() }
        XCTAssertNil(label2)
        XCTAssertEqual(chunk2.map(\.eventRef), ["v4"])
        guard case let .frames(_, _, chunk3) = rows[8] else { return XCTFail() }
        XCTAssertEqual(chunk3.map(\.eventRef), ["v5"])
    }

    func testAnchorResolution() {
        let rows: [HotSlotRow] = [
            .text(key: "t1", timeLabel: nil, tsMs: 10, side: .user, body: "a"),
            .frames(key: "f1", timeLabel: nil, frames: [
                HotSlotEvent(tsMs: 20, type: EventType.video, role: nil, text: nil,
                             frameId: "f", mediaUrl: URL(string: "https://e/f"), caption: nil, eventRef: "v1"),
                HotSlotEvent(tsMs: 30, type: EventType.video, role: nil, text: nil,
                             frameId: "f2", mediaUrl: URL(string: "https://e/f2"), caption: nil, eventRef: "v2")
            ]),
            .text(key: "t2", timeLabel: nil, tsMs: 40, side: .ai, body: "b")
        ]
        XCTAssertEqual(anchorRowIndex(rows, anchorTs: 10), 0)
        XCTAssertEqual(anchorRowIndex(rows, anchorTs: 30), 1)
        XCTAssertEqual(anchorRowIndex(rows, anchorTs: 25), 1)
        XCTAssertNil(anchorRowIndex(rows, anchorTs: 41))
        XCTAssertNil(anchorRowIndex(rows, anchorTs: 50))
    }
}
