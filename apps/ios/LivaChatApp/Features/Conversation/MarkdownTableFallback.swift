import Foundation

enum MarkdownTableFallback {
    static func rewrite(_ markdown: String, tablesSupported: Bool) -> String {
        guard !tablesSupported else { return markdown }

        let lines = markdown.components(separatedBy: "\n")
        var output: [String] = []
        var index = 0
        var fence: Fence?

        while index < lines.count {
            let line = lines[index]

            if let currentFence = fence {
                output.append(line)
                if isClosingFence(line, for: currentFence) {
                    fence = nil
                }
                index += 1
                continue
            }

            if let opened = openingFence(line) {
                fence = opened
                output.append(line)
                index += 1
                continue
            }

            if index + 1 < lines.count,
               lines[index].contains("|"),
               isDelimiterRow(lines[index + 1]) {
                var end = index + 2
                while end < lines.count,
                      lines[end].contains("|"),
                      !lines[end].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    end += 1
                }
                output.append("```text")
                output.append(contentsOf: lines[index..<end])
                output.append("```")
                index = end
                continue
            }

            output.append(line)
            index += 1
        }

        return output.joined(separator: "\n")
    }

    static func containsTable(_ markdown: String) -> Bool {
        let lines = markdown.components(separatedBy: "\n")
        var fence: Fence?
        for index in 0..<max(0, lines.count - 1) {
            let line = lines[index]
            if let currentFence = fence {
                if isClosingFence(line, for: currentFence) {
                    fence = nil
                }
                continue
            }
            if let opened = openingFence(line) {
                fence = opened
                continue
            }
            if line.contains("|"), isDelimiterRow(lines[index + 1]) {
                return true
            }
        }
        return false
    }

    struct Fence {
        let marker: Character
        let count: Int
    }

    static func openingFence(_ line: String) -> Fence? {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let marker = trimmed.first, marker == "`" || marker == "~" else { return nil }
        let count = trimmed.prefix { $0 == marker }.count
        guard count >= 3 else { return nil }
        return Fence(marker: marker, count: count)
    }

    static func isClosingFence(_ line: String, for fence: Fence) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
        let count = trimmed.prefix { $0 == fence.marker }.count
        guard count >= fence.count else { return false }
        let remainder = String(trimmed.dropFirst(count)).trimmingCharacters(in: .whitespacesAndNewlines)
        return remainder.isEmpty
    }

    static func isDelimiterRow(_ line: String) -> Bool {
        var hasPipe = false
        var hasDash = false
        for character in line {
            switch character {
            case "|":
                hasPipe = true
            case "-":
                hasDash = true
            case ":", " ", "\t":
                continue
            default:
                return false
            }
        }
        return hasPipe && hasDash
    }
}
