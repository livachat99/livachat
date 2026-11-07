import Foundation

enum L10n {
#if DEBUG
    private static let resourceLock = NSLock()
    nonisolated(unsafe) private static var storedResourceBundle: Bundle = .main

    /// Test seam only. Production always reads the app bundle.
    static var resourceBundle: Bundle {
        get {
            resourceLock.lock()
            defer { resourceLock.unlock() }
            return storedResourceBundle
        }
        set {
            resourceLock.lock()
            storedResourceBundle = newValue
            resourceLock.unlock()
        }
    }
#else
    static let resourceBundle: Bundle = .main
#endif

    static func t(_ key: String) -> String {
        NSLocalizedString(key, bundle: resourceBundle, comment: "")
    }

    static func format(
        _ key: String,
        locale: Locale = AppLocale.current,
        _ args: CVarArg...
    ) -> String {
        String(format: t(key), locale: locale, arguments: args)
    }

    static func plural(
        _ key: String,
        _ count: Int,
        locale: Locale = AppLocale.current
    ) -> String {
        String(format: t(key), locale: locale, count)
    }

    static func titleWithDetail(
        _ title: String,
        _ detail: String,
        locale: Locale = AppLocale.current
    ) -> String {
        detail.isEmpty
            ? title
            : format("common_title_with_detail", locale: locale, title, detail)
    }

    static func bannerTitle(_ message: ConversationBannerMessage) -> String {
        switch message {
        case .sessionEnded:
            return t("conv_session_ended")
        case .connectionFailed:
            return t("conv_connection_failed")
        case .authFailed:
            return t("conv_auth_failed")
        case .connectionClosed:
            return t("conv_connection_closed")
        case .micUnavailable:
            return t("conv_mic_unavailable")
        case .audioUnavailable:
            return t("conv_audio_unavailable")
        case let .captureFailed(error):
            return t(error.key)
        case let .cameraStartFailed(error):
            return t(error.key)
        case let .server(titleKey, detail):
            return titleWithDetail(t(titleKey), detail)
        }
    }

    static func micPermissionHint(_ state: MicPermissionState) -> String? {
        switch state {
        case .granted, .unknown:
            return nil
        case .undetermined, .denied:
            return t("perm_mic_required_hint")
        }
    }
}
