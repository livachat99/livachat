import Foundation

internal enum SDKQueue {
    static let connection = DispatchQueue(
        label: "dev.livachat.sdk.connection",
        qos: .userInitiated
    )
    static let session = DispatchQueue(
        label: "dev.livachat.sdk.session",
        qos: .userInitiated
    )
    static let audioSend = DispatchQueue(
        label: "dev.livachat.sdk.audio.send",
        qos: .userInitiated
    )
    static let audioRecv = DispatchQueue(
        label: "dev.livachat.sdk.audio.recv",
        qos: .userInteractive
    )
    static let videoSend = DispatchQueue(
        label: "dev.livachat.sdk.video.send",
        qos: .userInitiated
    )
    static let log = DispatchQueue(
        label: "dev.livachat.sdk.log",
        qos: .utility
    )
}
