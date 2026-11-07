import Foundation
import LivaChatSDK

enum UITestScreen: String {
    case splash
    case permission
    case conversation
    case settings
    case history
    case timeline
}

struct ServerOverride {
    let webSocketURL: URL
    let token: String
    let deviceID: String
    let tlsPolicy: TLSPolicy

    var livaChatConfig: LivaChatConfig {
        LivaChatConfig(
            serverURL: webSocketURL,
            token: token,
            deviceID: deviceID,
            clientVersion: AppInfo.clientVersion,
            enableVideo: true,
            tlsPolicy: tlsPolicy,
            language: AppLocale.resourceCode,
            languageProvider: { AppLocale.resourceCode },
            avdetectorEnabled: true
        )
    }
}

struct AppLaunchOptions {
    let uiTestScreen: UITestScreen?
    let uiTestPermission: MicPermissionState?
    let uiTestCameraPermission: CameraPermissionState?
    let uiTestConnection: String?
    let uiTestMessages: Bool
    let uiTestLongMessages: Bool
    let uiTestBanner: String?
    let uiTestCameraOn: Bool
    let uiTestCameraFront: Bool
    let uiTestGalleryPreview: Bool
    let uiTestAIImage: Bool
    let uiTestAIImagePair: Bool
    let uiTestMarkdown: Bool
    let uiTestAgentJob: Bool
    let uiTestJobStatus: Bool
    let uiTestJobStatusCount: Int?
    let uiTestJobCancelResult: String?
    let uiTestHistoryEmpty: Bool
    let uiTestTimelineFixture: String?
    let uiTestAvatar: Bool
    let uiTestAvatarHidden: Bool
    let uiTestAvatarSheet: Bool
    let uiTestAvatarCrop: Bool
    let uiTestModeReady: Bool
    let uiTestMode: String?
    let uiTestModePicker: String?
    let skipSplash: Bool
    let allowSelfSignedTLS: Bool
    let serverOverride: ServerOverride?
    let showAINotice: Bool
    let resetAINotice: Bool

    var isUITest: Bool { uiTestScreen != nil }

    static let current = AppLaunchOptions()

    init(
        arguments: [String] = ProcessInfo.processInfo.arguments,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        func value(after flag: String) -> String? {
            guard let index = arguments.firstIndex(of: flag), arguments.indices.contains(index + 1) else {
                return nil
            }
            return arguments[index + 1]
        }

        skipSplash = arguments.contains("-uiTestSkipSplash")

        #if DEBUG
        allowSelfSignedTLS = arguments.contains("-allowSelfSignedTLS")
        uiTestScreen = value(after: "-uiTestScreen").flatMap(UITestScreen.init(rawValue:))
        showAINotice = uiTestScreen == nil || arguments.contains("-uiTestShowAINotice")
        resetAINotice = arguments.contains("-uiTestResetAINotice")
        uiTestPermission = value(after: "-uiTestPermission").flatMap(Self.parsePermission)
        uiTestCameraPermission = value(after: "-uiTestCameraPermission").flatMap(Self.parseCameraPermission)
        uiTestConnection = value(after: "-uiTestConnection")
        uiTestMessages = value(after: "-uiTestMessages") == "sample"
        uiTestLongMessages = arguments.contains("-uiTestLongMessages")
        uiTestBanner = value(after: "-uiTestBanner")
        uiTestCameraOn = value(after: "-uiTestCamera") == "on"
        uiTestCameraFront = value(after: "-uiTestCameraFront") == "1"
        uiTestGalleryPreview = arguments.contains("-uiTestGalleryPreview")
        uiTestAIImage = arguments.contains("-uiTestAIImage")
        uiTestAIImagePair = arguments.contains("-uiTestAIImagePair")
        uiTestMarkdown = arguments.contains("-uiTestMarkdown")
        uiTestAgentJob = arguments.contains("-uiTestAgentJob")
        uiTestJobStatus = arguments.contains("-uiTestJobStatus")
        uiTestJobStatusCount = value(after: "-uiTestJobStatusCount")
            .flatMap(Int.init)
            .flatMap { $0 >= 0 ? $0 : nil }
        uiTestJobCancelResult = value(after: "-uiTestJobCancelResult")
        uiTestHistoryEmpty = arguments.contains("-uiTestHistoryEmpty")
        uiTestTimelineFixture = value(after: "-uiTestTimelineFixture")
        uiTestAvatar = arguments.contains("-uiTestAvatar")
        uiTestAvatarHidden = arguments.contains("-uiTestAvatarHidden")
        uiTestAvatarSheet = arguments.contains("-uiTestAvatarSheet")
        uiTestAvatarCrop = arguments.contains("-uiTestAvatarCrop")
        uiTestModeReady = arguments.contains("-uiTestModeReady")
        uiTestMode = value(after: "-uiTestMode")
        uiTestModePicker = value(after: "-uiTestModePicker")
        #else
        allowSelfSignedTLS = false
        showAINotice = true
        resetAINotice = false
        uiTestScreen = nil
        uiTestPermission = nil
        uiTestCameraPermission = nil
        uiTestConnection = nil
        uiTestMessages = false
        uiTestLongMessages = false
        uiTestBanner = nil
        uiTestCameraOn = false
        uiTestCameraFront = false
        uiTestGalleryPreview = false
        uiTestAIImage = false
        uiTestAIImagePair = false
        uiTestMarkdown = false
        uiTestAgentJob = false
        uiTestJobStatus = false
        uiTestJobStatusCount = nil
        uiTestJobCancelResult = nil
        uiTestHistoryEmpty = false
        uiTestTimelineFixture = nil
        uiTestAvatar = false
        uiTestAvatarHidden = false
        uiTestAvatarSheet = false
        uiTestAvatarCrop = false
        uiTestModeReady = false
        uiTestMode = nil
        uiTestModePicker = nil
        #endif

        #if DEBUG
        if let urlString = environment["LIVACHAT_SERVER_URL"],
           let url = URL(string: urlString),
           let scheme = url.scheme?.lowercased(),
           scheme == "ws" || scheme == "wss" {
            let token = environment["LIVACHAT_SERVER_TOKEN"]
                ?? environment["LIVACHAT_SERVER_SECRET"].map(WireToken.make)
                ?? ""
            if !token.isEmpty {
                serverOverride = ServerOverride(
                    webSocketURL: url,
                    token: token,
                    deviceID: environment["LIVACHAT_DEVICE_ID"] ?? UUID().uuidString,
                    tlsPolicy: environment["LIVACHAT_TLS_ALLOW_SELF_SIGNED"] == "1"
                        ? .allowSelfSigned
                        : .system
                )
            } else {
                serverOverride = nil
            }
        } else {
            serverOverride = nil
        }
        #else
        serverOverride = nil
        #endif
    }

    private static func parsePermission(_ raw: String) -> MicPermissionState? {
        switch raw {
        case "granted": return .granted
        case "denied": return .denied
        case "undetermined": return .undetermined
        default: return nil
        }
    }

    private static func parseCameraPermission(_ raw: String) -> CameraPermissionState? {
        switch raw {
        case "granted": return .granted
        case "denied": return .denied
        case "undetermined": return .undetermined
        default: return nil
        }
    }
}
