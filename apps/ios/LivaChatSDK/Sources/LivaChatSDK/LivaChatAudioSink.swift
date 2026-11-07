import Foundation

/// Receives decoded PCM audio from the SDK.
///
/// Callbacks run on the audio receive serial queue (not Main). Implementations
/// must return quickly; copy or enqueue the samples and return.
public protocol LivaChatAudioSink: AnyObject, Sendable {
    func livaChat(_ sdk: LivaChatSDK, didReceiveAudio frame: AudioPCMFrame)
}
