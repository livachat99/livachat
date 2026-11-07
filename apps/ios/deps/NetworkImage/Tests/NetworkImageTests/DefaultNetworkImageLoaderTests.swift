import Foundation
import XCTest

@testable import NetworkImage

private final class TestCounter: @unchecked Sendable {
  private let lock = NSLock()
  private var storage = 0

  @discardableResult
  func increment() -> Int {
    lock.lock()
    defer { lock.unlock() }
    storage += 1
    return storage
  }

  var value: Int {
    lock.lock()
    defer { lock.unlock() }
    return storage
  }
}

final class DefaultNetworkImageLoaderTests: XCTestCase {
  func testImageLoad() async throws {
    // given
    let imageCache = DefaultNetworkImageCache()

    let loadCount = TestCounter()
    let imageLoader = DefaultNetworkImageLoader(cache: imageCache) { _ in
      loadCount.increment()
      return (Fixtures.imageData, Fixtures.okResponse)
    }

    // when
    async let firstImage = imageLoader.image(from: Fixtures.url)
    async let secondImage = imageLoader.image(from: Fixtures.anotherURL)
    async let thirdImage = imageLoader.image(from: Fixtures.url)

    let images = try await (firstImage, secondImage, thirdImage)

    // then
    XCTAssertEqual(loadCount.value, 2)
    XCTAssertIdentical(images.0, images.2)
    XCTAssertIdentical(imageCache.image(for: Fixtures.url), images.0)
    XCTAssertIdentical(imageCache.image(for: Fixtures.anotherURL), images.1)
  }

  func testImageCache() async throws {
    // given
    let imageCache = DefaultNetworkImageCache()
    imageCache.setImage(Fixtures.image, for: Fixtures.url)

    let loadCount = TestCounter()
    let imageLoader = DefaultNetworkImageLoader(cache: imageCache) { _ in
      loadCount.increment()
      return (Fixtures.imageData, Fixtures.okResponse)
    }

    // when
    let image = try await imageLoader.image(from: Fixtures.url)

    // then
    XCTAssertEqual(loadCount.value, 0)
    XCTAssertIdentical(image, Fixtures.image)
  }

  func testImageBadServerResponse() async throws {
    // given
    let imageLoader = DefaultNetworkImageLoader(cache: .default) { _ in
      return (.init(), Fixtures.internalServerErrorResponse)
    }

    // when
    var capturedError: URLError?
    do {
      _ = try await imageLoader.image(from: Fixtures.url)
      XCTFail("Asynchronous call did not throw an error.")
    } catch {
      capturedError = error as? URLError
    }

    // then
    XCTAssertEqual(capturedError, URLError(.badServerResponse))
  }

  func testImageCannotDecodeContent() async throws {
    // given
    let imageLoader = DefaultNetworkImageLoader(cache: .default) { _ in
      return (Data([0xde, 0xad, 0xbe, 0xef]), Fixtures.okResponse)
    }

    // when
    var capturedError: URLError?
    do {
      _ = try await imageLoader.image(from: Fixtures.url)
      XCTFail("Asynchronous call did not throw an error.")
    } catch {
      capturedError = error as? URLError
    }

    // then
    XCTAssertEqual(capturedError, URLError(.cannotDecodeContentData))
  }

  func testTransportFailureIsRetried() async throws {
    // given
    let imageCache = DefaultNetworkImageCache()
    let loadCount = TestCounter()
    let imageLoader = DefaultNetworkImageLoader(cache: imageCache) { _ in
      if loadCount.increment() == 1 {
        throw URLError(.timedOut)
      }
      return (Fixtures.imageData, Fixtures.okResponse)
    }

    // when
    do {
      _ = try await imageLoader.image(from: Fixtures.url)
      XCTFail("first load must fail")
    } catch {
      XCTAssertEqual(error as? URLError, URLError(.timedOut))
    }

    let image = try await imageLoader.image(from: Fixtures.url)

    // then
    XCTAssertEqual(loadCount.value, 2)
    XCTAssertIdentical(imageCache.image(for: Fixtures.url), image)
  }

  func testShutdownRejectsNewLoads() async throws {
    // given
    let imageLoader = DefaultNetworkImageLoader(cache: DefaultNetworkImageCache()) { _ in
      return (Fixtures.imageData, Fixtures.okResponse)
    }

    // when
    await imageLoader.shutdown()

    // then
    do {
      _ = try await imageLoader.image(from: Fixtures.url)
      XCTFail("load after shutdown must fail")
    } catch {
      XCTAssertEqual(error as? URLError, URLError(.cancelled))
    }
  }
}
