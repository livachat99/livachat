import XCTest
@testable import LivaChatApp

final class AppLocaleTests: XCTestCase {
    func testResolvePreferredLocalizations() {
        XCTAssertEqual(AppLocale.resolvePreferred(["zh-TW"]), "zh-Hant")
        XCTAssertEqual(AppLocale.resolvePreferred(["zh-Hant"]), "zh-Hant")
        XCTAssertEqual(AppLocale.resolvePreferred(["zh-CN"]), "zh-Hans")
        XCTAssertEqual(AppLocale.resolvePreferred(["pt-BR"]), "pt")
        XCTAssertEqual(AppLocale.resolvePreferred(["ar-SA"]), "ar")
        XCTAssertEqual(AppLocale.resolvePreferred(["in"]), "id")
        XCTAssertEqual(AppLocale.resolvePreferred(["th"]), "th")
        XCTAssertEqual(AppLocale.resolvePreferred(["vi"]), "vi")
        XCTAssertEqual(AppLocale.resolvePreferred(["en-US", "zh-Hans"]), "en")
        XCTAssertEqual(AppLocale.resolvePreferred(["tr-TR"]), "en")
        XCTAssertEqual(AppLocale.resolvePreferred(["xx"]), "en")
    }

    func testFormattingLocaleMapping() {
        XCTAssertEqual(AppLocale.formattingLocale(for: "ar").identifier, "ar_SA")
        XCTAssertEqual(AppLocale.formattingLocale(for: "pt").identifier, "pt-BR")
        XCTAssertEqual(AppLocale.formattingLocale(for: "pt-PT").identifier, "pt-BR")
        XCTAssertEqual(AppLocale.formattingLocale(for: "in").identifier, "id")
        XCTAssertEqual(AppLocale.formattingLocale(for: "zh-Hant").identifier, "zh-Hant")
        XCTAssertEqual(AppLocale.formattingLocale(for: "th").identifier, "th")
        XCTAssertEqual(AppLocale.formattingLocale(for: "vi").identifier, "vi")
    }

    func testStyleKeys() {
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "zh-TW")), "zh-Hant")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "zh-CN")), "zh-Hans")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "pt-BR")), "pt")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "ar_SA")), "ar")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "in")), "id")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "th-TH")), "th")
        XCTAssertEqual(AppLocale.styleKey(for: Locale(identifier: "vi-VN")), "vi")
    }

    func testRTLDetection() {
        XCTAssertTrue(AppLocale.isRTL(forResourceCode: "ar"))
        XCTAssertFalse(AppLocale.isRTL(forResourceCode: "en"))
        XCTAssertFalse(AppLocale.isRTL(forResourceCode: "zh-Hant"))
        XCTAssertFalse(AppLocale.isRTL(forResourceCode: "th"))
        XCTAssertFalse(AppLocale.isRTL(forResourceCode: "vi"))
    }

    func testCalendarIsGregorian() {
        XCTAssertEqual(AppLocale.calendar.identifier, .gregorian)
    }

    func testTraditionalChineseBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("zh-Hant"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("zh-Hant")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "zh-Hant", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "讓我聽見你的聲音，更好地陪伴和傾聽"
        )
    }

    func testJapaneseBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("ja"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("ja")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "ja", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "あなたの声を聞かせてください。そばに寄り添い、耳を傾けます"
        )
    }

    func testKoreanBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("ko"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("ko")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "ko", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "목소리를 듣고 함께 곁에 있기 위해 필요해요"
        )
    }

    func testRussianBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("ru"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("ru")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "ru", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Чтобы слышать вас и быть рядом"
        )
    }

    func testGermanBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("de"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("de")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "de", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Damit ich Ihre Stimme hören und für Sie da sein kann"
        )
    }

    func testFrenchBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("fr"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("fr")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "fr", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Pour entendre votre voix et rester à vos côtés"
        )
    }

    func testItalianBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("it"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("it")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "it", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Per sentire la tua voce e starti accanto"
        )
    }

    func testSpanishBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("es"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("es")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "es", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Para oír tu voz y acompañarte"
        )
    }

    func testPortugueseBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("pt"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("pt")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "pt", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Para ouvir sua voz e acompanhar você"
        )
    }

    func testHindiBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("hi"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("hi")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "hi", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "आपकी आवाज़ सुनने और साथ रहने के लिए"
        )
    }

    func testIndonesianBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("id"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("id")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "id", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "Untuk mendengar suara Anda dan menemani Anda"
        )
    }

    func testArabicBundleAndInfoPlist() throws {
        XCTAssertTrue(Bundle.main.localizations.contains("ar"))
        XCTAssertTrue(
            (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                .contains("ar")
        )
        let path = try XCTUnwrap(Bundle.main.path(forResource: "ar", ofType: "lproj"))
        let bundle = try XCTUnwrap(Bundle(path: path))
        XCTAssertEqual(
            bundle.localizedString(
                forKey: "NSMicrophoneUsageDescription",
                value: nil,
                table: "InfoPlist"
            ),
            "لأسمع صوتك وأكون إلى جانبك"
        )
    }

    func testThaiAndVietnameseBundlesAndInfoPlist() throws {
        let expected = [
            "th": "เพื่อให้ฉันได้ยินเสียงของคุณและอยู่เคียงข้างคุณ",
            "vi": "Để tôi có thể nghe giọng nói và đồng hành cùng bạn.",
        ]
        for (code, microphoneText) in expected {
            XCTAssertTrue(Bundle.main.localizations.contains(code))
            XCTAssertTrue(
                (Bundle.main.object(forInfoDictionaryKey: "CFBundleLocalizations") as? [String] ?? [])
                    .contains(code)
            )
            let path = try XCTUnwrap(Bundle.main.path(forResource: code, ofType: "lproj"))
            let bundle = try XCTUnwrap(Bundle(path: path))
            XCTAssertEqual(
                bundle.localizedString(
                    forKey: "NSMicrophoneUsageDescription",
                    value: nil,
                    table: "InfoPlist"
                ),
                microphoneText,
                "locale \(code)"
            )
        }
    }
}
