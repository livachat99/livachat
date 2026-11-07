import XCTest
@testable import LivaChatApp

final class ServerAddressTests: XCTestCase {
    func testParsesIPv4AndDomain() throws {
        let ip = try XCTUnwrap(ServerAddress(input: " 192.168.1.10:8080 "))
        XCTAssertEqual(ip.host, "192.168.1.10")
        XCTAssertEqual(ip.port, 8080)
        XCTAssertEqual(ip.webSocketURL.absoluteString, "wss://192.168.1.10:8080/v1/ws/stream")
        XCTAssertEqual(ip.healthURL.absoluteString, "https://192.168.1.10:8080/healthz_token")

        let domain = try XCTUnwrap(ServerAddress(input: "Chat.Example.COM:443"))
        XCTAssertEqual(domain.host, "chat.example.com")
        XCTAssertEqual(domain.normalizedInput, "chat.example.com:443")
    }

    func testParsesIPv6Literal() throws {
        let address = try XCTUnwrap(ServerAddress(input: "[::1]:9000"))
        XCTAssertEqual(address.host, "::1")
        XCTAssertEqual(address.port, 9000)
        XCTAssertEqual(address.normalizedInput, "[::1]:9000")
    }

    func testRejectsInvalidInputs() {
        let invalid = [
            "",
            "   ",
            "example.com",
            "wss://example.com:8080",
            "http://example.com:8080",
            "example.com:0",
            "example.com:65536",
            "example.com:abc",
            "example.com:8080/path",
            "example.com:8080?x=1",
            "example.com:8080#fragment",
            "user:pass@example.com:8080"
        ]
        for input in invalid {
            XCTAssertNil(ServerAddress(input: input), "should reject \(input)")
        }
    }

    func testWireTokenKnownVector() {
        XCTAssertEqual(
            WireToken.make(secret: "dev-livachat-token"),
            "1161f736057f8e087b6351a66254f32510d0121c"
        )
    }

    func testWireTokenIsDeterministicAndUtf8() {
        XCTAssertEqual(WireToken.make(secret: "x"), WireToken.make(secret: "x"))
        XCTAssertNotEqual(WireToken.make(secret: "x"), WireToken.make(secret: "X"))
        XCTAssertEqual(WireToken.make(secret: "密钥").count, 40)
    }
}
