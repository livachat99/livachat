package dev.livachat.app.ui.screens.mylife.gallery

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.data.remote.mylife.dto.FrameDto
import dev.livachat.app.R
import dev.livachat.app.data.repository.MylifeRepository
import dev.livachat.app.media.ImageGallerySaver
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.InterruptedIOException
import javax.inject.Inject

/**
 * Gallery 屏 ViewModel（docs/android_timeline_image_gallery.md §3.3/§3.4）：
 * 首屏 + cursor 分页（页大小 40）；append 按 frame_id 去重（分析中插入新帧 → offset
 * 游标页重复，A1）；翻页互斥；失败静默保留已有页（滚动可重试）。
 * 保存到相册（§3.5）：下载 → ImageGallerySaver 字节入口；提示 TTL / 重入守卫 /
 * 关闭查看器取消在飞保存（对齐 conversation 二期口径）。
 * 编辑模式删除（§9.4.2）：选择上限 50；弹窗确认；成功剔除+重载+记延迟刷新标记
 * （Q9.3，退出画廊时由 repository flush）；失败保留选择可重试（服务端幂等）。
 */
@HiltViewModel
class FrameGalleryViewModel @Inject constructor(
    private val repository: MylifeRepository,
    private val imageGallerySaver: ImageGallerySaver,
    @ApplicationContext private val appContext: Context,
) : ViewModel() {

    private val _uiState = MutableStateFlow(FrameGalleryUiState())
    val uiState: StateFlow<FrameGalleryUiState> = _uiState.asStateFlow()

    private var from: String = ""
    private var to: String = ""
    private var loadJob: Job? = null
    private var saveJob: Job? = null
    private var tipJob: Job? = null
    private var deleteTipJob: Job? = null

    /** 首屏加载（Screen LaunchedEffect(from, to) 触发）；同范围已加载成功则跳过重拉（force 绕开）。 */
    fun load(from: String, to: String, force: Boolean = false) {
        if (from.isBlank() || to.isBlank()) return
        if (!force && from == this.from && to == this.to &&
            _uiState.value.state is FrameGalleryState.Data
        ) {
            return
        }
        this.from = from
        this.to = to
        loadJob?.cancel()
        _uiState.update { it.copy(state = FrameGalleryState.Loading) }
        loadJob = viewModelScope.launch {
            try {
                val page = repository.frames(from, to)
                _uiState.update {
                    it.copy(
                        state = if (page.frames.isEmpty()) FrameGalleryState.Empty
                        else FrameGalleryState.Data(page.frames, page.total, page.nextCursor),
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(state = FrameGalleryState.Error(e.message ?: appContext.getString(R.string.mylife_load_failed))) }
            }
        }
    }

    /** 加载更多：滚动触底 / 查看器接近已加载末尾（A2）共用；loadingMore 互斥。 */
    fun loadMore() {
        val data = _uiState.value.state as? FrameGalleryState.Data ?: return
        val cursor = data.nextCursor ?: return
        if (data.loadingMore) return
        _uiState.update { prev ->
            val d = prev.state as? FrameGalleryState.Data ?: return@update prev
            prev.copy(state = d.copy(loadingMore = true))
        }
        loadJob = viewModelScope.launch {
            try {
                val page = repository.frames(from, to, cursor = cursor)
                _uiState.update { prev ->
                    val d = prev.state as? FrameGalleryState.Data ?: return@update prev
                    val seen = d.frames.mapTo(HashSet()) { it.frameId }
                    val appended: List<FrameDto> = page.frames.filter { seen.add(it.frameId) }
                    prev.copy(
                        state = d.copy(
                            frames = d.frames + appended,
                            total = page.total,
                            nextCursor = page.nextCursor,
                            loadingMore = false,
                        ),
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // 静默保留已加载页（滚动/翻页可再次触发重试）
                _uiState.update { prev ->
                    val d = prev.state as? FrameGalleryState.Data ?: return@update prev
                    prev.copy(state = d.copy(loadingMore = false))
                }
            }
        }
    }

    fun onFrameClick(index: Int) {
        // 删除在飞期间全拦截（§9 Q9.5）：否则完成后 load() 重载替换列表 → 查看器页索引错位
        if (_uiState.value.deleting) return
        // 重开查看器清旧提示（对齐 conversation 二期：2s 内重开不显示陈语文案）
        tipJob?.cancel()
        tipJob = null
        _uiState.update { it.copy(viewerIndex = index, imageSaveTip = null) }
    }

    /** 关闭查看器：取消在飞保存与提示（避免"看不见的保存"与陈旧提示）。 */
    fun onViewerDismiss() {
        saveJob?.cancel()
        saveJob = null
        tipJob?.cancel()
        tipJob = null
        _uiState.update {
            it.copy(viewerIndex = null, savingFrameId = null, imageSaveTip = null)
        }
    }

    /**
     * 保存指定帧到相册（按 frameId 解析——权限回调可能晚于翻页，二期教训：以 id 为准，
     * 不用点击瞬间的页索引）。进行中忽略再次点击（重入守卫）。
     */
    fun saveFrameById(frameId: String) {
        if (_uiState.value.savingFrameId != null) return
        val frame = (_uiState.value.state as? FrameGalleryState.Data)
            ?.frames?.firstOrNull { it.frameId == frameId } ?: return
        if (frame.mediaUrl.isBlank()) return
        _uiState.update { it.copy(savingFrameId = frame.frameId) }
        saveJob = viewModelScope.launch {
            try {
                val bytes = repository.downloadFrame(frame.mediaUrl)
                // runCatching 把跨类契约（saver 失败返回 false、不抛）本地化：违约也收敛为
                // 「保存失败」提示，而不是沿 viewModelScope 上抛成崩溃（同 conversation 二期）
                val ok = bytes != null &&
                    runCatching { imageGallerySaver.saveJpegBytes(bytes) }.getOrDefault(false)
                showTipWithTimeout(
                    if (ok) appContext.getString(R.string.common_saved_to_gallery)
                    else appContext.getString(R.string.common_save_failed),
                )
            } finally {
                // 防未来改动把 flag 卡死、保存入口永久静默失效（finally 覆盖取消路径）
                _uiState.update { it.copy(savingFrameId = null) }
            }
        }
    }

    /** 权限被拒（API ≤ 28）：只提示，可再次点击重试（不做"去设置"引导，同二期 D14）。 */
    fun onSavePermissionDenied() {
        showTipWithTimeout(appContext.getString(R.string.common_save_failed_storage))
    }

    /**
     * 统一提示 TTL：先取消上一个定时器再展示 2s（文案比对防不了顺序保存串台——同二期）。
     */
    private fun showTipWithTimeout(text: String) {
        tipJob?.cancel()
        tipJob = viewModelScope.launch {
            _uiState.update { it.copy(imageSaveTip = text) }
            delay(TIP_TTL_MS)
            _uiState.update { it.copy(imageSaveTip = null) }
        }
    }

    // ---- 编辑模式与删除（docs/android_timeline_image_gallery.md §9.4.2）----

    fun enterEditMode() {
        if (_uiState.value.deleting) return
        _uiState.update { it.copy(editMode = true, selectedFrameIds = emptySet()) }
    }

    /** 退出编辑模式（已选不保留）；删除在飞时 no-op（Q9.5 全拦截）。 */
    fun exitEditMode() {
        if (_uiState.value.deleting) return
        _uiState.update {
            it.copy(editMode = false, selectedFrameIds = emptySet(), deleteConfirm = false)
        }
    }

    /** 点选择框：切换选中。无 eventRef 的帧不可选（防御，Q9.7）；满 50 上限提示不选中（Q9.6）。 */
    fun toggleSelect(frame: FrameDto) {
        val s = _uiState.value
        if (!s.editMode || s.deleting) return
        if (frame.eventRef.isNullOrEmpty()) return
        if (frame.frameId in s.selectedFrameIds) {
            _uiState.update { it.copy(selectedFrameIds = it.selectedFrameIds - frame.frameId) }
        } else if (s.selectedFrameIds.size >= MAX_SELECTION) {
            showDeleteTipWithTimeout(appContext.getString(R.string.mylife_max_selection))
        } else {
            _uiState.update { it.copy(selectedFrameIds = it.selectedFrameIds + frame.frameId) }
        }
    }

    /** 底栏「删除 (N)」→ 开确认弹窗（Q9.1）。 */
    fun requestDelete() {
        val s = _uiState.value
        if (!s.editMode || s.deleting || s.selectedFrameIds.isEmpty()) return
        _uiState.update { it.copy(deleteConfirm = true) }
    }

    fun dismissDeleteConfirm() {
        _uiState.update { it.copy(deleteConfirm = false) }
    }

    /**
     * 删除选中帧（弹窗「删除」确认触发）。成功：not_found 视作已删、剔除并按 Q9.2 整页重载、
     * 退出编辑模式、记延迟刷新标记（Q9.3——不在画廊内刷新，退出画廊时由 repository flush）。
     * 失败（超时/网络/5xx）：保留选择与编辑模式，可直接重试（服务端幂等，D7）。
     */
    fun deleteSelected() {
        val s = _uiState.value
        _uiState.update { it.copy(deleteConfirm = false) }
        if (!s.editMode || s.deleting || s.selectedFrameIds.isEmpty()) return
        val frames = (s.state as? FrameGalleryState.Data)?.frames ?: return
        val refToFrame = frames
            .filter { it.frameId in s.selectedFrameIds }
            .mapNotNull { f -> f.eventRef?.takeIf(String::isNotEmpty)?.let { it to f.frameId } }
        if (refToFrame.isEmpty()) return
        _uiState.update { it.copy(deleting = true) }
        viewModelScope.launch {
            try {
                val resp = repository.removeEvents(refToFrame.map { it.first })
                // not_found = 已不可见（并发已删/重复提交）→ 与 removed 同视作已删（Q9.4）
                val gone = resp.removed.toSet() +
                    resp.skipped.filter { it.reason == REASON_NOT_FOUND }.map { it.eventRef }
                val failed = resp.skipped.count { it.reason != REASON_NOT_FOUND }
                showDeleteTipWithTimeout(
                    if (failed == 0) {
                        appContext.resources.getQuantityString(
                            R.plurals.mylife_photos_deleted, gone.size, gone.size,
                        )
                    } else {
                        appContext.getString(R.string.mylife_photos_deleted_partial, gone.size, failed)
                    },
                )
                _uiState.update { it.copy(editMode = false, selectedFrameIds = emptySet()) }
                repository.markPendingRemovalRefresh()
                load(from, to, force = true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // read/call 超时均为 InterruptedIOException（SocketTimeoutException 是其子类）
                showDeleteTipWithTimeout(
                    if (e is InterruptedIOException) appContext.getString(R.string.mylife_delete_timeout)
                    else appContext.getString(R.string.mylife_delete_failed),
                )
            } finally {
                _uiState.update { it.copy(deleting = false) }
            }
        }
    }

    override fun onCleared() {
        // 退出画廊（返回栈弹出才触发，旋转不触发）→ 有删除记录则强刷已加载窗口（Q9.3）
        repository.flushPendingRemovalRefresh()
        super.onCleared()
    }

    /** 删除提示 TTL（同保存链模式：先取消上一个定时器再展示 2s）。 */
    private fun showDeleteTipWithTimeout(text: String) {
        deleteTipJob?.cancel()
        deleteTipJob = viewModelScope.launch {
            _uiState.update { it.copy(deleteTip = text) }
            delay(TIP_TTL_MS)
            _uiState.update { it.copy(deleteTip = null) }
        }
    }

    private companion object {
        const val TIP_TTL_MS = 2_000L

        /** 选择上限 = 服务端批量上限（bff_service_remove.md §2.1：1–50）。 */
        const val MAX_SELECTION = 50
        const val REASON_NOT_FOUND = "not_found"
    }
}
