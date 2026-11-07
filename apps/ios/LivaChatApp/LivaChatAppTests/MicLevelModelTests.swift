import Combine
import XCTest
@testable import LivaChatApp

@MainActor
final class MicLevelModelTests: XCTestCase {
    func testClampsToRange() {
        let model = MicLevelModel()
        model.set(-1)
        XCTAssertEqual(model.level, 0)
        model.set(5)
        XCTAssertEqual(model.level, MicLevelModel.levels)
    }

    func testDeduplicatesEqualValues() {
        let model = MicLevelModel()
        var changes = 0
        let cancellable = model.objectWillChange.sink { changes += 1 }

        model.set(2)
        model.set(2)
        model.set(2)

        XCTAssertEqual(model.level, 2)
        XCTAssertEqual(changes, 1)
        cancellable.cancel()
    }

    func testResetReturnsToZero() {
        let model = MicLevelModel()
        model.set(3)
        model.reset()
        XCTAssertEqual(model.level, 0)
    }
}
