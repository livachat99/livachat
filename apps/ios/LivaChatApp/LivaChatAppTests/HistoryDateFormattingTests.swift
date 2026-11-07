import XCTest
@testable import LivaChatApp

final class HistoryDateFormattingTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!

    private func session(messageCount: Int = 3) -> HistorySession {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let start = calendar.date(from: DateComponents(year: 2026, month: 8, day: 21, hour: 12))!
        return HistorySession(
            id: "s1",
            startTime: start,
            endTime: nil,
            messageCount: messageCount,
            summary: nil,
            firstUserMessage: nil
        )
    }

    func testEnglishLongDateFullWeekdayAndPlural() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "en"),
                    timeZone: utc
                ),
                "August 21, 2026, Friday, 3 messages"
            )
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(messageCount: 1),
                    locale: Locale(identifier: "en"),
                    timeZone: utc
                ),
                "August 21, 2026, Friday, 1 message"
            )
        }
    }

    func testSimplifiedChineseDateAndWeekday() {
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "zh-Hans"),
                    timeZone: utc
                ),
                "2026年8月21日，星期五，3条消息"
            )
        }
    }

    func testTraditionalChineseDateAndWeekday() {
        LocalizationTestSupport.withBundle("zh-Hant") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "zh-Hant"),
                    timeZone: utc
                ),
                "2026年8月21日，星期五，3 則訊息"
            )
        }
    }

    func testJapaneseDateAndWeekday() {
        LocalizationTestSupport.withBundle("ja") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "ja"),
                    timeZone: utc
                ),
                "2026年8月21日、金曜日、3件のメッセージ"
            )
        }
    }

    func testKoreanDateAndWeekday() {
        LocalizationTestSupport.withBundle("ko") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "ko"),
                    timeZone: utc
                ),
                "2026년 8월 21일, 금요일, 메시지 3개"
            )
        }
    }

    func testRussianDateAndWeekday() {
        LocalizationTestSupport.withBundle("ru") {
            let actual = HistoryDateFormatting.subtitle(
                for: session(),
                locale: Locale(identifier: "ru"),
                timeZone: utc
            )
            let normalized = actual
                .replacingOccurrences(of: "\u{00A0}", with: " ")
                .replacingOccurrences(of: "\u{202F}", with: " ")
            XCTAssertEqual(normalized, "21 августа 2026 г., пятница, 3 сообщения")
        }
    }

    func testGermanDateAndWeekday() {
        LocalizationTestSupport.withBundle("de") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "de"),
                    timeZone: utc
                ),
                "21. August 2026, Freitag, 3 Nachrichten"
            )
        }
    }

    func testFrenchDateAndWeekday() {
        LocalizationTestSupport.withBundle("fr") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "fr"),
                    timeZone: utc
                ),
                "21 août 2026, vendredi, 3 messages"
            )
        }
    }

    func testItalianDateAndWeekday() {
        LocalizationTestSupport.withBundle("it") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "it"),
                    timeZone: utc
                ),
                "21 agosto 2026, venerdì, 3 messaggi"
            )
        }
    }

    func testSpanishDateAndWeekday() {
        LocalizationTestSupport.withBundle("es") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "es"),
                    timeZone: utc
                ),
                "21 de agosto de 2026, viernes, 3 mensajes"
            )
        }
    }

    func testPortugueseDateAndWeekday() {
        LocalizationTestSupport.withBundle("pt") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "pt-BR"),
                    timeZone: utc
                ),
                "21 de agosto de 2026, sexta-feira, 3 mensagens"
            )
        }
    }

    func testHindiDateAndWeekday() {
        LocalizationTestSupport.withBundle("hi") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "hi"),
                    timeZone: utc
                ),
                "21 अगस्त 2026, शुक्रवार, 3 संदेश"
            )
        }
    }

    func testIndonesianDateAndWeekday() {
        LocalizationTestSupport.withBundle("id") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "id"),
                    timeZone: utc
                ),
                "21 Agustus 2026, Jumat, 3 pesan"
            )
        }
    }

    func testArabicDateAndWeekday() {
        LocalizationTestSupport.withBundle("ar") {
            let actual = HistoryDateFormatting.subtitle(
                for: session(),
                locale: Locale(identifier: "ar_SA"),
                timeZone: utc
            )
            XCTAssertEqual(
                LocalizationTestSupport.strippingBidiControls(actual),
                "٢١ أغسطس، ٢٠٢٦، الجمعة، ٣ رسائل"
            )
        }
    }

    func testThaiDateAndWeekday() {
        LocalizationTestSupport.withBundle("th") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "th"),
                    timeZone: utc
                ),
                "21 สิงหาคม ค.ศ. 2026, วันศุกร์, 3 ข้อความ"
            )
        }
    }

    func testVietnameseDateAndWeekday() {
        LocalizationTestSupport.withBundle("vi") {
            XCTAssertEqual(
                HistoryDateFormatting.subtitle(
                    for: session(),
                    locale: Locale(identifier: "vi"),
                    timeZone: utc
                ),
                "ngày 21 tháng 8, 2026, Thứ Sáu, 3 tin nhắn"
            )
        }
    }
}
