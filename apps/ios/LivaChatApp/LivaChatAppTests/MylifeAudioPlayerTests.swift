import XCTest
@testable import LivaChatApp

@MainActor
final class MylifeAudioPlayerTests: XCTestCase {
    private func wavData(seconds: Int) -> Data {
        let sampleRate = 16_000
        let frames = sampleRate * seconds
        let dataSize = frames * 2
        var data = Data()
        func le32(_ value: UInt32) {
            var v = value.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        func le16(_ value: UInt16) {
            var v = value.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }
        data.append(contentsOf: Array("RIFF".utf8))
        le32(UInt32(36 + dataSize))
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        le32(16)
        le16(1)
        le16(1)
        le32(UInt32(sampleRate))
        le32(UInt32(sampleRate * 2))
        le16(2)
        le16(16)
        data.append(contentsOf: Array("data".utf8))
        le32(UInt32(dataSize))
        data.append(Data(count: dataSize))
        return data
    }

    private func waitUntil(
        timeout: TimeInterval = 3,
        _ condition: @escaping () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 30_000_000)
        }
        XCTFail("condition not met")
    }

    func testTogglePauseSeekResumeAndStop() async {
        let player = MylifeAudioPlayer { _ in self.wavData(seconds: 3) }
        let url = URL(fileURLWithPath: "/tmp/uitest.m4a")

        player.toggle(key: "a", url: url)
        await waitUntil { player.state.isPlaying && !player.state.isBuffering }
        XCTAssertEqual(player.state.key, "a")
        XCTAssertGreaterThan(player.state.durationMs, 0)

        player.pause()
        XCTAssertFalse(player.state.isPlaying)
        let paused = player.state.positionMs

        player.seek(to: 1_000)
        XCTAssertGreaterThanOrEqual(player.state.positionMs, 1_000)

        player.toggle(key: "a", url: url, startAtMs: 1_500)
        await waitUntil { player.state.isPlaying }
        XCTAssertGreaterThanOrEqual(player.state.positionMs, 1_400)

        player.toggle(key: "a", url: url)
        XCTAssertFalse(player.state.isPlaying)

        player.stop()
        XCTAssertNil(player.state.key)
        XCTAssertFalse(player.state.isPlaying)
        _ = paused
    }

    func testDownloadFailureSetsPlaybackError() async {
        let player = MylifeAudioPlayer { _ in
            throw MylifeAPIError(statusCode: 500, code: "internal", message: "failed")
        }
        player.toggle(key: "a", url: URL(fileURLWithPath: "/tmp/x.m4a"))
        await waitUntil { player.state.error != nil }
        XCTAssertEqual(player.state.error, L10n.t("player_play_failed"))
        XCTAssertEqual(player.state.key, "a")
        player.stop()
    }
}
