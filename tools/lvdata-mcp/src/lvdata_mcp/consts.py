"""事件常量（§5.1 单一出处）：同 ts_ms 时的稳定排序（分页游标依赖）。

2026-09-15 重排（recording_data_process.md §2.5）：新增 transcribe/translate
（chat 三文件独立事件类型），文本类靠前。**排序 SQL 一律由 EVENT_PRIORITY
生成**（PRIORITY_SQL），禁止手写副本——重排时漏改 CASE 会导致游标错乱。
bff 有独立副本（services/bff/common/store.py），重排时须同步。
"""

EVENT_PRIORITY = {
    "chat": 0,
    "transcribe": 1,
    "translate": 2,
    "window": 3,
    "video": 4,
    "audio": 5,
    "agent_job": 6,
}
EVENT_TYPES = tuple(EVENT_PRIORITY.keys())

# 未知类型排序值（排最后）；与 PRIORITY_SQL 的 ELSE 同源
PRIORITY_FALLBACK = len(EVENT_PRIORITY)

# SQL 排序表达式（store.get_timeline 游标 WHERE 与 ORDER BY 共用）
PRIORITY_SQL = (
    "CASE type "
    + " ".join(f"WHEN '{t}' THEN {p}" for t, p in EVENT_PRIORITY.items())
    + f" ELSE {PRIORITY_FALLBACK} END"
)

# ── day_stats 派生常量（docs/bff_service_interfaces.md §7.2.2）────────────────
# 2026-09-25 自 services/bff/timeline/service.py 平移（BFF 侧随 §7 删除）——
# 口径必须逐字一致，改动即破坏 wire 契约（Android 按同规则重建，
# docs/android_timeline_image_gallery.md §3.2 不变量）。

# hotSlots：事件间隔 > SPLIT_GAP_MS 切段；段数 > MAX_HOT_SLOTS 时反复合并
# 「相邻间隔最小」的一对（并列取最左）直至 ≤3
SPLIT_GAP_MS = 60 * 60 * 1000
MAX_HOT_SLOTS = 3

# hotSlots 分段输入 = hotSlotView 可展示类型；audio/window 无展示形态，不参与分段
HOT_SLOT_TYPES = frozenset({"chat", "agent_job", "video", "transcribe", "translate"})

# images：日候选池 = 各粒度 cap 的上界（Android 展示 cap day6/week9/month12/year15）；
# 池 ≥ 最大 cap 是「任意粒度可由单日喂满」与 Android 端重建等价性的共同前提
DAY_IMAGE_POOL = 15
