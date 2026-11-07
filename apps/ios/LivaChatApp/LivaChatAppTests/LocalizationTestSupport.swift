import Foundation
import XCTest
@testable import LivaChatApp

enum LocalizationTestSupport {
    static func withBundle<T>(_ resourceCode: String, _ body: () throws -> T) rethrows -> T {
        let previous = L10n.resourceBundle
        defer { L10n.resourceBundle = previous }

        guard let path = Bundle.main.path(forResource: resourceCode, ofType: "lproj"),
              let bundle = Bundle(path: path) else {
            XCTFail("Missing \(resourceCode).lproj in the test host bundle")
            return try body()
        }
        L10n.resourceBundle = bundle
        return try body()
    }

    static func locale(_ code: String) -> Locale {
        AppLocale.formattingLocale(for: code)
    }

    /// Strips bidi isolation/embedding controls that Foundation inserts around
    /// formatted arguments. They change display order, not the visible text.
    static func strippingBidiControls(_ value: String) -> String {
        value.replacingOccurrences(
            of: "[\u{200E}\u{200F}\u{061C}\u{202A}-\u{202E}\u{2066}-\u{2069}]",
            with: "",
            options: .regularExpression
        )
    }
}
