import Foundation

enum Granularity: String, CaseIterable, Equatable, Sendable {
    case day
    case week
    case month
    case year
}

struct MylifeHotSlot: Hashable, Sendable {
    let startMs: Int64
    let endMs: Int64
}

struct MylifeDayImage: Equatable, Sendable {
    let url: URL
    let tsMs: Int64
    let eventRef: String
    let frameId: String
    let windowId: String?
}

struct MylifeDayTopic: Hashable, Sendable {
    let topic: String
    let searchWords: String?
}

struct MylifeDay: Identifiable, Equatable, Sendable {
    let date: String
    let counts: [String: Int]
    let summary: String?
    let hotSlots: [MylifeHotSlot]
    let images: [MylifeDayImage]
    let hasPending: Bool
    let topics: [MylifeDayTopic]

    var id: String { date }
}

struct OverviewWindow: Equatable, Sendable {
    var timezone: String
    var days: [MylifeDay]
    var nextCursor: String?
    var total: Int
}

struct PeriodImage: Identifiable, Equatable, Sendable {
    let url: URL
    let tsMs: Int64
    let eventRef: String
    let frameId: String
    let windowId: String?

    var id: String { frameId }
}

struct PeriodDigest: Identifiable, Equatable, Sendable {
    let key: String
    let granularity: Granularity
    let rangeLabel: String
    let hotSlots: [MylifeHotSlot]
    let summaryLines: [String]
    let summaryLimit: Int
    let topics: [MylifeDayTopic]
    let images: [PeriodImage]
    let imageTotal: Int
    let hasPending: Bool
    let audioCount: Int

    var id: String { "\(granularity.rawValue)-\(key)" }

    func dateRange() -> (from: String, to: String)? {
        switch granularity {
        case .day:
            return (key, key)
        case .week:
            guard let monday = MylifeDateLabels.mondayOfIsoWeek(key) else { return nil }
            guard let sunday = MylifeDateLabels.offsetDateKey(
                year: monday.year, month: monday.month, day: monday.day, days: 6
            ) else { return nil }
            let first = PosixFormat.string("%04d-%02d-%02d", monday.year, monday.month, monday.day)
            return (first, sunday)
        case .month:
            guard let components = MylifeDateLabels.components(fromDateKey: key + "-01"),
                  let year = components.year, let month = components.month else { return nil }
            let first = PosixFormat.string("%04d-%02d-01", year, month)
            let lastDay = MylifeDateLabels.daysInMonth(year: year, month: month)
            let last = PosixFormat.string("%04d-%02d-%02d", year, month, lastDay)
            return (first, last)
        case .year:
            guard let year = Int(key) else { return nil }
            let yearKey = PosixFormat.string("%04d", year)
            return ("\(yearKey)-01-01", "\(yearKey)-12-31")
        }
    }
}

enum EventType {
    static let chat = "chat"
    static let audio = "audio"
    static let video = "video"
    static let window = "window"
    static let agentJob = "agent_job"
    static let transcribe = "transcribe"
    static let translate = "translate"
}

struct HotSlotEvent: Identifiable, Equatable, Sendable {
    let tsMs: Int64
    let type: String
    let role: String?
    let text: String?
    let frameId: String?
    let mediaUrl: URL?
    let caption: String?
    let eventRef: String?

    var id: String { eventRef ?? "ts-\(tsMs)" }
}

struct HotSlotPage: Equatable, Sendable {
    let events: [HotSlotEvent]
    let nextCursor: String?
    let reachedWindowEnd: Bool
}

struct FrameItem: Identifiable, Equatable, Sendable {
    let tsMs: Int64
    let mediaUrl: URL
    let frameId: String
    let windowId: String?
    let caption: String?
    let eventRef: String?

    var id: String { frameId }
}

struct FramePage: Equatable, Sendable {
    let frames: [FrameItem]
    let total: Int
    let nextCursor: String?
}

struct EnergyEnvelope: Equatable, Sendable {
    let frameMs: Int
    let dbMin: Float
    let dbMax: Float
    let values: [UInt8]
}

struct AudioSegment: Identifiable, Equatable, Sendable {
    let eventRef: String
    let tsMs: Int64
    let mediaUrl: URL
    let transcript: String?
    let summary: String?
    let durationMs: Int64
    let energy: EnergyEnvelope?

    var id: String { eventRef }
}

struct AudioPage: Equatable, Sendable {
    let segments: [AudioSegment]
    let total: Int
    let nextCursor: String?
}

enum MylifeFormat {
    static let overviewDayPage = 30
    static let overviewMonthPage = 90
    static let overviewYearPage = 366
    static let maxPollDays = 400
    static let hotSlotPageLimit = 500
    static let galleryPageLimit = 40
    static let audioPageLimit = 40
    static let searchPageLimit = 50
    static let hotSlotTypes = "chat,agent_job,video,transcribe,translate"

    static func overviewPageSize(for granularity: Granularity) -> Int {
        switch granularity {
        case .day, .week: return overviewDayPage
        case .month: return overviewMonthPage
        case .year: return overviewYearPage
        }
    }
}
