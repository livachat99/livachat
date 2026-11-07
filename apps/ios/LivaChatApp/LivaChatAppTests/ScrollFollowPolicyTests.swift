import XCTest
@testable import LivaChatApp

final class ScrollFollowPolicyTests: XCTestCase {
    func testStartsFollowing() {
        XCTAssertTrue(ScrollFollowPolicy().shouldAutoScroll)
    }

    func testDragUpPausesFollowing() {
        var policy = ScrollFollowPolicy()
        policy.dragChanged(translationY: -20)
        XCTAssertFalse(policy.shouldAutoScroll)
    }

    func testDragDownDoesNotPause() {
        var policy = ScrollFollowPolicy()
        policy.dragChanged(translationY: 20)
        XCTAssertTrue(policy.shouldAutoScroll)
    }

    func testBottomWithinThresholdResumes() {
        var policy = ScrollFollowPolicy()
        policy.dragChanged(translationY: -100)
        XCTAssertFalse(policy.shouldAutoScroll)

        policy.bottomUpdated(maxY: 400 + 80, viewportHeight: 400)
        XCTAssertTrue(policy.shouldAutoScroll)
    }

    func testBottomBeyondThresholdKeepsPaused() {
        var policy = ScrollFollowPolicy()
        policy.dragChanged(translationY: -100)
        policy.bottomUpdated(maxY: 400 + 81, viewportHeight: 400)
        XCTAssertFalse(policy.shouldAutoScroll)
    }

    func testResetResumesFollowing() {
        var policy = ScrollFollowPolicy()
        policy.dragChanged(translationY: -100)
        policy.reset()
        XCTAssertTrue(policy.shouldAutoScroll)
    }
}
