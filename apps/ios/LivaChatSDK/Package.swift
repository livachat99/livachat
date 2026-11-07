// swift-tools-version: 6.0
import PackageDescription

// LivaChat iOS SDK library. Mirrors apps/android/LivaChatSDK.
// Protocol DTOs are shared from packages/sdk-swift.
let package = Package(
    name: "LivaChatSDK",
    platforms: [.iOS(.v15), .macOS(.v13)], // macOS is only for host-side swift test
    products: [
        .library(name: "LivaChatSDK", targets: ["LivaChatSDK"]),
    ],
    dependencies: [
        .package(path: "../../../packages/sdk-swift"),
    ],
    targets: [
        // Prebuilt native libraries. Paths are relative to this package root.
        .binaryTarget(
            name: "Opus",
            path: "../../../3rdparty/dist-ios/Opus.xcframework"
        ),
        .binaryTarget(
            name: "WebRtcVadLite",
            path: "../../../3rdparty/dist-ios/WebRtcVadLite.xcframework"
        ),

        // C shims isolate the prebuilt headers from Swift and keep a stable API.
        .target(
            name: "COpus",
            dependencies: [
                .target(name: "Opus", condition: .when(platforms: [.iOS]))
            ]
        ),
        .target(
            name: "CVad",
            dependencies: [
                .target(name: "WebRtcVadLite", condition: .when(platforms: [.iOS]))
            ]
        ),

        // Minimal ObjC bridge for raw WebSocket close codes (e.g. 4000) that
        // Foundation's Swift CloseCode enum cannot represent.
        .target(name: "CWebSocketClose"),

        .target(
            name: "LivaChatSDK",
            dependencies: [
                .product(name: "LivaChatProtocol", package: "sdk-swift"),
                "CWebSocketClose",
                .target(name: "COpus", condition: .when(platforms: [.iOS])),
                .target(name: "CVad", condition: .when(platforms: [.iOS])),
            ]
        ),

        .testTarget(
            name: "LivaChatSDKTests",
            dependencies: [
                "LivaChatSDK",
                .product(name: "LivaChatProtocol", package: "sdk-swift"),
            ]
        ),
    ]
)
