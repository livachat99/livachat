import NetworkImage
import SwiftUI

struct MylifeRemoteImage: View {
    let url: URL?
    @Environment(\.displayScale) private var displayScale

    var body: some View {
        NetworkImage(url: url, scale: displayScale) { state in
            switch state {
            case .empty, .failure:
                MylifePalette.panel
            case let .success(image, _):
                image
                    .resizable()
                    .scaledToFill()
            }
        }
        .background(MylifePalette.panel)
        .clipped()
        .accessibilityHidden(true)
    }
}

/// Square thumbnail container. `Color.clear` fixes the layout to 1:1 regardless
/// of the remote image's intrinsic aspect ratio (Android: `aspectRatio(1f)`).
struct MylifeSquareImage: View {
    let url: URL?

    var body: some View {
        Color.clear
            .aspectRatio(1, contentMode: .fit)
            .overlay {
                MylifeRemoteImage(url: url)
            }
            .clipShape(RoundedRectangle(cornerRadius: AppRadius.s))
            .contentShape(Rectangle())
    }
}
