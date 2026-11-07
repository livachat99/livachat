import Foundation

enum BubbleSide: Equatable, Sendable {
    case user
    case ai
    case agent
}

struct HotSlotTextRow: Equatable {
    let key: String
    let timeLabel: String?
    let tsMs: Int64
    let side: BubbleSide
    let body: String
}

enum HotSlotRow: Identifiable, Equatable {
    case text(key: String, timeLabel: String?, tsMs: Int64, side: BubbleSide, body: String)
    case frames(key: String, timeLabel: String?, frames: [HotSlotEvent])

    var id: String { key }

    var key: String {
        switch self {
        case let .text(key, _, _, _, _): return key
        case let .frames(key, _, _): return key
        }
    }

    var timeLabel: String? {
        switch self {
        case let .text(_, timeLabel, _, _, _): return timeLabel
        case let .frames(_, timeLabel, _): return timeLabel
        }
    }
}

private let minuteMs: Int64 = 60_000

func buildHotSlotRows(
    events: [HotSlotEvent],
    locale: Locale,
    transcriptSuffix: String,
    originalSuffix: String,
    translationSuffix: String
) -> [HotSlotRow] {
    var rows: [HotSlotRow] = []
    var frames: [HotSlotEvent] = []
    var lastMinute: Int64?

    func labelFor(_ tsMs: Int64) -> String? {
        let bucket = tsMs / minuteMs
        guard bucket != lastMinute else { return nil }
        lastMinute = bucket
        return formatRowTime(tsMs: tsMs, locale: locale)
    }

    func flushFrames() {
        guard !frames.isEmpty else { return }
        for chunk in frames.chunked(3) {
            guard let head = chunk.first else { continue }
            rows.append(.frames(
                key: "f-\(head.eventRef ?? String(head.tsMs))",
                timeLabel: labelFor(head.tsMs),
                frames: chunk
            ))
        }
        frames.removeAll()
    }

    for event in events {
        if event.type == EventType.video {
            frames.append(event)
            continue
        }
        // A non-frame event interrupts the frame group even when it is dropped
        // by the role whitelist.
        flushFrames()
        guard let text = event.text else { continue }

        let content: (BubbleSide, String)?
        switch event.type {
        case EventType.chat:
            content = side(for: event.role).map { ($0, text) }
        case EventType.transcribe:
            content = side(for: event.role).map { ($0, "\(text) \(transcriptSuffix)") }
        case EventType.translate:
            content = side(for: event.role).map {
                let suffix = event.role == "user" ? originalSuffix : translationSuffix
                return ($0, "\(text) \(suffix)")
            }
        case EventType.agentJob:
            content = (.agent, text)
        default:
            content = nil
        }
        if let (side, body) = content {
            rows.append(.text(
                key: "t-\(event.eventRef ?? String(event.tsMs))",
                timeLabel: labelFor(event.tsMs),
                tsMs: event.tsMs,
                side: side,
                body: body
            ))
        }
    }
    flushFrames()
    return rows
}

private func side(for role: String?) -> BubbleSide? {
    switch role {
    case "user": return .user
    case "assistant": return .ai
    default: return nil
    }
}

func anchorRowIndex(_ rows: [HotSlotRow], anchorTs: Int64) -> Int? {
    if let exact = rows.firstIndex(where: { row in
        switch row {
        case let .text(_, _, tsMs, _, _): return tsMs == anchorTs
        case let .frames(_, _, frames): return frames.contains { $0.tsMs == anchorTs }
        }
    }) {
        return exact
    }
    return rows.firstIndex(where: { row in
        switch row {
        case let .text(_, _, tsMs, _, _): return tsMs >= anchorTs
        case let .frames(_, _, frames): return (frames.last?.tsMs ?? Int64.min) >= anchorTs
        }
    })
}

func formatRowTime(tsMs: Int64, locale: Locale) -> String {
    MylifeDateLabels.timeLabel(tsMs: tsMs, locale: locale)
}

func formatHotSlotTitle(startMs: Int64, locale: Locale) -> String {
    MylifeDateLabels.hotSlotTitle(tsMs: startMs, locale: locale)
}

private extension Array {
    func chunked(_ size: Int) -> [[Element]] {
        guard size > 0 else { return [] }
        return stride(from: 0, to: count, by: size).map {
            Array(self[$0..<Swift.min($0 + size, count)])
        }
    }
}
