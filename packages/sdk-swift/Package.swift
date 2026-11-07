// swift-tools-version: 6.0
import PackageDescription

// Protocol DTOs only. The iOS SDK itself lives in apps/ios/LivaChatSDK.
let package = Package(
    name: "LivaChatProtocol",
    platforms: [.iOS(.v15), .macOS(.v13)], // macOS is only for host-side swift test
    products: [
        .library(name: "LivaChatProtocol", targets: ["LivaChatProtocol"]),
    ],
    targets: [
        .target(name: "LivaChatProtocol"),
        .testTarget(
            name: "LivaChatProtocolTests",
            dependencies: ["LivaChatProtocol"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
