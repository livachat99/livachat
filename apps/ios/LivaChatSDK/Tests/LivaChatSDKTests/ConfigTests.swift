import XCTest
@testable import LivaChatSDK

final class ConfigTests: XCTestCase {
    private func makeConfig(
        url: String = "wss://example.com/v1/ws/stream",
        token: String = "token",
        deviceID: String = "device",
        audioSampleRate: AudioSampleRate = .hz16000,
        audioFrameMs: Int = 20,
        videoQuality: Int = 70,
        videoMaxLongSide: Int = 1080,
        connectTimeout: TimeInterval = 10,
        reconnectMaxAttempts: Int = 5,
        reconnectBaseDelay: TimeInterval = 1,
        reconnectMaxDelay: TimeInterval = 30,
        keepaliveInterval: TimeInterval = 15,
        keepaliveTimeout: TimeInterval = 35
    ) -> LivaChatConfig {
        LivaChatConfig(
            serverURL: URL(string: url)!,
            token: token,
            deviceID: deviceID,
            clientVersion: "ios-1.0.0.1",
            audioSampleRate: audioSampleRate,
            audioFrameMs: audioFrameMs,
            videoQuality: videoQuality,
            videoMaxLongSide: videoMaxLongSide,
            connectTimeout: connectTimeout,
            reconnectMaxAttempts: reconnectMaxAttempts,
            reconnectBaseDelay: reconnectBaseDelay,
            reconnectMaxDelay: reconnectMaxDelay,
            keepaliveInterval: keepaliveInterval,
            keepaliveTimeout: keepaliveTimeout
        )
    }

    func testValidConfig() {
        let config = makeConfig()
        XCTAssertNoThrow(try config.validate())
        XCTAssertNil(config.language)
        XCTAssertNil(config.languageProvider)
        XCTAssertNoThrow(try makeConfig(url: "ws://example.com").validate())
    }

    func testUnsupportedSampleRate() {
        XCTAssertThrowsError(try makeConfig(audioSampleRate: .hz24000).validate()) { error in
            XCTAssertEqual(error as? LivaChatError, .unsupportedSampleRate(24_000))
        }
    }

    func testUnsupportedFrameDuration() {
        XCTAssertThrowsError(try makeConfig(audioFrameMs: 40).validate()) { error in
            XCTAssertEqual(error as? LivaChatError, .unsupportedFrameDuration(40))
        }
    }

    func testInvalidQualityAndDimensions() {
        XCTAssertThrowsError(try makeConfig(videoQuality: 0).validate())
        XCTAssertThrowsError(try makeConfig(videoQuality: 101).validate())
        XCTAssertThrowsError(try makeConfig(videoMaxLongSide: 0).validate())
    }

    func testInvalidNetworkParameters() {
        XCTAssertThrowsError(try makeConfig(connectTimeout: 0).validate())
        XCTAssertThrowsError(try makeConfig(reconnectMaxAttempts: -1).validate())
        XCTAssertThrowsError(try makeConfig(reconnectMaxDelay: 0.5).validate())
        XCTAssertThrowsError(try makeConfig(keepaliveInterval: 35, keepaliveTimeout: 15).validate())
    }

    func testInvalidIdentityAndScheme() {
        XCTAssertThrowsError(try makeConfig(token: "").validate())
        XCTAssertThrowsError(try makeConfig(deviceID: "").validate())
        XCTAssertThrowsError(try makeConfig(url: "ftp://example.com").validate())
    }
}
