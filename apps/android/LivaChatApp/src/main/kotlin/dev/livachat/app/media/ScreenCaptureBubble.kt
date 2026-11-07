package dev.livachat.app.media

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.hardware.display.DisplayManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.TextPaint
import android.text.TextUtils
import android.util.DisplayMetrics
import android.util.TypedValue
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import androidx.compose.ui.graphics.toArgb
import androidx.core.graphics.ColorUtils
import dagger.hilt.android.qualifiers.ApplicationContext
import dev.livachat.app.R
import dev.livachat.app.ui.theme.AvatarGradientEnd
import dev.livachat.app.ui.theme.Green
import dev.livachat.app.ui.theme.PillBlack
import dev.livachat.app.ui.theme.Red
import dev.livachat.app.ui.theme.TextPrimary
import dev.livachat.app.ui.theme.TranslucentBlack
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.abs

/** 浮窗 → controller 的点按回调（`show(host)` 注入，controller 实现）。 */
interface ScreenCaptureBubbleHost {
    fun onBubbleTap()
}

/** 浮窗贴边方向（横向只允许贴左/贴右，§5.2 需求 2）。 */
internal enum class Side { LEFT, RIGHT }

/**
 * 屏幕抓取浮窗（docs/android_screen_recording.md §4.2）。
 *
 * `TYPE_APPLICATION_OVERLAY` 窗口的唯一持有者：自绘 "~" 标记圆 + 提示胶囊、拖拽
 * 吸附（横向只贴左/右）、抓取期间瞬时隐藏（防"浮窗入镜"）、反馈渲染。
 *
 * 线程约定：**所有方法都必须在主线程调用**——窗口操作不可跨线程，且"隐藏先于
 * 取帧"的顺序依赖调用方在主线程**同步**完成隐藏（controller 以 mainHandler.post
 * 保证）。未挂载时除 `show` 外全部 no-op（不重建窗口、不复活已结束的会话）。
 *
 * 提示胶囊（§5.2）：`SENT` 期间窗口追加 `FLAG_NOT_TOUCHABLE`（触摸穿透，与 600ms
 * 防连点自然衔接）；`REJECTED_*`/`FAILED_*` **不追加**（这三类都引导"换条件重试"，
 * 锁 1.5s 会吃掉"立即重试"的点击；代价是胶囊加宽区 1.5s 吞下层点击，可接受）。
 */
@Singleton
class ScreenCaptureBubble @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val windowManager = context.getSystemService(WindowManager::class.java)
    private val displayManager = context.getSystemService(DisplayManager::class.java)
    private val density = context.resources.displayMetrics.density
    private val touchSlop = ViewConfiguration.get(context).scaledTouchSlop

    private var host: ScreenCaptureBubbleHost? = null
    private var view: BubbleView? = null
    private var params: WindowManager.LayoutParams? = null
    private var attached = false
    private var side = Side.RIGHT
    // v4 等待态（docs/android_screen_recording.md §4.2）：抓取已发出、等本轮 ai_text
    private var waiting = false
    // 会话内记忆的贴边位置（bubble 左上角 x）；y 为 null 表示首次挂载纵向居中
    private var bubbleX = 0
    private var bubbleY: Int? = null
    private var feedbackSeq = 0
    private var dragStartX = 0
    private var dragStartY = 0

    private val displayListener = object : DisplayManager.DisplayListener {
        override fun onDisplayAdded(displayId: Int) = Unit

        override fun onDisplayRemoved(displayId: Int) = Unit

        override fun onDisplayChanged(displayId: Int) {
            if (displayId == Display.DEFAULT_DISPLAY) relayout()
        }
    }

    // ── 公开 API（主线程）──────────────────────────────────────────

    /**
     * 幂等挂载（已挂载直接 true）。`addView` 抛异常（权限运行中被回收、平台拒绝）
     * 返回 false —— 浮窗是 v2 唯一抓取入口，调用方据此升级为启动失败（§5.9）。
     */
    fun show(host: ScreenCaptureBubbleHost): Boolean {
        this.host = host
        if (attached) return true
        if (frameWidthPx() <= 0 || frameHeightPx() <= 0) return false
        val newView = BubbleView().apply {
            pillOnLeft = side == Side.RIGHT
            // 消掉"flag 已置、渲染未置"的分叉（§4.2 注意⑥）：等待态正常只在挂载期产生
            waiting = this@ScreenCaptureBubble.waiting
        }
        val newParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            // FLAG_NOT_FOCUSABLE 隐含 FLAG_NOT_TOUCH_MODAL：窗口外触摸落到下层窗口
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        )
        newParams.gravity = Gravity.TOP or Gravity.START
        bubbleX = snapX(side, frameWidthPx(), bubbleSizePx(), edgeMarginPx())
        newParams.x = bubbleX
        newParams.y = clampY(
            bubbleY ?: (frameHeightPx() - bubbleSizePx()) / 2,
            frameHeightPx(), bubbleSizePx(), topMarginPx(), bottomMarginPx(),
        )
        try {
            windowManager.addView(newView, newParams)
        } catch (e: Exception) {
            // addView 失败（权限运行中被回收/平台拒绝）：调用方升级为启动失败（§5.9）
            return false
        }
        // 窗口已挂载：**先落地引用再做事后步骤** —— 若把事后步骤并进 addView 的 try，
        // 抛异常会走 catch 把 view/params 清空，而已挂载的窗口失去引用后
        // `hide()` 无法移除 → 永远悬浮在屏幕上（比启动失败严重得多）。故单独兜底。
        view = newView
        params = newParams
        attached = true
        bubbleY = newParams.y
        // 旋转重排通道：注册失败仅失去即时重排（tick/下次交互仍按实测父框定位）
        runCatching { displayManager.registerDisplayListener(displayListener, mainHandler) }
        return true
    }

    /** 幂等移除（v1 D11 全部停录路径汇于此）。 */
    fun hide() {
        if (!attached) return
        attached = false
        feedbackSeq++
        runCatching { displayManager.unregisterDisplayListener(displayListener) }
        view?.let { v -> runCatching { windowManager.removeView(v) } }
        view = null
        params = null
        host = null
        // v4 D9：等待态随录制会话结束复位（controller 侧标志由 endWaiting 归零）
        waiting = false
        // D14：位置只在会话内记忆——停止后再启动复位为默认（贴右、纵向居中）
        side = Side.RIGHT
        bubbleY = null
    }

    /** 抓取前瞬时隐藏（无动画：半透明残留会进帧），取帧完成后由 `presentFeedback` 恢复。 */
    fun hideForCapture() {
        if (!attached) return
        feedbackSeq++ // 取消未到期提示
        view?.visibility = View.INVISIBLE
    }

    /** 确保可见 + 渲染反馈（抓取收尾与门控拒绝共用）；到 TTL 自动回 IDLE。 */
    fun presentFeedback(feedback: CaptureFeedback) {
        if (!attached) return
        val v = view ?: return
        v.maxPillTextWidth = maxPillTextWidth(feedback) // 先定量宽上限，再置文案触发重绘
        v.feedback = feedback
        v.visibility = View.VISIBLE
        setTouchable(feedback != CaptureFeedback.SENT)
        anchorPill()
        val seq = ++feedbackSeq
        mainHandler.postDelayed({
            if (seq == feedbackSeq && attached) {
                view?.feedback = null
                setTouchable(true)
                anchorPill()
            }
        }, if (feedback == CaptureFeedback.SENT) SENT_TTL_MS else FEEDBACK_TTL_MS)
    }

    /**
     * v4 等待态（docs/android_screen_recording.md §4.2）：置位后绿环改画旋转缺口弧
     * （系统动画缩放=0 时为静止整圈，D5）；到点由 controller 置回 false——首个本轮
     * `ai_text` / 15s 超时 / 停录任一到达（§4.3）。
     *
     * 主线程；幂等；未挂载时只置位（`show()` 以本 flag 初始化新 view）。
     */
    fun setWaiting(waiting: Boolean) {
        if (this.waiting == waiting) return
        this.waiting = waiting
        view?.waiting = waiting
    }

    // ── 布局与手势（主线程）────────────────────────────────────────

    /**
     * 胶囊在窗口内的加宽只向"屏幕内侧"生长：贴右时窗口左边扩展（x 回退 extra），
     * 否则胶囊会顶出屏幕右缘。`extraWidth` 由视图按当前文案量出。
     */
    private fun anchorPill() {
        val v = view ?: return
        val p = params ?: return
        val extra = if (side == Side.RIGHT) v.extraWidth() else 0
        p.x = (bubbleX - extra).coerceAtLeast(0)
        runCatching { windowManager.updateViewLayout(v, p) }
    }

    private fun setTouchable(touchable: Boolean) {
        val p = params ?: return
        val v = view ?: return
        val wanted = if (touchable) {
            p.flags and WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE.inv()
        } else {
            p.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
        }
        if (wanted != p.flags) {
            p.flags = wanted
            runCatching { windowManager.updateViewLayout(v, p) }
        }
    }

    private fun relayout() {
        val v = view ?: return
        val p = params ?: return
        bubbleX = snapX(side, frameWidthPx(), bubbleSizePx(), edgeMarginPx())
        p.y = clampY(p.y, frameHeightPx(), bubbleSizePx(), topMarginPx(), bottomMarginPx())
        bubbleY = p.y
        anchorPill()
    }

    /**
     * 拖拽起手：清提示并把窗口锚点回正到 `bubbleX`（胶囊显示期窗口 x 带内缩偏移），
     * 使拖拽全程"窗口 x == 浮窗 x"，不必在位移里补偿胶囊宽度。
     */
    private fun onDragStart() {
        val v = view ?: return
        val p = params ?: return
        feedbackSeq++
        v.feedback = null
        setTouchable(true)
        p.x = bubbleX
        runCatching { windowManager.updateViewLayout(v, p) }
        dragStartX = bubbleX
        dragStartY = p.y
    }

    private fun onDrag(dx: Int, dy: Int) {
        val v = view ?: return
        val p = params ?: return
        val width = bubbleSizePx()
        p.x = (dragStartX + dx).coerceIn(0, (frameWidthPx() - width).coerceAtLeast(0))
        p.y = clampY(dragStartY + dy, frameHeightPx(), width, topMarginPx(), bottomMarginPx())
        runCatching { windowManager.updateViewLayout(v, p) }
    }

    private fun onDragEnd() {
        val p = params ?: return
        val width = bubbleSizePx()
        side = snapSide(p.x + width / 2, frameWidthPx())
        bubbleX = snapX(side, frameWidthPx(), width, edgeMarginPx())
        p.y = clampY(p.y, frameHeightPx(), width, topMarginPx(), bottomMarginPx())
        bubbleY = p.y
        view?.pillOnLeft = side == Side.RIGHT
        anchorPill()
    }

    private fun onTap() {
        host?.onBubbleTap()
    }

    // ── 度量 ──────────────────────────────────────────────────────

    private fun realMetrics(): DisplayMetrics? {
        val display = displayManager.getDisplay(Display.DEFAULT_DISPLAY) ?: return null
        return DisplayMetrics().also { display.getRealMetrics(it) }
    }

    /**
     * 定位坐标系 = 窗口父框的可用区域（`params.x/y` 相对父框，**不是整屏**）。
     *
     * 实测（EMUI 12）：悬浮窗父框被系统按状态栏高度内缩
     * （`Frames: containing=[0,99][1176,2400]`，请求 y=1116 → 实际 frame y=1215）。
     * 用整屏度量做 clamp 会让浮窗在屏幕底部越界；而 `view.rootView` 是**浮窗自身**
     * （裸 addView 无 DecorView），量到 168×168，会把 y 夹到顶边距——必须用
     * `getWindowVisibleDisplayFrame`（屏幕坐标下的可用区域）反推父框尺寸。
     * 未挂载时退回整屏度量（首帧定位用；挂载后每次定位都用实测值）。
     */
    private val frameRect = Rect()

    private fun measureFrame(): Rect? {
        val v = view ?: return null
        v.getWindowVisibleDisplayFrame(frameRect)
        return if (frameRect.width() > 0 && frameRect.height() > 0) frameRect else null
    }

    private fun frameWidthPx(): Int =
        measureFrame()?.width() ?: realMetrics()?.widthPixels ?: 0

    private fun frameHeightPx(): Int =
        measureFrame()?.height() ?: realMetrics()?.heightPixels ?: 0

    private fun bubbleSizePx(): Int = dp(BUBBLE_DP).toInt()

    private fun edgeMarginPx(): Int = dp(EDGE_MARGIN_DP).toInt()

    private fun topMarginPx(): Int = dp(TOP_MARGIN_DP).toInt()

    private fun bottomMarginPx(): Int = dp(BOTTOM_MARGIN_DP).toInt()

    private fun dp(value: Float): Float = value * density

    private fun spToPx(value: Float): Float =
        TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_SP, value, context.resources.displayMetrics)

    /** 胶囊可用文本宽度：从浮窗内侧向外到屏幕边距（留 gap 与水平 padding）。 */
    private fun maxPillTextWidth(feedback: CaptureFeedback): Int {
        val available = if (side == Side.LEFT) {
            frameWidthPx() - bubbleX - bubbleSizePx() - edgeMarginPx()
        } else {
            bubbleX - edgeMarginPx()
        }
        return (available - dp(PILL_GAP_DP) - 2 * dp(PILL_PAD_H_DP)).coerceAtLeast(dp(48f)).toInt()
    }

    internal inner class BubbleView : View(context) {
        var feedback: CaptureFeedback? = null
            set(value) {
                field = value
                requestLayout()
                invalidate()
            }

        /** v4 等待态：绿环画旋转缺口弧；置位即确保 ticker 在跑（否则弧静止不转）。 */
        var waiting = false
            set(value) {
                field = value
                invalidate()
                ensureTicker()
            }

        /** 胶囊画在浮窗左侧（贴右时）；贴左时胶囊在右侧。 */
        var pillOnLeft = false

        var maxPillTextWidth = 0

        private val sizePx = bubbleSizePx()
        /** 可见标记（黑底 + "~" 波纹）直径；其余区域全透明，但仍属 56dp 点按热区。 */
        private val markSizePx = dp(MARK_DP).toInt()
        private val markInsetPx = (sizePx - markSizePx) / 2f
        private val gapPx = dp(PILL_GAP_DP)
        private val pillHeightPx = dp(PILL_HEIGHT_DP)
        private val pillPadPx = dp(PILL_PAD_H_DP)
        private val ringStrokePx = dp(RING_STROKE_DP)

        private val circlePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = TranslucentBlack.toArgb() }
        private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            strokeWidth = ringStrokePx
            // v4：等待态缺口弧用圆头端点（整圈反馈环不受 cap 影响）
            strokeCap = Paint.Cap.ROUND
        }
        /** 等待态弧的外接矩形（复用字段，避免每帧分配）。 */
        private val ringRectF = RectF()
        private val pillBgPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = PillBlack.toArgb() }
        private val textPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = TextPrimary.toArgb()
            textSize = spToPx(PILL_TEXT_SP)
        }
        private val glyphPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
            color = TextPrimary.toArgb()
            textSize = spToPx(GLYPH_TEXT_SP)
            textAlign = Paint.Align.CENTER
        }

        // "~" 颜色脉动：白 ⇄ 头像紫（简单双向循环；仅可见时运行，抓取隐藏期不耗帧）
        private val glyphWhite = TextPrimary.toArgb()
        private val glyphViolet = AvatarGradientEnd.toArgb()

        /**
         * "~" 颜色脉动（白 ⇄ 头像紫）与 v4 等待弧旋转的**统一**驱动，节拍按状态切换：
         * 等待态 20fps（弧要连续），常态 1fps（脉动）。
         *
         * 不用 [ValueAnimator]：实测本机（EMUI 12）在 overlay 窗口里 1100ms 的动画
         * 54ms 就跑完且不再回调（animator scale 读数为 1.0，属 ROM 行为），改用墙钟
         * 相位 + 轻量自绘 tick（仅可见时运行）。系统"动画缩放=0"时不启动 ticker：
         * 脉动保持静态白色、等待弧退化为静止整圈（D5），零自绘零功耗。
         */
        private val ticker = object : Runnable {
            override fun run() {
                // 动画缩放=0（无脉动/无旋转）时**不启动 ticker**：否则颜色/环虽静态，
                // 仍在空转 invalidate——白白驱动合成与镜像 VD 产帧（实测 §9.3）
                if (!pulseEnabled) return
                if (visibility != VISIBLE || !isAttachedToWindow) return
                invalidateMark()
                postDelayed(this, if (waiting) SPIN_TICK_MS else PULSE_TICK_MS)
            }
        }

        /** 确保 ticker 在运行（幂等）；setWaiting 与 attach/可见性切换共用。 */
        private fun ensureTicker() {
            if (!pulseEnabled) return
            removeCallbacks(ticker)
            post(ticker)
        }

        /**
         * 只脏化可见标记的矩形（不是整窗）。
         *
         * 脉动每 tick 都会驱动一次合成——而合成同时会让采集侧的镜像 VirtualDisplay
         * 产帧（队列满即被丢弃，纯浪费）。缩小脏区可减少 SF/面板的重绘范围；
         * 频率本身已压到 12fps（见 [PULSE_TICK_MS] 注释）。
         */
        private fun invalidateMark() {
            val boxLeft = if (pillOnLeft) measuredWidth - sizePx else 0
            val cx = boxLeft + sizePx / 2f
            val cy = sizePx / 2f
            val r = markSizePx / 2f + ringStrokePx + 1f
            invalidate((cx - r).toInt(), (cy - r).toInt(), (cx + r).toInt(), (cy + r).toInt())
        }
        private val pulseEnabled = android.provider.Settings.Global.getFloat(
            context.contentResolver,
            android.provider.Settings.Global.ANIMATOR_DURATION_SCALE,
            1f,
        ) > 0f

        private fun glyphFraction(nowMs: Long): Float =
            pulseFraction(nowMs, GLYPH_PULSE_MS, pulseEnabled)

        override fun onAttachedToWindow() {
            super.onAttachedToWindow()
            if (visibility == VISIBLE) ensureTicker()
        }

        override fun onDetachedFromWindow() {
            removeCallbacks(ticker)
            super.onDetachedFromWindow()
        }

        override fun onVisibilityChanged(changedView: View, visibility: Int) {
            super.onVisibilityChanged(changedView, visibility)
            if (visibility == VISIBLE && isAttachedToWindow) ensureTicker() else removeCallbacks(ticker)
        }

        private var downRawX = 0f
        private var downRawY = 0f
        private var dragging = false

        private fun pillWidth(): Int {
            val text = feedback?.let { pillTextRes(it)?.let(context::getString) } ?: return 0
            val textWidth = textPaint.measureText(text)
            val capped = minOf(textWidth, maxPillTextWidth.toFloat())
            return (capped + 2 * pillPadPx).toInt()
        }

        /** 胶囊相对浮窗的额外宽度（0 = 无胶囊）。 */
        fun extraWidth(): Int = if (feedback == null) 0 else gapPx.toInt() + pillWidth()

        override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
            setMeasuredDimension(sizePx + extraWidth(), sizePx)
        }

        override fun onDraw(canvas: Canvas) {
            val boxLeft = (if (pillOnLeft) measuredWidth - sizePx else 0).toFloat()
            val centerX = boxLeft + sizePx / 2f
            val centerY = sizePx / 2f
            val markRadius = markSizePx / 2f

            // 可见标记 = 仅环绕 "~" 的高透明黑底（其余区域全透明，点按热区仍是 56dp）：
            // 目标是把对屏幕内容的遮挡压到最小（需求 2，v1.7 观感调整）
            canvas.drawCircle(centerX, centerY, markRadius, circlePaint)

            glyphPaint.color = ColorUtils.blendARGB(
                glyphWhite, glyphViolet, glyphFraction(SystemClock.uptimeMillis()),
            )
            canvas.drawText("~", centerX, centerY - (glyphPaint.descent() + glyphPaint.ascent()) / 2f, glyphPaint)

            // 环（v4 §4.2 注意⑤：与胶囊**拆开**画——等待态与 SENT 反馈同刻开始，
            // 若把胶囊留在环的分支里，等待期前 800ms 的"屏幕截图已发送"会被吞掉）
            val ringRadius = markRadius + ringStrokePx / 2f
            if (waiting) {
                ringPaint.color = Green.toArgb()
                if (pulseEnabled) {
                    ringRectF.set(centerX - ringRadius, centerY - ringRadius, centerX + ringRadius, centerY + ringRadius)
                    canvas.drawArc(
                        ringRectF,
                        spinAngleDeg(SystemClock.uptimeMillis()),
                        SPIN_SWEEP_DEG,
                        false,
                        ringPaint,
                    )
                } else {
                    // D5：系统动画缩放=0 → 静止整圈（不启动 ticker，零功耗）
                    canvas.drawCircle(centerX, centerY, ringRadius, ringPaint)
                }
            } else {
                feedback?.let { state ->
                    ringPaint.color = when (state) {
                        CaptureFeedback.SENT -> Green.toArgb()
                        else -> Red.toArgb()
                    }
                    canvas.drawCircle(centerX, centerY, ringRadius, ringPaint)
                }
            }
            feedback?.let { drawPill(canvas, it) }
        }

        private fun drawPill(canvas: Canvas, state: CaptureFeedback) {
            val text = pillTextRes(state)?.let(context::getString) ?: return
            val width = pillWidth()
            // 胶囊贴着可见标记（不是 56dp 盒边），左右两侧对称留出透明内缩
            val left = if (pillOnLeft) markInsetPx else (sizePx - markInsetPx) + gapPx
            val top = (sizePx - pillHeightPx) / 2f
            val rect = RectF(left, top, left + width.toFloat(), top + pillHeightPx)
            canvas.drawRoundRect(rect, pillHeightPx / 2f, pillHeightPx / 2f, pillBgPaint)
            val ellipsized = TextUtils.ellipsize(
                text, textPaint, (width - 2 * pillPadPx).toFloat(), TextUtils.TruncateAt.END,
            ).toString()
            // 文字贴"外侧"对齐（远离浮窗的一侧）：浮窗在右 → 胶囊在左 → 左对齐；
            // 浮窗在左 → 胶囊在右 → 右对齐（胶囊贴文字收缩时与居中同效，为对齐契约保留）
            textPaint.textAlign = if (pillOnLeft) Paint.Align.LEFT else Paint.Align.RIGHT
            val textX = if (pillOnLeft) rect.left + pillPadPx else rect.right - pillPadPx
            canvas.drawText(
                ellipsized,
                textX,
                rect.centerY() - (textPaint.descent() + textPaint.ascent()) / 2f,
                textPaint,
            )
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downRawX = event.rawX
                    downRawY = event.rawY
                    dragging = false
                    onDragStart()
                    return true
                }

                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - downRawX).toInt()
                    val dy = (event.rawY - downRawY).toInt()
                    if (!dragging && !isTap(dx, dy, touchSlop)) dragging = true
                    if (dragging) onDrag(dx, dy)
                    return true
                }

                MotionEvent.ACTION_UP -> {
                    val dx = (event.rawX - downRawX).toInt()
                    val dy = (event.rawY - downRawY).toInt()
                    if (dragging) {
                        onDragEnd()
                    } else if (isTap(dx, dy, touchSlop)) {
                        onTap()
                    }
                    dragging = false
                    return true
                }

                MotionEvent.ACTION_CANCEL -> {
                    if (dragging) onDragEnd()
                    dragging = false
                    return true
                }
            }
            return super.onTouchEvent(event)
        }
    }

    companion object {
        internal const val BUBBLE_DP = 56f

        /** 可见标记（黑底圆 + "~" 波纹）直径；窗口与点按热区仍是 [BUBBLE_DP]。 */
        internal const val MARK_DP = 32f
        internal const val EDGE_MARGIN_DP = 4f
        internal const val TOP_MARGIN_DP = 24f
        internal const val BOTTOM_MARGIN_DP = 24f
        private const val PILL_GAP_DP = 8f
        private const val PILL_HEIGHT_DP = 32f
        private const val PILL_PAD_H_DP = 12f
        private const val RING_STROKE_DP = 3f
        private const val PILL_TEXT_SP = 13f
        private const val GLYPH_TEXT_SP = 22f
        /**
         * 单程渐变时长 = 2 × [PULSE_TICK_MS] 的整数倍（v1.17：改为 2s，配合 1fps）。
         *
         * 取整是为了让低帧率采样落在均匀步进上：1fps 采 2s 单程正好得到
         * 白(f=0) → 中(f=.5) → 紫(f=1) → 中(f=.5) → 白，四步呼吸；
         * 若仍用 1.1s，1s 采样每 tick 相位走 0.45 周期 → 看起来是不均匀的随机跳变。
         */
        private const val GLYPH_PULSE_MS = 2_000L

        /**
         * 脉动自绘周期 = **1fps**（v1.15 从 24fps 降到 12fps；v1.17 再降到 1fps）。
         *
         * 机理：浮窗可见期每 tick 一次 `invalidate` → 合成一帧 → **采集侧镜像 VD 也产一帧**
         * （无人取帧时队列满即丢，纯功耗）。实测真机（浮窗可见、App 后台、屏幕静止，
         * `dumpsys gfxinfo` 本进程出帧）：24fps → 236 帧/10s；12fps → 120；**1fps → ~10**；
         * 脉动停（动画缩放=0，不启动 ticker）→ 0（§9.3）。颜色是渐变而非运动，
         * 1fps 下仍可读（4s 一个完整呼吸）。
         */
        private const val PULSE_TICK_MS = 1_000L

        /**
         * v4 等待弧的 tick 周期 = **20fps**（等待态专用，≤15s 有界）。
         *
         * 旋转是运动而非渐变，1fps 会看成跳变；代价仅存在于"点按后等回应"的短窗口
         * （每 tick 一次自绘 → 合成 → 采集侧镜像 VD 产帧，与脉动同机理）。
         * 周期 1200ms = 24 × 50ms 整除 → 每 tick 恰 +15°。
         */
        private const val SPIN_TICK_MS = 50L
        private const val SPIN_PERIOD_MS = 1_200L
        private const val SPIN_SWEEP_DEG = 270f
        private const val SENT_TTL_MS = 800L
        private const val FEEDBACK_TTL_MS = 1500L

        /**
         * 脉动相位（纯函数，可直测）：三角波 + smoothstep，返回 0..1（0 = 白，1 = 头像紫）。
         *
         * 整周期 = 2×[rampMs]；[enabled]=false（系统动画缩放=0）恒为 0（静态白）。
         * 纯函数的意义：低帧率下"采样点是否落在均匀步进上"只能靠数学断言——
         * 1fps（tick=1000）采 ramp=2000 时，四步恰为 0 → .5 → 1 → .5 → 0。
         */
        internal fun pulseFraction(nowMs: Long, rampMs: Long, enabled: Boolean): Float {
            if (!enabled || rampMs <= 0) return 0f
            val period = rampMs * 2
            val t = ((nowMs % period) + period) % period
            val raw = if (t < rampMs) t.toFloat() / rampMs else (period - t).toFloat() / rampMs
            return raw * raw * (3f - 2f * raw)
        }

        internal fun snapSide(centerX: Int, screenWidth: Int): Side =
            if (centerX <= screenWidth / 2) Side.LEFT else Side.RIGHT

        internal fun snapX(side: Side, screenWidth: Int, bubbleWidth: Int, edgeMargin: Int): Int =
            if (side == Side.LEFT) edgeMargin
            else (screenWidth - bubbleWidth - edgeMargin).coerceAtLeast(edgeMargin)

        internal fun clampY(
            y: Int,
            screenHeight: Int,
            bubbleHeight: Int,
            topMargin: Int,
            bottomMargin: Int,
        ): Int {
            val maxY = (screenHeight - bubbleHeight - bottomMargin).coerceAtLeast(topMargin)
            return y.coerceIn(topMargin, maxY)
        }

        internal fun isTap(dx: Int, dy: Int, slop: Int): Boolean =
            abs(dx) <= slop && abs(dy) <= slop

        /**
         * v4 等待弧的旋转相位（纯函数，可直测）：三角波之外的**线性** 0..360，
         * 顺时针（与 `Canvas.drawArc` 的角度约定一致：0° = 3 点钟方向）。
         *
         * [SPIN_PERIOD_MS] 取 [SPIN_TICK_MS] 的整数倍（24×50ms）→ 每 tick 恰 +15°，
         * 低帧率下步进均匀（同 [pulseFraction] 对采样均匀性的要求）。
         */
        internal fun spinAngleDeg(nowMs: Long, periodMs: Long = SPIN_PERIOD_MS): Float {
            if (periodMs <= 0) return 0f
            val t = ((nowMs % periodMs) + periodMs) % periodMs
            return t.toFloat() / periodMs * 360f
        }

        /** 提示文案（§5.2）；SENT 不再提 5s——帧随指令被服务端立即消费（v3 §4.3 D8）。 */
        internal fun pillTextRes(feedback: CaptureFeedback): Int? = when (feedback) {
            CaptureFeedback.SENT -> R.string.cap_bubble_sent
            CaptureFeedback.REJECTED_FOREGROUND -> R.string.cap_bubble_foreground
            CaptureFeedback.REJECTED_LOCKED -> R.string.cap_bubble_locked
            CaptureFeedback.FAILED_NO_FRAME -> R.string.cap_bubble_failed
        }
    }
}
