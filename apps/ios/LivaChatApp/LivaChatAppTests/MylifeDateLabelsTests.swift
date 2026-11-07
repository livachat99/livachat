import XCTest
@testable import LivaChatApp

final class MylifeDateLabelsTests: XCTestCase {
    private let en = Locale(identifier: "en")
    private let zh = Locale(identifier: "zh-Hans")
    private let ar = Locale(identifier: "ar_SA")
    private let utc = TimeZone(secondsFromGMT: 0)!

    func testMonthDayAndWeekdayLabels() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: en), "Aug 21")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: en), "Aug 21 · Fri")
            XCTAssertEqual(MylifeDateLabels.searchDayLabel(forDateKey: "2026-08-21", locale: en), "Aug 21")
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: zh), "8月21日")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: zh), "8月21日 · 周五")
            XCTAssertEqual(MylifeDateLabels.searchDayLabel(forDateKey: "2026-08-21", locale: zh), "8月21日")
        }
    }

    func testMonthAndYearLabels() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: en), "Aug 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: en), "Aug 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: en), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026", locale: en), "2026")
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: zh), "2026年8月")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: zh), "2026年 · 8月")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: zh), "2026年")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: zh), "2026年8月")
        }
    }

    func testISOWeekAndWeekRange() {
        XCTAssertEqual(MylifeDateLabels.isoWeekKey(forDateKey: "2026-08-21"), "2026-W34")
        let monday = MylifeDateLabels.mondayOfIsoWeek("2026-W34")
        XCTAssertEqual(monday?.year, 2026)
        XCTAssertEqual(monday?.month, 8)
        XCTAssertEqual(monday?.day, 17)

        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: en), "8/17 – 8/23")
            XCTAssertEqual(MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: en), "W34")
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: zh), "8/17 – 8/23")
            XCTAssertEqual(MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: zh), "34周")
        }
    }

    func testCrossYearIsoWeekUsesWeekBasedYear() {
        XCTAssertEqual(MylifeDateLabels.isoWeekKey(forDateKey: "2026-01-01"), "2026-W01")
        let monday = MylifeDateLabels.mondayOfIsoWeek("2026-W01")
        XCTAssertEqual(monday?.year, 2025)
        XCTAssertEqual(monday?.month, 12)
        XCTAssertEqual(monday?.day, 29)
    }

    func testDateKeyHelpersAndLocalDate() {
        XCTAssertEqual(MylifeDateLabels.dateKey(year: 2026, month: 8, day: 21), "2026-08-21")
        XCTAssertEqual(MylifeDateLabels.offsetDateKey(year: 2026, month: 8, day: 31, days: 1), "2026-09-01")
        XCTAssertEqual(MylifeDateLabels.dayAfter("2026-12-31"), "2027-01-01")
        XCTAssertEqual(MylifeDateLabels.daysBetweenInclusive(from: "2026-08-01", to: "2026-08-31"), 31)
        XCTAssertEqual(
            MylifeDateLabels.localDateKey(tsMs: 1_755_720_000_000, timeZone: TimeZone(identifier: "Asia/Shanghai")!),
            "2025-08-21"
        )
        XCTAssertEqual(MylifeDateLabels.components(fromDateKey: "2026-02-30"), nil)
    }

    func testHotSlotTitleUsesDeviceTimeZone() {
        let shanghai = TimeZone(identifier: "Asia/Shanghai")!
        let ts = Int64(Date(timeIntervalSince1970: 0).timeIntervalSince1970 * 1000)
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                MylifeDateLabels.hotSlotTitle(tsMs: ts, locale: en, timeZone: shanghai),
                "Jan 1 08:00"
            )
        }
    }

    func testTraditionalChineseLabels() {
        let traditional = Locale(identifier: "zh-Hant")
        LocalizationTestSupport.withBundle("zh-Hant") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: traditional), "8月21日")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: traditional), "週五")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: traditional), "8月21日 · 週五")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: traditional), "2026年8月")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: traditional), "2026年 · 8月")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: traditional), "2026年")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: traditional), "2026年8月")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: traditional),
                "8/17 – 8/23"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: traditional),
                "34週"
            )
        }
    }

    func testJapaneseLabels() {
        let japanese = Locale(identifier: "ja")
        LocalizationTestSupport.withBundle("ja") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: japanese), "8月21日")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: japanese), "金曜日")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: japanese), "8月21日 · 金曜日")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: japanese), "2026年8月")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: japanese), "2026年 · 8月")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: japanese), "2026年")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: japanese), "2026年8月")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: japanese),
                "8/17 – 8/23"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: japanese),
                "第34週"
            )
        }
    }

    func testKoreanLabels() {
        let korean = Locale(identifier: "ko")
        LocalizationTestSupport.withBundle("ko") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: korean), "8월 21일")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: korean), "금요일")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: korean), "8월 21일 · 금요일")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: korean), "2026년 8월")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: korean), "2026년 · 8월")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: korean), "2026년")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: korean), "2026년 8월")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: korean),
                "8/17 – 8/23"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: korean),
                "34주차"
            )
        }
    }

    func testRussianLabels() {
        let russian = Locale(identifier: "ru")
        LocalizationTestSupport.withBundle("ru") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: russian), "21 авг.")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: russian), "пт")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: russian), "21 авг. · пт")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: russian), "август 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: russian), "август 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: russian), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: russian), "август 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: russian),
                "17.08 – 23.08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: russian),
                "Неделя 34"
            )
        }
    }

    func testGermanLabels() {
        let german = Locale(identifier: "de")
        LocalizationTestSupport.withBundle("de") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: german), "21. Aug.")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: german), "Fr.")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: german), "21. Aug. · Fr.")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: german), "August 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: german), "August 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: german), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: german), "August 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: german),
                "17.08. – 23.08."
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: german),
                "KW 34"
            )
        }
    }

    func testFrenchLabels() {
        let french = Locale(identifier: "fr")
        LocalizationTestSupport.withBundle("fr") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: french), "21 août")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: french), "ven.")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: french), "21 août · ven.")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: french), "août 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: french), "août 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: french), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: french), "août 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: french),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: french),
                "S34"
            )
        }
    }

    func testItalianLabels() {
        let italian = Locale(identifier: "it")
        LocalizationTestSupport.withBundle("it") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: italian), "21 ago")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: italian), "venerdì")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: italian), "21 ago · venerdì")
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: italian), "agosto 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: italian), "agosto 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: italian), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: italian), "agosto 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: italian),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: italian),
                "S34"
            )
        }
    }

    func testSpanishLabels() {
        let spanish = Locale(identifier: "es")
        LocalizationTestSupport.withBundle("es") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: spanish), "21 ago")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: spanish), "viernes")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: spanish), "21 ago · viernes")
            XCTAssertEqual(
                MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: spanish),
                "agosto de 2026"
            )
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: spanish), "agosto de 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: spanish), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: spanish), "agosto de 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: spanish),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: spanish),
                "S34"
            )
        }
    }

    func testPortugueseLabels() {
        let portuguese = Locale(identifier: "pt-BR")
        LocalizationTestSupport.withBundle("pt") {
            XCTAssertEqual(
                MylifeDateLabels.monthDayText(month: 8, day: 21, locale: portuguese),
                "21 ago."
            )
            XCTAssertEqual(
                MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: portuguese),
                "sex."
            )
            XCTAssertEqual(
                MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: portuguese),
                "21 ago. · sex."
            )
            XCTAssertEqual(
                MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: portuguese),
                "agosto de 2026"
            )
            XCTAssertEqual(
                MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: portuguese),
                "agosto de 2026"
            )
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: portuguese), "2026")
            XCTAssertEqual(
                MylifeDateLabels.scopeLabel("2026-08", locale: portuguese),
                "agosto de 2026"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: portuguese),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: portuguese),
                "S34"
            )
        }
    }

    func testHindiLabels() {
        let hindi = Locale(identifier: "hi")
        LocalizationTestSupport.withBundle("hi") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: hindi), "21 अग॰")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: hindi), "शुक्र")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: hindi), "21 अग॰ · शुक्र")
            XCTAssertEqual(
                MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: hindi),
                "अगस्त 2026"
            )
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: hindi), "अगस्त 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: hindi), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: hindi), "अगस्त 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: hindi),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: hindi),
                "सप्ताह 34"
            )
        }
    }

    func testIndonesianLabels() {
        let indonesian = Locale(identifier: "id")
        LocalizationTestSupport.withBundle("id") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: indonesian), "21 Agu")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: indonesian), "Jum")
            XCTAssertEqual(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: indonesian), "21 Agu · Jum")
            XCTAssertEqual(
                MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: indonesian),
                "Agustus 2026"
            )
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: indonesian), "Agustus 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: indonesian), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: indonesian), "Agustus 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: indonesian),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: indonesian),
                "Pekan 34"
            )
        }
    }

    func testArabicLabels() {
        func visible(_ value: String) -> String {
            LocalizationTestSupport.strippingBidiControls(value)
        }
        LocalizationTestSupport.withBundle("ar") {
            XCTAssertEqual(visible(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: ar)), "٢١ أغسطس")
            XCTAssertEqual(visible(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: ar)), "الجمعة")
            XCTAssertEqual(
                visible(MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: ar)),
                "٢١ أغسطس · الجمعة"
            )
            XCTAssertEqual(visible(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: ar)), "أغسطس ٢٠٢٦")
            XCTAssertEqual(visible(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: ar)), "أغسطس ٢٠٢٦")
            XCTAssertEqual(visible(MylifeDateLabels.yearText(2026, locale: ar)), "٢٠٢٦")
            XCTAssertEqual(visible(MylifeDateLabels.scopeLabel("2026-08", locale: ar)), "أغسطس ٢٠٢٦")
            XCTAssertEqual(
                visible(MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: ar)),
                "١٧/٠٨ – ٢٣/٠٨"
            )
            XCTAssertEqual(
                visible(MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: ar)),
                "الأسبوع ٣٤"
            )
        }
    }

    func testArabicDateSymbolsAndClockDigits() {
        XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: ar), "الجمعة")
        XCTAssertEqual(
            MylifeDateLabels.timeLabel(tsMs: 0, locale: ar, timeZone: utc),
            "٠٠:٠٠"
        )
        XCTAssertEqual(MylifeDateLabels.dateKey(year: 2026, month: 8, day: 21), "2026-08-21")
        XCTAssertEqual(MylifeDateLabels.isoWeekKey(forDateKey: "2026-08-21"), "2026-W34")
    }

    func testThaiLabels() {
        let thai = Locale(identifier: "th")
        // ICU short-weekday data changed in iOS 27 ("ศ." -> "ศุกร์"); derive the
        // expected symbol from the running system instead of pinning it.
        let formatter = DateFormatter()
        formatter.locale = thai
        let fridayShort = formatter.shortWeekdaySymbols[5]
        LocalizationTestSupport.withBundle("th") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: thai), "21 ส.ค.")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: thai), fridayShort)
            XCTAssertEqual(
                MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: thai),
                "21 ส.ค. · \(fridayShort)"
            )
            XCTAssertEqual(MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: thai), "สิงหาคม 2026")
            XCTAssertEqual(MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: thai), "สิงหาคม 2026")
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: thai), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: thai), "สิงหาคม 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: thai),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: thai),
                "สัปดาห์ที่ 34"
            )
        }
    }

    func testVietnameseLabels() {
        let vietnamese = Locale(identifier: "vi")
        LocalizationTestSupport.withBundle("vi") {
            XCTAssertEqual(MylifeDateLabels.monthDayText(month: 8, day: 21, locale: vietnamese), "21 thg 8")
            XCTAssertEqual(MylifeDateLabels.weekdayText(forDateKey: "2026-08-21", locale: vietnamese), "Thứ Sáu")
            XCTAssertEqual(
                MylifeDateLabels.dayLabel(forDateKey: "2026-08-21", locale: vietnamese),
                "21 thg 8 · Thứ Sáu"
            )
            XCTAssertEqual(
                MylifeDateLabels.monthYearText(year: 2026, month: 8, locale: vietnamese),
                "Tháng 8 năm 2026"
            )
            XCTAssertEqual(
                MylifeDateLabels.monthHeader(year: 2026, month: 8, locale: vietnamese),
                "Tháng 8 năm 2026"
            )
            XCTAssertEqual(MylifeDateLabels.yearText(2026, locale: vietnamese), "2026")
            XCTAssertEqual(MylifeDateLabels.scopeLabel("2026-08", locale: vietnamese), "Tháng 8 năm 2026")
            XCTAssertEqual(
                MylifeDateLabels.weekRangeLabel(forDateKey: "2026-08-21", locale: vietnamese),
                "17/08 – 23/08"
            )
            XCTAssertEqual(
                MylifeDateLabels.weekNumberLabel(forWeekKey: "2026-W34", locale: vietnamese),
                "Tuần 34"
            )
        }
    }
}
