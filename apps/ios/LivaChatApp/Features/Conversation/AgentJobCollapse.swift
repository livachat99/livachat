import Foundation

struct CollapsedMarkdown: Equatable {
    let prefix: String
    let remainingCharacters: Int
}

enum AgentJobCollapse {
    static let minChars = 400
    static let minVisibleChars = 300
    static let maxVisibleChars = 600

    private final class Box {
        let result: CollapsedMarkdown?

        init(_ result: CollapsedMarkdown?) {
            self.result = result
        }
    }

    nonisolated(unsafe) private static let cache: NSCache<NSString, Box> = {
        let cache = NSCache<NSString, Box>()
        cache.countLimit = 50
        return cache
    }()

    static func clearCache() {
        cache.removeAllObjects()
    }

    static func collapsed(markdown: String, key: String) -> CollapsedMarkdown? {
        let cacheKey = "\(key):\(markdown.hashValue)" as NSString
        if let cached = cache.object(forKey: cacheKey) {
            return cached.result
        }
        let result = collapse(markdown)
        cache.setObject(Box(result), forKey: cacheKey)
        return result
    }

    static func collapse(_ markdown: String) -> CollapsedMarkdown? {
        guard markdown.count >= minChars else { return nil }

        let lines = markdown.components(separatedBy: "\n")
        var lineOffsets: [Int] = []
        var offset = 0
        for line in lines {
            lineOffsets.append(offset)
            offset += line.count + 1
        }
        let lastContentIndex = lines.lastIndex {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }

        var fence: MarkdownTableFallback.Fence?
        var inTable = false
        var candidates: [Int] = []

        for index in lines.indices {
            let line = lines[index]

            if let currentFence = fence {
                if MarkdownTableFallback.isClosingFence(line, for: currentFence) {
                    fence = nil
                }
                continue
            }
            if let opened = MarkdownTableFallback.openingFence(line) {
                fence = opened
                continue
            }

            if inTable {
                if line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    inTable = false
                    if let lastContentIndex, index < lastContentIndex {
                        candidates.append(lineOffsets[index])
                    }
                } else if !line.contains("|") {
                    inTable = false
                }
                continue
            }

            if index + 1 < lines.count,
               line.contains("|"),
               MarkdownTableFallback.isDelimiterRow(lines[index + 1]) {
                inTable = true
                continue
            }

            if line.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               let lastContentIndex,
               index < lastContentIndex {
                candidates.append(lineOffsets[index])
            }
        }

        let boundary: Int?
        if let last = candidates.last(where: { $0 >= minVisibleChars && $0 <= minChars }) {
            boundary = last
        } else if let first = candidates.first(where: { $0 > minChars && $0 <= maxVisibleChars }) {
            boundary = first
        } else {
            boundary = nil
        }

        guard let boundary, boundary > 0 else { return nil }

        var prefix = String(markdown.prefix(boundary))
        while prefix.last == "\n" || prefix.last == "\r" {
            prefix.removeLast()
        }
        guard !prefix.isEmpty else { return nil }

        let remaining = markdown.count - prefix.count
        guard remaining > 0 else { return nil }
        return CollapsedMarkdown(prefix: prefix, remainingCharacters: remaining)
    }
}
