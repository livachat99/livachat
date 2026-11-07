import Foundation

/// Deterministic client-side aggregation from loaded daily summaries, matching
/// Android PeriodAggregator. No network/LLM work happens here.
enum MylifeAggregator {
    static let maxTopics = 50

    static func aggregate(
        days: [MylifeDay],
        granularity: Granularity,
        scopeKey: String? = nil,
        locale: Locale = AppLocale.current
    ) -> [PeriodDigest] {
        let filtered = days.filter { day in
            guard MylifeDateLabels.components(fromDateKey: day.date) != nil else { return false }
            if let scopeKey { return day.date.hasPrefix(scopeKey) }
            return true
        }

        var groups: [String: [MylifeDay]] = [:]
        for day in filtered {
            guard let key = groupKey(for: day.date, granularity: granularity) else { continue }
            groups[key, default: []].append(day)
        }

        return groups.keys.sorted(by: >).compactMap { key in
            guard let group = groups[key], !group.isEmpty else { return nil }
            return buildDigest(key: key, granularity: granularity, days: group, locale: locale)
        }
    }

    static func groupKey(for date: String, granularity: Granularity) -> String? {
        switch granularity {
        case .day:
            return date
        case .week:
            return MylifeDateLabels.isoWeekKey(forDateKey: date)
        case .month:
            return String(date.prefix(7))
        case .year:
            return String(date.prefix(4))
        }
    }

    static func summaryLimit(for granularity: Granularity) -> Int {
        switch granularity {
        case .day: return 3
        case .week: return 4
        case .month: return 5
        case .year: return 6
        }
    }

    private static func buildDigest(
        key: String,
        granularity: Granularity,
        days: [MylifeDay],
        locale: Locale
    ) -> PeriodDigest? {
        let daysByTime = days.sorted { $0.date > $1.date }
        guard let newest = daysByTime.first else { return nil }
        let label = rangeLabel(key: key, granularity: granularity, newest: newest.date, locale: locale)

        let hotSlots: [MylifeHotSlot] = granularity == .day ? newest.hotSlots : []
        let summaryLines = summarize(days: daysByTime, granularity: granularity, rangeLabel: label, locale: locale)
        let topics = selectTopics(days: daysByTime, granularity: granularity, key: key)
        let images = collectImages(days: daysByTime, granularity: granularity)
        let imageTotal = days.reduce(0) { $0 + ($1.counts[EventType.video] ?? 0) }
        let audioCount = granularity == .day
            ? days.reduce(0) { $0 + ($1.counts[EventType.audio] ?? 0) }
            : 0

        return PeriodDigest(
            key: key,
            granularity: granularity,
            rangeLabel: label,
            hotSlots: hotSlots,
            summaryLines: summaryLines,
            summaryLimit: summaryLimit(for: granularity),
            topics: topics,
            images: images,
            imageTotal: imageTotal,
            hasPending: days.contains { $0.hasPending },
            audioCount: audioCount
        )
    }

    private static func rangeLabel(
        key: String,
        granularity: Granularity,
        newest: String,
        locale: Locale
    ) -> String {
        switch granularity {
        case .day:
            return MylifeDateLabels.dayLabel(forDateKey: newest, locale: locale)
        case .week:
            return MylifeDateLabels.weekRangeLabel(forDateKey: newest, locale: locale)
        case .month:
            let parts = key.split(separator: "-")
            guard parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]) else { return key }
            return MylifeDateLabels.monthYearText(year: year, month: month, locale: locale)
        case .year:
            let year = Int(key) ?? 0
            return MylifeDateLabels.yearText(year, locale: locale)
        }
    }

    private static func summarize(
        days: [MylifeDay],
        granularity: Granularity,
        rangeLabel: String,
        locale: Locale
    ) -> [String] {
        if granularity == .day {
            guard let summary = days.first?.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !summary.isEmpty else { return [] }
            return [summary]
        }

        var seen = Set<String>()
        var lines: [String] = []
        for day in days {
            guard let summary = day.summary?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !summary.isEmpty, seen.insert(summary).inserted else { continue }
            lines.append(summary)
        }
        if !lines.isEmpty { return lines }

        let n = days.count
        return [
            L10n.format("mylife_period_summary", locale: locale, rangeLabel, n)
        ]
    }

    private static func mergeTopics(days: [MylifeDay]) -> [MylifeDayTopic] {
        var seen = Set<String>()
        var merged: [MylifeDayTopic] = []
        for day in days {
            for topic in day.topics where seen.insert(topic.topic).inserted {
                merged.append(topic)
            }
        }
        return merged
    }

    private static func selectTopics(days: [MylifeDay], granularity: Granularity, key: String) -> [MylifeDayTopic] {
        let merged = mergeTopics(days: days)
        if granularity == .day || merged.count <= maxTopics { return merged }

        var weights: [String: Int] = [:]
        for day in days {
            for topic in day.topics {
                weights[topic.topic, default: 0] += 1
            }
        }

        var rng = SplitMix64(seed: stableHash(key))
        var shuffled = merged
        shuffled.shuffle(using: &rng)
        let sorted = shuffled.enumerated().sorted { lhs, rhs in
            let lw = weights[lhs.element.topic] ?? 0
            let rw = weights[rhs.element.topic] ?? 0
            if lw != rw { return lw > rw }
            return lhs.offset < rhs.offset
        }.map(\.element)
        return Array(sorted.prefix(maxTopics))
    }

    private static func collectImages(days: [MylifeDay], granularity: Granularity) -> [PeriodImage] {
        let cap: Int
        switch granularity {
        case .day: cap = 3
        case .week: cap = 9
        case .month: cap = 12
        case .year: cap = 15
        }
        let candidates = days.flatMap { day in
            day.images.map {
                PeriodImage(
                    url: $0.url,
                    tsMs: $0.tsMs,
                    eventRef: $0.eventRef,
                    frameId: $0.frameId,
                    windowId: $0.windowId
                )
            }
        }
        return rotateSelect(candidates, cap: cap)
    }

    static func rotateSelect(_ candidates: [PeriodImage], cap: Int) -> [PeriodImage] {
        guard !candidates.isEmpty, cap > 0 else { return [] }
        let sorted = stableSort(candidates, by: \.tsMs)

        var order: [String] = []
        var groups: [String: [PeriodImage]] = [:]
        for image in sorted {
            let key = nonEmpty(image.windowId) ?? image.eventRef
            if groups[key] == nil { order.append(key) }
            groups[key, default: []].append(image)
        }

        let ordered = order.sorted { lhs, rhs in
            let l = groups[lhs]?.first?.tsMs ?? 0
            let r = groups[rhs]?.first?.tsMs ?? 0
            if l != r { return l < r }
            return lhs < rhs
        }

        var picked: [PeriodImage] = []
        var round = 0
        while picked.count < cap {
            var added = false
            for key in ordered {
                guard let group = groups[key], round < group.count else { continue }
                picked.append(group[round])
                added = true
                if picked.count >= cap { break }
            }
            if !added { break }
            round += 1
        }
        return stableSort(picked, by: \.tsMs)
    }

    static func stableSort<T>(_ values: [T], by key: (T) -> Int64) -> [T] {
        values.enumerated()
            .sorted { lhs, rhs in
                let l = key(lhs.element)
                let r = key(rhs.element)
                if l != r { return l < r }
                return lhs.offset < rhs.offset
            }
            .map(\.element)
    }

    static func stableHash(_ string: String) -> UInt64 {
        var hash: UInt64 = 0xcbf29ce484222325
        for byte in string.utf8 {
            hash ^= UInt64(byte)
            hash &*= 0x100000001b3
        }
        return hash
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }
}

struct SplitMix64: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        state = seed
    }

    mutating func next() -> UInt64 {
        state &+= 0x9e3779b97f4a7c15
        var z = state
        z = (z ^ (z >> 30)) &* 0xbf58476d1ce4e5b9
        z = (z ^ (z >> 27)) &* 0x94d049bb133111eb
        return z ^ (z >> 31)
    }
}
