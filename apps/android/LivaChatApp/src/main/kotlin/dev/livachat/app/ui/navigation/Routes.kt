package dev.livachat.app.ui.navigation

object Routes {
    const val SPLASH = "splash"
    const val PERMISSION = "permission"
    const val CONVERSATION = "conversation"
    const val HISTORY = "history"
    const val HISTORY_DETAIL = "history/{sessionId}"
    const val SETTINGS = "settings"
    const val WEBVIEW = "webview/{url}"

    // MyLife 宿主（设计文档 §6.3 / D11：单 NavHost 的 App 级路由）
    // 2026-09-22 §14 R1/R3=C：原 MYLIFE+REPLAY 两条已在删 Replay 后仅剩本条；tab/date 参数亦随批退役
    const val MYLIFE = "mylife"
    // Gallery（docs/android_timeline_image_gallery.md §3.3）：范围必选；label 展示文案；
    // focus 被点帧 id（可空，允许空串）；pending "true"/"false"
    const val MYLIFE_GALLERY = "mylife/gallery?from={from}&to={to}&label={label}&focus={focus}&pending={pending}"
    // hotSlotView（docs/android_timeline_hotslot.md §4.3）：start/end 必选（epoch ms 取数窗口 + 标题）；
    // pending "true"/"false" 顶栏「分析中…」。无日期参数——取数日由 start/end 现推（§3.3 时区约束）
    const val MYLIFE_HOT_SLOT = "mylife/hotslot?start={start}&end={end}&pending={pending}"
    // eventListViewer（docs/android_timeline_topics.md §3.3.2）：ts 必选（被点事件时刻，窗口 ts±10min）；
    // pending "true"/"false" 顶栏「分析中…」
    const val MYLIFE_EVENT_LIST = "mylife/events?ts={ts}&pending={pending}"
    // AudioGallery（docs/android_timeline_audio_gallery.md §4.1）：date 必选（本地日，日卡 key）；
    // label 展示文案；count 日卡段数快照（首屏成功前顶栏「N 段」，§4.7）；pending 顶栏「分析中…」
    const val MYLIFE_AUDIO = "mylife/audio?date={date}&label={label}&count={count}&pending={pending}"

    fun historyDetail(id: String) = "history/$id"
    fun webview(url: String) = "webview/${android.net.Uri.encode(url)}"
    fun mylife() = MYLIFE

    /** gallery 路由：from/to 为本地日 DATE（含端点），label 需编码（含空格/斜杠等）。 */
    fun mylifeGallery(
        from: String,
        to: String,
        label: String,
        focusFrameId: String? = null,
        pending: Boolean = false,
    ) = "mylife/gallery?from=$from&to=$to&label=${android.net.Uri.encode(label)}" +
        "&focus=${focusFrameId.orEmpty()}&pending=$pending"

    /** hotSlot 路由：start/end 必选（取数窗口 + 标题），pending 顶栏提示。 */
    fun mylifeHotSlot(startMs: Long, endMs: Long, pending: Boolean) =
        "mylife/hotslot?start=$startMs&end=$endMs&pending=$pending"

    /** eventList 路由：ts 必选（被点事件时刻，窗口 ts±10min），pending 顶栏提示。 */
    fun mylifeEventList(tsMs: Long, pending: Boolean) =
        "mylife/events?ts=$tsMs&pending=$pending"

    /** audio 路由：date 必选（本地日），label（含空格等需编码）/count/pending 供顶栏。 */
    fun mylifeAudio(date: String, label: String, count: Int, pending: Boolean) =
        "mylife/audio?date=$date&label=${android.net.Uri.encode(label)}&count=$count&pending=$pending"
}
