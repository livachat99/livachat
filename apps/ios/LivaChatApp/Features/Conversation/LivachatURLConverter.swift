import Foundation

enum LivachatURLConverter {
    private static let prefix = "livachat_url://webroot/"
    private static let trailingPunctuation: Set<Character> = [".", ",", ";", ":", "!", "?"]

    static func convert(_ text: String, serverBaseURL: String) -> String {
        guard !serverBaseURL.isEmpty, !text.isEmpty else { return text }

        var output = String()
        output.reserveCapacity(text.count + 16)
        var searchStart = text.startIndex
        var replaced = false

        while let match = text.range(
            of: prefix,
            options: [.caseInsensitive],
            range: searchStart..<text.endIndex
        ) {
            output += text[searchStart..<match.lowerBound]

            var end = match.upperBound
            while end < text.endIndex, !isTokenBoundary(text[end]) {
                end = text.index(after: end)
            }

            var tokenEnd = end
            while tokenEnd > match.upperBound,
                  trailingPunctuation.contains(text[text.index(before: tokenEnd)]) {
                tokenEnd = text.index(before: tokenEnd)
            }

            let token = String(text[match.upperBound..<tokenEnd])
            if isValidToken(token) {
                output += serverBaseURL + "/webroot/" + token
                replaced = true
            } else {
                output += text[match.lowerBound..<tokenEnd]
            }
            output += text[tokenEnd..<end]
            searchStart = end
        }

        output += text[searchStart...]
        return replaced ? output : text
    }

    private static func isTokenBoundary(_ character: Character) -> Bool {
        character.isWhitespace
            || character == ")"
            || character == "]"
            || character == ">"
            || character == "\""
            || character == "'"
            || !character.isASCII
    }

    private static func isValidToken(_ token: String) -> Bool {
        guard !token.isEmpty else { return false }
        if token.contains(where: { $0.isWhitespace }) { return false }
        if token.contains("..") { return false }
        if token.contains("\\") { return false }
        let firstSegment = token[..<(token.firstIndex(of: "/") ?? token.endIndex)]
        return isUUIDv7(firstSegment)
    }

    private static func isUUIDv7(_ value: Substring) -> Bool {
        let characters = Array(value)
        guard characters.count == 36 else { return false }
        for (index, character) in characters.enumerated() {
            switch index {
            case 8, 13, 18, 23:
                if character != "-" { return false }
            case 14:
                if character != "7" { return false }
            case 19:
                if !"89abAB".contains(character) { return false }
            default:
                if !character.isHexDigit { return false }
            }
        }
        return true
    }
}
