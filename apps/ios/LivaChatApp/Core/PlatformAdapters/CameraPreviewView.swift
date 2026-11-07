import AVFoundation
import SwiftUI

struct CameraPreviewView: UIViewRepresentable {
    let source: CameraPreviewing

    func makeCoordinator() -> Coordinator {
        Coordinator(source: source)
    }

    func makeUIView(context: Context) -> PreviewContainerView {
        let view = PreviewContainerView()
        view.backgroundColor = .black
        source.attachPreview(to: view.previewLayer)
        return view
    }

    func updateUIView(_ uiView: PreviewContainerView, context: Context) {}

    static func dismantleUIView(_ uiView: PreviewContainerView, coordinator: Coordinator) {
        coordinator.source.detachPreview()
    }

    @MainActor
    final class Coordinator {
        let source: CameraPreviewing

        init(source: CameraPreviewing) {
            self.source = source
        }
    }
}

final class PreviewContainerView: UIView {
    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    var previewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }
}
