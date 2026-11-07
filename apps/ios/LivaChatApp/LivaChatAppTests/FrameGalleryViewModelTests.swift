import XCTest
@testable import LivaChatApp

private final class FailingPhotoSaver: PhotoLibrarySaving {
    func save(jpeg: Data, completion: @escaping @MainActor (Bool) -> Void) {
        Task { @MainActor in completion(false) }
    }
}

@MainActor
final class FrameGalleryViewModelTests: XCTestCase {
    private func waitUntil(
        timeout: TimeInterval = 2,
        _ condition: @escaping () -> Bool
    ) async {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 30_000_000)
        }
        XCTFail("condition not met")
    }

    func testLoadViewerSelectionAndDeleteFailure() async {
        let viewModel = FrameGalleryViewModel(store: UITestMylifeStore())
        viewModel.load(from: "2026-08-21", to: "2026-08-21")
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }
        XCTAssertEqual(viewModel.total, 8)
        XCTAssertEqual(viewModel.frames.count, 8)
        XCTAssertEqual(viewModel.frames.first?.frameId, "uitest-frame")

        viewModel.onFrameClick(0)
        XCTAssertEqual(viewModel.viewerIndex, 0)
        viewModel.dismissViewer()
        XCTAssertNil(viewModel.viewerIndex)

        viewModel.enterEditMode()
        let frame = try! XCTUnwrap(viewModel.frames.first)
        viewModel.toggleSelect(frame)
        XCTAssertTrue(viewModel.selectedFrameIds.contains("uitest-frame"))
        viewModel.requestDelete()
        XCTAssertTrue(viewModel.deleteConfirm)
        viewModel.dismissDeleteConfirm()
        XCTAssertFalse(viewModel.deleteConfirm)

        viewModel.deleteSelected()
        await waitUntil { viewModel.deleteTip != nil }
        XCTAssertEqual(viewModel.deleteTip, L10n.t("mylife_delete_failed"))
        XCTAssertTrue(viewModel.editMode)
        XCTAssertTrue(viewModel.selectedFrameIds.contains("uitest-frame"))
        XCTAssertFalse(viewModel.deleting)
    }

    func testSaveFailsForInvalidImageBytes() async {
        let viewModel = FrameGalleryViewModel(store: UITestMylifeStore(), saver: FailingPhotoSaver())
        viewModel.load(from: "2026-08-21", to: "2026-08-21")
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }
        viewModel.onFrameClick(0)
        viewModel.saveFrame(id: "uitest-frame")
        await waitUntil { viewModel.imageSaveTip != nil }
        XCTAssertEqual(viewModel.imageSaveTip, L10n.t("common_save_failed"))
        XCTAssertNil(viewModel.savingFrameId)
    }

    func testSelectionCapAndMissingEventRef() async {
        let viewModel = FrameGalleryViewModel(store: UITestMylifeStore())
        viewModel.load(from: "2026-08-21", to: "2026-08-21")
        await waitUntil {
            if case .data = viewModel.state { return true }
            return false
        }
        viewModel.enterEditMode()
        for frame in viewModel.frames {
            viewModel.toggleSelect(frame)
        }
        XCTAssertEqual(viewModel.selectedFrameIds.count, min(50, viewModel.frames.count))

        let noRef = FrameItem(
            tsMs: 1,
            mediaUrl: URL(string: "https://example.com/x.jpg")!,
            frameId: "no-ref",
            windowId: nil,
            caption: nil,
            eventRef: nil
        )
        viewModel.toggleSelect(noRef)
        XCTAssertFalse(viewModel.selectedFrameIds.contains("no-ref"))
    }
}
