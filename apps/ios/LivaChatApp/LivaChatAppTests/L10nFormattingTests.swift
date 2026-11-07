import XCTest
@testable import LivaChatApp

final class L10nFormattingTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!

    func testEnglishAndChineseLookupAndPlural() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(L10n.t("common_back"), "Back")
            XCTAssertEqual(L10n.plural("conv_agent_jobs_running", 2), "2 agent tasks running")
            XCTAssertEqual(L10n.plural("conv_agent_jobs_running", 1), "1 agent task running")
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            let zh = Locale(identifier: "zh-Hans")
            XCTAssertEqual(L10n.t("common_back"), "返回")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: zh),
                "Agent任务，2 个运行中"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: zh),
                "查看全文 · 还有约 2 字"
            )
        }
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 1),
                "View full text · about 1 character left"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2),
                "View full text · about 2 characters left"
            )
        }
        LocalizationTestSupport.withBundle("zh-Hant") {
            let traditional = Locale(identifier: "zh-Hant")
            XCTAssertEqual(L10n.t("common_back"), "返回")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: traditional),
                "Agent任務，2 個執行中"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: traditional),
                "檢視全文 · 還有 2 字"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: traditional, 2, 1),
                "2 張已刪除，1 張失敗"
            )
        }
        LocalizationTestSupport.withBundle("ja") {
            let japanese = Locale(identifier: "ja")
            XCTAssertEqual(L10n.t("common_back"), "戻る")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: japanese),
                "Agentタスク、2 件実行中"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: japanese),
                "全文を表示 · 残り 2 文字"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: japanese, 2, 1),
                "2 枚を削除、1 枚が失敗"
            )
        }
        LocalizationTestSupport.withBundle("ko") {
            let korean = Locale(identifier: "ko")
            XCTAssertEqual(L10n.t("common_back"), "뒤로")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: korean),
                "Agent 작업, 2개 실행 중"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: korean),
                "전체 텍스트 보기 · 2자 남음"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: korean, 2, 1),
                "2장 삭제, 1장 실패"
            )
        }
        LocalizationTestSupport.withBundle("ru") {
            let russian = Locale(identifier: "ru")
            XCTAssertEqual(L10n.t("common_back"), "Назад")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: russian),
                "Запущены 2 задачи агента"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: russian),
                "Показать весь текст · осталось 2 символа"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: russian, 2, 1),
                "Удалено: 2, не удалось: 1"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 5, locale: russian),
                "Запущено 5 задач агента"
            )
        }
        LocalizationTestSupport.withBundle("de") {
            let german = Locale(identifier: "de")
            XCTAssertEqual(L10n.t("common_back"), "Zurück")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: german),
                "2 Agent-Aufgaben laufen"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: german),
                "Gesamten Text anzeigen · noch 2 Zeichen"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: german, 2, 1),
                "2 gelöscht, 1 fehlgeschlagen"
            )
        }
        LocalizationTestSupport.withBundle("fr") {
            let french = Locale(identifier: "fr")
            XCTAssertEqual(L10n.t("common_back"), "Retour")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: french),
                "2 tâches d'agent en cours"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: french),
                "Afficher le texte complet · encore 2 caractères"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: french, 2, 1),
                "2 supprimées, 1 en échec"
            )
        }
        LocalizationTestSupport.withBundle("it") {
            let italian = Locale(identifier: "it")
            XCTAssertEqual(L10n.t("common_back"), "Indietro")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: italian),
                "2 attività dell'agente in corso"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: italian),
                "Mostra il testo completo · ancora 2 caratteri"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: italian, 2, 1),
                "2 eliminate, 1 non riuscite"
            )
        }
        LocalizationTestSupport.withBundle("es") {
            let spanish = Locale(identifier: "es")
            XCTAssertEqual(L10n.t("common_back"), "Atrás")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: spanish),
                "2 tareas del agente en curso"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 1, locale: spanish),
                "1 tarea del agente en curso"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: spanish),
                "Ver el texto completo · quedan 2 caracteres"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: spanish, 2, 1),
                "2 eliminadas, 1 con error"
            )
        }
        LocalizationTestSupport.withBundle("pt") {
            let portuguese = Locale(identifier: "pt-BR")
            XCTAssertEqual(L10n.t("common_back"), "Voltar")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: portuguese),
                "2 tarefas do agente em andamento"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: portuguese),
                "Ver o texto completo · restam 2 caracteres"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: portuguese, 2, 1),
                "2 excluídas, 1 com erro"
            )
        }
        LocalizationTestSupport.withBundle("hi") {
            let hindi = Locale(identifier: "hi")
            XCTAssertEqual(L10n.t("common_back"), "वापस")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: hindi),
                "2 एजेंट कार्य चल रहे हैं"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: hindi),
                "पूरा टेक्स्ट देखें · 2 अक्षर शेष"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: hindi, 2, 1),
                "2 हटाई गईं, 1 विफल"
            )
        }
        LocalizationTestSupport.withBundle("id") {
            let indonesian = Locale(identifier: "id")
            XCTAssertEqual(L10n.t("common_back"), "Kembali")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: indonesian),
                "2 tugas agen berjalan"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 1, locale: indonesian),
                "1 tugas agen berjalan"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: indonesian),
                "Lihat teks lengkap · 2 karakter tersisa"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: indonesian, 2, 1),
                "2 dihapus, 1 gagal"
            )
        }
        LocalizationTestSupport.withBundle("ar") {
            let arabic = Locale(identifier: "ar_SA")
            XCTAssertEqual(L10n.t("common_back"), "رجوع")
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 1, locale: arabic),
                "١ مهمة قيد التشغيل"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 2, locale: arabic),
                "٢ مهمتان قيد التشغيل"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 3, locale: arabic),
                "٣ مهام قيد التشغيل"
            )
            XCTAssertEqual(
                L10n.plural("conv_agent_jobs_running", 11, locale: arabic),
                "١١ مهمة قيد التشغيل"
            )
            XCTAssertEqual(
                L10n.plural("conv_job_remaining_chars", 2, locale: arabic),
                "عرض النص كاملًا · تبقّى ٢ حرفان"
            )
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: arabic, 2, 1),
                "تم حذف ٢، وفشل ١"
            )
        }
    }

    func testSearchKindLabels() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(L10n.t("search_kind_chat"), "Chat")
            XCTAssertEqual(L10n.t("search_kind_transcribe"), "Transcript")
            XCTAssertEqual(L10n.t("search_kind_translate"), "Translation")
            XCTAssertEqual(L10n.t("search_kind_video"), "Photo")
            XCTAssertEqual(L10n.t("search_kind_event"), "Event")
            XCTAssertEqual(L10n.t("conv_agent_header"), "Agent")
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(L10n.t("search_kind_chat"), "对话")
            XCTAssertEqual(L10n.t("search_kind_transcribe"), "转写")
            XCTAssertEqual(L10n.t("search_kind_video"), "照片")
        }
        LocalizationTestSupport.withBundle("ar") {
            XCTAssertEqual(L10n.t("search_kind_chat"), "دردشة")
            XCTAssertEqual(L10n.t("search_kind_translate"), "ترجمة")
        }
    }

    func testTitleWithDetailUsesLocalePunctuation() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                L10n.titleWithDetail("Title", "Detail", locale: Locale(identifier: "en")),
                "Title: Detail"
            )
            XCTAssertEqual(
                L10n.titleWithDetail("Title", "", locale: Locale(identifier: "en")),
                "Title"
            )
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(
                L10n.titleWithDetail("错误", "详情", locale: Locale(identifier: "zh-Hans")),
                "错误：详情"
            )
        }
        LocalizationTestSupport.withBundle("fr") {
            XCTAssertEqual(
                L10n.titleWithDetail("Titre", "Détail", locale: Locale(identifier: "fr")),
                "Titre\u{00A0}: Détail"
            )
        }
        LocalizationTestSupport.withBundle("ar") {
            let value = L10n.titleWithDetail(
                "عنوان",
                "تفاصيل",
                locale: Locale(identifier: "ar_SA")
            )
            XCTAssertEqual(
                LocalizationTestSupport.strippingBidiControls(value),
                "عنوان: تفاصيل"
            )
        }
    }

    func testBannerTitleKeepsTechnicalDetailOutOfAppErrors() {
        LocalizationTestSupport.withBundle("en") {
            let error = AppErrorMessage("conv_capture_failed", logDetail: "technical detail")
            XCTAssertEqual(L10n.bannerTitle(.captureFailed(error)), "Microphone error")
            XCTAssertFalse(L10n.bannerTitle(.captureFailed(error)).contains("technical"))

            XCTAssertEqual(
                L10n.bannerTitle(
                    .server(titleKey: "conv_service_warning", detail: "server detail")
                ),
                "Service warning: server detail"
            )
            XCTAssertEqual(
                L10n.bannerTitle(.server(titleKey: "conv_service_warning", detail: "")),
                "Service warning"
            )
        }
    }

    func testAvatarSaveErrorHasNoTechnicalPlaceholder() {
        for code in AppLocale.supportedCodes {
            LocalizationTestSupport.withBundle(code) {
                let message = L10n.t("conv_avatar_save_failed")
                XCTAssertFalse(message.isEmpty, code)
                XCTAssertFalse(message.contains("%"), "\(code): \(message)")
            }
        }
    }

    func testLocalizedNumberUsesArabicIndicDigitsWithoutGrouping() {
        let arabic = Locale(identifier: "ar_SA")
        XCTAssertEqual(LocalizedNumber.integer(2026, locale: arabic), "٢٠٢٦")
        XCTAssertEqual(LocalizedNumber.integer(1_234, locale: arabic), "١٢٣٤")
        XCTAssertEqual(LocalizedNumber.integer(7, locale: arabic, minimumDigits: 2), "٠٧")
        XCTAssertEqual(LocalizedNumber.integer(2026, locale: Locale(identifier: "en_US_POSIX")), "2026")
    }

    func testPosixFormatKeepsAsciiForDataKeys() {
        XCTAssertEqual(PosixFormat.string("%04d-%02d-%02d", 2026, 8, 21), "2026-08-21")
        XCTAssertEqual(PosixFormat.string("%04d-W%02d", 2026, 34), "2026-W34")
        XCTAssertEqual(PosixFormat.string("%02x", 255), "ff")
    }

    func testPageIndicatorUsesLocalizedDigits() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                LocalizedNumber.page(index: 1, total: 2, locale: Locale(identifier: "en")),
                "1 / 2"
            )
            XCTAssertEqual(
                LocalizedNumber.page(index: 1, total: 2, locale: Locale(identifier: "ar_SA")),
                "١ / ٢"
            )
        }
    }

    func testMultipleCountFormatting() {
        LocalizationTestSupport.withBundle("en") {
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: Locale(identifier: "en"), 2, 1),
                "2 deleted, 1 failed"
            )
        }
        LocalizationTestSupport.withBundle("zh-Hans") {
            XCTAssertEqual(
                L10n.format("mylife_photos_deleted_partial", locale: Locale(identifier: "zh-Hans"), 2, 1),
                "2 张已删除，1 张失败"
            )
        }
    }

    func testLocalizedDateSymbolsAndClock() {
        let english = Locale(identifier: "en")
        let russian = Locale(identifier: "ru")
        let arabic = Locale(identifier: "ar_SA")

        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .short, locale: english), "Aug")
        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .full, locale: english), "August")
        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .fullStandalone, locale: russian), "август")
        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .short, locale: russian), "авг.")
        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .full, locale: arabic), "أغسطس")
        XCTAssertEqual(LocalizedDate.monthName(month: 8, style: .numeric, locale: arabic), "٨")

        XCTAssertEqual(LocalizedDate.weekdayName(weekday: 6, style: .short, locale: english), "Fri")
        XCTAssertEqual(LocalizedDate.weekdayName(weekday: 6, style: .full, locale: english), "Friday")
        XCTAssertEqual(LocalizedDate.weekdayName(weekday: 6, style: .short, locale: russian), "Пт")
        XCTAssertEqual(
            LocalizedDate.weekdayName(weekday: 6, style: .short, locale: russian, lowercase: true),
            "пт"
        )
        XCTAssertEqual(LocalizedDate.weekdayName(weekday: 6, style: .full, locale: arabic), "الجمعة")

        XCTAssertEqual(LocalizedDate.time(tsMs: 0, locale: english, timeZone: utc), "00:00")
        XCTAssertEqual(LocalizedDate.time(tsMs: 0, locale: arabic, timeZone: utc), "٠٠:٠٠")
    }

    func testGalleryActionTextAllLocales() {
        let expected = [
            "en": "Describe the image content",
            "zh-Hans": "描述图片内容",
            "zh-Hant": "描述圖片內容",
            "ja": "画像の内容を説明してください",
            "ko": "이미지 내용을 설명해 주세요",
            "ru": "Опишите содержимое изображения",
            "de": "Beschreibe den Inhalt des Bildes",
            "fr": "Décrivez le contenu de l'image",
            "it": "Descrivi il contenuto dell'immagine",
            "es": "Describe el contenido de la imagen",
            "pt": "Descreva o conteúdo da imagem",
            "hi": "छवि की सामग्री का वर्णन करें",
            "id": "Jelaskan isi gambar",
            "th": "อธิบายเนื้อหาในภาพ",
            "vi": "Mô tả nội dung hình ảnh",
            "ar": "صف محتوى الصورة",
        ]
        XCTAssertEqual(Set(expected.keys), Set(AppLocale.supportedCodes))
        for code in AppLocale.supportedCodes {
            LocalizationTestSupport.withBundle(code) {
                XCTAssertEqual(
                    GalleryPhotoEncoder.actionText,
                    expected[code],
                    "locale \(code)"
                )
            }
        }
    }

    func testLongDateAndFullWeekdayUseGregorian() {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = utc
        let date = calendar.date(from: DateComponents(year: 2026, month: 8, day: 21))!

        XCTAssertEqual(LocalizedDate.longDate(date: date, locale: Locale(identifier: "en"), timeZone: utc), "August 21, 2026")
        XCTAssertEqual(LocalizedDate.fullWeekday(date: date, locale: Locale(identifier: "en"), timeZone: utc), "Friday")
        XCTAssertEqual(LocalizedDate.fullWeekday(date: date, locale: Locale(identifier: "ar_SA"), timeZone: utc), "الجمعة")

        let arabicDate = LocalizedDate.longDate(date: date, locale: Locale(identifier: "ar_SA"), timeZone: utc)
        XCTAssertTrue(arabicDate.contains("٢٠٢٦"), "Gregorian year expected, got \(arabicDate)")
        XCTAssertTrue(arabicDate.contains("أغسطس"), "Gregorian month expected, got \(arabicDate)")
    }
}
