import Foundation

enum TimelineRoute: Equatable, Identifiable {
    case hotSlot(startMs: Int64, endMs: Int64, pending: Bool)
    case eventList(tsMs: Int64, pending: Bool)
    case gallery(from: String, to: String, label: String, focusFrameId: String?, pending: Bool)
    case audio(date: String, label: String, count: Int, pending: Bool)

    var id: String {
        switch self {
        case let .hotSlot(startMs, _, _): return "hotSlot-\(startMs)"
        case let .eventList(tsMs, _): return "eventList-\(tsMs)"
        case let .gallery(from, to, _, focus, _): return "gallery-\(from)-\(to)-\(focus ?? "")"
        case let .audio(date, _, _, _): return "audio-\(date)"
        }
    }
}
