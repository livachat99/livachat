import SwiftUI

/// Shared agent glyph. Android uses Material's `SmartToy` robot head; SF
/// Symbols has no equivalent robot-head symbol (`robotic.vacuum` is a vacuum
/// disc), so the bundled `AgentRobot` template mirrors the Android shape.
/// Used by the avatar badge, message/timeline agent headers, result overlay
/// and job panel.
struct AgentIcon: View {
    let size: CGFloat
    let color: Color

    var body: some View {
        Image("AgentRobot")
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .frame(width: size, height: size)
            .foregroundColor(color)
            .accessibilityHidden(true)
    }
}
