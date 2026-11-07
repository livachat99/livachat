import CoreGraphics

/// Decides whether new messages may scroll the conversation to the bottom.
/// A user drag upward pauses follow mode; returning within 80 pt of the
/// bottom resumes it.
struct ScrollFollowPolicy: Equatable {
    static let bottomThreshold: CGFloat = 80
    static let dragUpThreshold: CGFloat = 10

    private(set) var shouldAutoScroll = true

    mutating func dragChanged(translationY: CGFloat) {
        if translationY < -Self.dragUpThreshold {
            shouldAutoScroll = false
        }
    }

    mutating func bottomUpdated(maxY: CGFloat, viewportHeight: CGFloat) {
        if maxY <= viewportHeight + Self.bottomThreshold {
            shouldAutoScroll = true
        }
    }

    mutating func reset() {
        shouldAutoScroll = true
    }
}
