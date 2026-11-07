import Foundation
import XCTest
@testable import LivaChatSDK

final class SeqGeneratorTests: XCTestCase {
    func testMonotonic() {
        let generator = SeqGenerator()
        XCTAssertEqual(generator.next(), 1)
        XCTAssertEqual(generator.next(), 2)
        XCTAssertEqual(generator.next(), 3)
    }

    func testConcurrentAllocationIsUnique() {
        let generator = SeqGenerator()
        let lock = NSLock()
        var values: [UInt32] = []

        DispatchQueue.concurrentPerform(iterations: 1_000) { _ in
            let value = generator.next()
            lock.lock()
            values.append(value)
            lock.unlock()
        }

        XCTAssertEqual(values.count, 1_000)
        XCTAssertEqual(Set(values).count, 1_000)
        XCTAssertEqual(values.min(), 1)
        XCTAssertEqual(values.max(), 1_000)
    }
}
