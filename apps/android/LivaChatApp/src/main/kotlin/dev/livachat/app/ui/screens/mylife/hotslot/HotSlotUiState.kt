package dev.livachat.app.ui.screens.mylife.hotslot

import dev.livachat.app.data.remote.mylife.dto.HotSlotEvent

/** hotSlotView 数据态（docs/android_timeline_hotslot.md §4.3）。 */
sealed interface HotSlotState {
    data object Loading : HotSlotState

    /** 窗口内无内容（非 pending 空态文案 vs pending「内容分析中…」由 Screen 按 `HotSlotUiState.pending` 区分）。 */
    data object Empty : HotSlotState

    data class Error(val message: String) : HotSlotState

    /** events 只增不减（追加式）；exhausted = 翻页终止（越窗或游标耗尽，§4.3 精确语义）。 */
    data class Data(
        val events: List<HotSlotEvent>,
        val loadingMore: Boolean = false,
        val exhausted: Boolean = false,
    ) : HotSlotState
}

/** hotSlotView UI 态（查看器字段见 docs/android_timeline_hotslot.md §9.3）。 */
data class HotSlotUiState(
    val state: HotSlotState = HotSlotState.Loading,

    /**
     * 顶栏「分析中…」+ pending 空态（§4.3 Q11）。
     * 初值取路由快照（日卡 hasPending）；§11 P1①（2026-09-22 定案随 §10 批次）：
     * overview 轮询显示已无 pending 分析时由 VM **单向收敛**为 false。
     */
    val pending: Boolean = false,

    /** 非空 = 帧全屏查看器打开（Q15：持事件对象而非列表索引——Q11 重取/触底追加时不失效）。 */
    val viewerFrame: HotSlotEvent? = null,

    /**
     * 非空 = agent job 结果全屏查看器打开；值为**行身份**（`HotSlotRow.Text.key`，非快照）——
     * Screen 侧按 key 解析当前正文（重取/分析重写后自动跟新；未命中 → 守卫关窗）。§12.3.3。
     */
    val viewerAgentJobKey: String? = null,

    /** agent job 结果查看器落位偏移（打开时行内点击处字符偏移；null = 落顶部）。 */
    val agentJobInitialCharOffset: Int? = null,

    /** 保存重入守卫（单图 → 布尔即可）。 */
    val saving: Boolean = false,

    /** 保存提示（三文案 + 2s TTL，VM 统一管理）。 */
    val imageSaveTip: String? = null,
)
