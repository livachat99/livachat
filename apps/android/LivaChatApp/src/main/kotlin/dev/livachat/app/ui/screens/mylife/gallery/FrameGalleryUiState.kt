package dev.livachat.app.ui.screens.mylife.gallery

import dev.livachat.app.data.remote.mylife.dto.FrameDto

/** Gallery 数据态（docs/android_timeline_image_gallery.md §3.4）。 */
sealed interface FrameGalleryState {
    data object Loading : FrameGalleryState
    data object Empty : FrameGalleryState
    data class Error(val message: String) : FrameGalleryState

    /** total = 服务端计数（含被跳过的空 media_url 条，E8）；frames 只增不减（追加式）。 */
    data class Data(
        val frames: List<FrameDto>,
        val total: Int,
        val nextCursor: String?,
        val loadingMore: Boolean = false,
    ) : FrameGalleryState
}

data class FrameGalleryUiState(
    val state: FrameGalleryState = FrameGalleryState.Loading,
    /** 全屏查看器当前页；null = 关闭（覆盖层非路由，§3.5）。 */
    val viewerIndex: Int? = null,
    /** 保存到相册提示（2s TTL；null = 不显示）。 */
    val imageSaveTip: String? = null,
    /** 正在保存的帧 id（按钮转圈 + 重入守卫）。 */
    val savingFrameId: String? = null,
    // ---- 编辑模式（§9）----
    /** 编辑模式开关（顶栏「编辑」↔「退出」）。 */
    val editMode: Boolean = false,
    /** 已选帧 id 集合（上限 MAX_SELECTION=50，见 FrameGalleryViewModel）。 */
    val selectedFrameIds: Set<String> = emptySet(),
    /** 删除确认弹窗（对齐 HistoryScreen showDeleteConfirm 先例）。 */
    val deleteConfirm: Boolean = false,
    /** 删除请求在飞（按钮转圈 + 全部交互拦截）。 */
    val deleting: Boolean = false,
    /** 删除相关提示（上限/失败/超时/已删除；2s TTL；屏幕级覆盖层展示）。 */
    val deleteTip: String? = null,
)
