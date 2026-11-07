import Foundation

// Wire DTOs for /api/mylife/*. Decode defensively: arrays/dictionaries default
// to empty and optional properties stay nil, so an older/newer BFF cannot make
// a whole screen fail.

struct OverviewResponseDTO: Decodable, Sendable {
    let schemaVersion: Int
    let timezone: String
    let days: [OverviewDayDTO]
    let nextCursor: String?
    let total: Int

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case timezone
        case days
        case nextCursor = "next_cursor"
        case total
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        timezone = try c.decode(String.self, forKey: .timezone)
        days = try c.decodeIfPresent([OverviewDayDTO].self, forKey: .days) ?? []
        nextCursor = try c.decodeIfPresent(String.self, forKey: .nextCursor)
        total = try c.decodeIfPresent(Int.self, forKey: .total) ?? 0
    }
}

struct OverviewDayDTO: Decodable, Sendable {
    let date: String
    let counts: [String: Int]
    let summary: String?
    let hotSlots: [HotSlotDTO]
    let images: [DayImageDTO]
    let hasPending: Bool
    let topics: [DayTopicDTO]

    enum CodingKeys: String, CodingKey {
        case date
        case counts
        case summary
        case hotSlots = "hot_slots"
        case images
        case hasPending = "has_pending"
        case topics
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        counts = try c.decodeIfPresent([String: Int].self, forKey: .counts) ?? [:]
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        hotSlots = try c.decodeIfPresent([HotSlotDTO].self, forKey: .hotSlots) ?? []
        images = try c.decodeIfPresent([DayImageDTO].self, forKey: .images) ?? []
        hasPending = try c.decodeIfPresent(Bool.self, forKey: .hasPending) ?? false
        topics = try c.decodeIfPresent([DayTopicDTO].self, forKey: .topics) ?? []
    }
}

struct HotSlotDTO: Decodable, Sendable {
    let startMs: Int64
    let endMs: Int64
    let count: Int

    enum CodingKeys: String, CodingKey {
        case startMs = "start_ms"
        case endMs = "end_ms"
        case count
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        startMs = try c.decode(Int64.self, forKey: .startMs)
        endMs = try c.decode(Int64.self, forKey: .endMs)
        count = try c.decodeIfPresent(Int.self, forKey: .count) ?? 0
    }
}

struct DayImageDTO: Decodable, Sendable {
    let frameId: String
    let tsMs: Int64
    let eventRef: String
    let mediaUrl: String
    let windowId: String?

    enum CodingKeys: String, CodingKey {
        case frameId = "frame_id"
        case tsMs = "ts_ms"
        case eventRef = "event_ref"
        case mediaUrl = "media_url"
        case windowId = "window_id"
    }
}

struct DayTopicDTO: Decodable, Sendable, Equatable {
    let topic: String
    let detail: String?
    let searchWords: String?

    enum CodingKeys: String, CodingKey {
        case topic
        case detail
        case searchWords = "search_words"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        topic = try c.decode(String.self, forKey: .topic)
        detail = try c.decodeIfPresent(String.self, forKey: .detail)
        searchWords = try c.decodeIfPresent(String.self, forKey: .searchWords)
    }
}

struct TimelineResponseDTO: Decodable, Sendable {
    let schemaVersion: Int
    let timezone: String
    let days: [TimelineDayDTO]
    let nextCursor: String?
    let total: Int

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case timezone
        case days
        case nextCursor = "next_cursor"
        case total
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        timezone = try c.decodeIfPresent(String.self, forKey: .timezone) ?? ""
        days = try c.decodeIfPresent([TimelineDayDTO].self, forKey: .days) ?? []
        nextCursor = try c.decodeIfPresent(String.self, forKey: .nextCursor)
        total = try c.decodeIfPresent(Int.self, forKey: .total) ?? 0
    }
}

struct TimelineDayDTO: Decodable, Sendable {
    let date: String
    let events: [TimelineEventDTO]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        events = try c.decodeIfPresent([TimelineEventDTO].self, forKey: .events) ?? []
    }

    enum CodingKeys: String, CodingKey {
        case date
        case events
    }
}

struct TimelineEventDTO: Decodable, Sendable {
    let tsMs: Int64
    let type: String
    let role: String?
    let text: String?
    let summary: String?
    let agentName: String?
    let jobKey: String?
    let mediaUrl: String?
    let frameId: String?
    let windowId: String?
    let caption: String?
    let eventRef: String?
    let transcript: String?
    let audioSummary: String?
    let durationS: Double?
    let energy: String?
    let energyV: Int?
    let frameMs: Int?
    let dbMin: Double?
    let dbMax: Double?

    enum CodingKeys: String, CodingKey {
        case tsMs = "ts_ms"
        case type
        case role
        case text
        case summary
        case agentName = "agent_name"
        case jobKey = "job_key"
        case mediaUrl = "media_url"
        case frameId = "frame_id"
        case windowId = "window_id"
        case caption
        case eventRef = "event_ref"
        case transcript
        case audioSummary = "audio_summary"
        case durationS = "duration_s"
        case energy
        case energyV = "energy_v"
        case frameMs = "frame_ms"
        case dbMin = "db_min"
        case dbMax = "db_max"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsMs = try c.decode(Int64.self, forKey: .tsMs)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? ""
        role = try c.decodeIfPresent(String.self, forKey: .role)
        text = try c.decodeIfPresent(String.self, forKey: .text)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        agentName = try c.decodeIfPresent(String.self, forKey: .agentName)
        jobKey = try c.decodeIfPresent(String.self, forKey: .jobKey)
        mediaUrl = try c.decodeIfPresent(String.self, forKey: .mediaUrl)
        frameId = try c.decodeIfPresent(String.self, forKey: .frameId)
        windowId = try c.decodeIfPresent(String.self, forKey: .windowId)
        caption = try c.decodeIfPresent(String.self, forKey: .caption)
        eventRef = try c.decodeIfPresent(String.self, forKey: .eventRef)
        transcript = try c.decodeIfPresent(String.self, forKey: .transcript)
        audioSummary = try c.decodeIfPresent(String.self, forKey: .audioSummary)
        durationS = try c.decodeIfPresent(Double.self, forKey: .durationS)
        energy = try c.decodeIfPresent(String.self, forKey: .energy)
        energyV = try c.decodeIfPresent(Int.self, forKey: .energyV)
        frameMs = try c.decodeIfPresent(Int.self, forKey: .frameMs)
        dbMin = try c.decodeIfPresent(Double.self, forKey: .dbMin)
        dbMax = try c.decodeIfPresent(Double.self, forKey: .dbMax)
    }
}

struct SearchResponseDTO: Decodable, Sendable {
    let schemaVersion: Int
    let query: String
    let total: Int
    let nextCursor: String?
    let hits: [SearchHitDTO]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case query
        case total
        case nextCursor = "next_cursor"
        case hits
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        query = try c.decodeIfPresent(String.self, forKey: .query) ?? ""
        total = try c.decodeIfPresent(Int.self, forKey: .total) ?? 0
        nextCursor = try c.decodeIfPresent(String.self, forKey: .nextCursor)
        hits = try c.decodeIfPresent([SearchHitDTO].self, forKey: .hits) ?? []
    }

    init(schemaVersion: Int, query: String, total: Int, nextCursor: String?, hits: [SearchHitDTO]) {
        self.schemaVersion = schemaVersion
        self.query = query
        self.total = total
        self.nextCursor = nextCursor
        self.hits = hits
    }
}

struct SearchHitDTO: Decodable, Sendable, Equatable, Identifiable {
    let date: String
    let tsMs: Int64?
    let kind: String
    let snippet: String

    var id: String {
        "\(kind)-\(date)-\(tsMs ?? 0)-\(snippet.hashValue)"
    }

    enum CodingKeys: String, CodingKey {
        case date
        case tsMs = "ts_ms"
        case kind
        case snippet
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        tsMs = try c.decodeIfPresent(Int64.self, forKey: .tsMs)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        snippet = try c.decodeIfPresent(String.self, forKey: .snippet) ?? ""
    }

    init(date: String, tsMs: Int64?, kind: String, snippet: String) {
        self.date = date
        self.tsMs = tsMs
        self.kind = kind
        self.snippet = snippet
    }
}

struct RemoveResponseDTO: Decodable, Sendable {
    let removed: [String]
    let skipped: [RemoveSkippedDTO]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case removed
        case skipped
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        removed = try c.decodeIfPresent([String].self, forKey: .removed) ?? []
        skipped = try c.decodeIfPresent([RemoveSkippedDTO].self, forKey: .skipped) ?? []
    }
}

struct RemoveSkippedDTO: Decodable, Sendable {
    let eventRef: String
    let reason: String

    enum CodingKeys: String, CodingKey {
        case eventRef = "event_ref"
        case reason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        eventRef = try c.decodeIfPresent(String.self, forKey: .eventRef) ?? ""
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
    }
}
