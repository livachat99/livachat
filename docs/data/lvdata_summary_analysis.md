# LvData Summary & Alignment Tool Design

> **Phase 4-5 多模态对齐与跨 Session 报告** — 消费 Phase 1-3 的衍生数据（音频分析、图像分析、聊天记录），按时间戳对齐重建完整用户旅程，生成 session 级摘要与跨 session 综合报告（用户行为模式 + AI 表现评估），输出 Markdown / HTML 双格式报告。

---

## 1. Overview

`lvdata_summary.py` 是 Phase 4-5 的 Python CLI 工具，也是分析管线的**最后一环**。它只读 Phase 1-3 的产物（`derived/` 下 JSON + inventory DB），不做任何 AI 采集工作，按三个阶段运行：

| 阶段 | 内容 | 对应计划 | 是否需要 LLM |
|------|------|---------|-------------|
| `align` | 多模态对齐：音频片段 + 视频关键帧 + 聊天消息合并为统一时间线 | Phase 4 Task 4.1 | 否（纯本地确定性计算） |
| `summarize` | 每 session 生成结构化摘要 + Markdown 渲染 | Phase 4 Task 4.2 | 是（文本-only 一次调用/session） |
| `report` | 跨 session 洞察：用户行为模式 + AI 表现评估，输出综合报告 | Phase 5 Task 5.1/5.2 + Phase 6 Task 6.1 | 是（文本-only 一次调用 + 本地渲染） |

### 设计原则

- **不重复造数据** — 对齐阶段 100% 消费已有衍生 JSON 与 DB，零模型调用、零费用；音频 `summary.json` 的 `timeline` 与视频 `video_summary.json` 的 `scene_timeline` 在此合并（Phase 3 设计文档 Open Question #7 的答案）
- **单一事实来源** — 对齐产物 `timeline.json` 落盘到 `derived/`，与源数据同 root；后续报告全部从它消费，不再碰源文件
- **LLM 只做叙事** — 统计（时长、计数、分布）全部本地计算；LLM 只负责语义层：话题、行为归纳、表现评估
- **可离线** — `--no-llm` 模式下对齐 + 统计摘要完全本地完成，仅缺 AI 叙事字段
- **幂等可恢复** — 摘要按 `multi_summary_status` 跳过已完成；报告按产物新旧判断；`--force` 重跑
- **容错优先** — 某 session 缺某类衍生数据（如无音频/无视频）不中断，只对该来源降级

---

## 2. 目录结构与衍生数据布局

### 2.1 输入（Phase 1-3 产物，只读）

```
<root_dir>/                          # 默认 /opt/lvdata
  <device_uuid>/
    <session_id>/
      session.json                  # Phase 1 源元数据
      derived/                      # 若 --root-dir 下已存在 derived 则在此；见 §2.2
  derived/                          # ← 实际衍生根目录（与源数据同 root）
    <device_uuid>/
      <session_id>/
        summary.json                # Phase 2 音频聚合（含 timeline 骨架）
        audio/<segment_id>.json     # Phase 2 片段级结果
        video_summary.json          # Phase 3 图像聚合（含 scene_timeline）
        video/windows/<window_id>.json   # Phase 3 窗口筛选
        video/frames/<frame_id>.json     # Phase 3 关键帧提取
```

聊天消息不读文件，直接查 inventory DB 的 `chat_messages` 表（Phase 1 已解析入库）。

### 2.2 输出（Phase 4-5 产出，只写）

```
<root_dir>/derived/
  <device_uuid>/
    <session_id>/
      timeline.json                 # align 产出：统一时间线（本工具主产物）
      session_summary.json          # summarize 产出：结构化摘要（含 LLM 叙事字段）
      session_summary.md            # 摘要的 Markdown 渲染（Task 4.2 模板）
  _reports/                         # report 产出（跨 session 全局）
    report.json                     # 结构化跨 session 洞察
    report.md                       # Markdown 综合报告
    report.html                     # HTML 综合报告（单文件自包含）
```

- `timeline.json` 携带统一事件 schema（§2.3），摘要与报告全部从它消费
- `session_summary.md` 按原计划 Task 4.2 的摘要模板渲染，可直接交付阅读

### 2.3 timeline.json Schema（align 主产出）

```json
{
  "schema_version": 1,
  "session_id": "019f92f2-5b73-7886-8000-576e09d3f328",
  "device_uuid": "b53985c7-cb7f-4382-95c9-18d33a9bc36a",
  "session_date": "2026-07-24",
  "built_at": "2026-08-03T12:00:00+00:00",
  "builder": "lvdata_summary.py",
  "timezone": "Asia/Shanghai",

  "started_at": "2026-07-24T07:06:20Z",
  "ended_at": "2026-07-24T08:14:05Z",
  "time_span_ms": 4065000,

  "sources": {
    "chat_messages": 8,
    "audio_segments": 6,
    "audio_empty": 1,
    "video_frames_total": 7,
    "video_key_frames": 5,
    "video_windows": 1
  },

  "events": [
    {"ts_ms": 1784876784813, "ts_utc": "2026-07-24T07:06:24.813Z",
     "type": "chat", "role": "user", "text": "Hello, 你好。", "chat_session_id": "..."},
    {"ts_ms": 1784876789900, "ts_utc": "2026-07-24T07:06:29.900Z",
     "type": "audio", "segment_id": "019f92f2-...", "transcript": "你好我想问一个问题",
     "summary": "用户提出一个问题", "duration_s": 60.0, "empty": false,
     "emotions": [{"emotion": "neutral", "confidence": 0.7}]},
    {"ts_ms": 1784876828272, "ts_utc": "2026-07-24T07:07:08.272Z",
     "type": "chat", "role": "assistant", "text": "Hi there!"},
    {"ts_ms": 1784876830000, "ts_utc": "2026-07-24T07:07:10.000Z",
     "type": "window", "window_id": "019f92f2-...", "caption": "Chrome 浏览器打开 GitHub 页面",
     "frame_count": 7, "key_frame_count": 3, "static": false},
    {"ts_ms": 1784876831000, "ts_utc": "2026-07-24T07:07:11.000Z",
     "type": "video", "frame_id": "019f92f2-...", "scene": "browser",
     "caption": "GitHub 仓库页面，窗口最大化",
     "apps": [{"name": "Google Chrome", "window_title": "anthropics/claude-code", "active": true}],
     "ocr": [{"text": "README.md", "location": "主内容区"}],
     "actions": ["浏览 GitHub 仓库页面"]}
  ]
}
```

**事件类型与字段：**

| type | 来源 | 关键字段 |
|------|------|---------|
| `chat` | DB `chat_messages` | `role`, `text`, `chat_session_id` |
| `audio` | `summary.json` timeline + `audio/<seg>.json` | `segment_id`, `transcript`, `summary`, `duration_s`, `emotions`, `empty` |
| `window` | `video/windows/<win>.json` | `window_id`, `caption`, `frame_count`, `key_frame_count`, `static` |
| `video` | `video/frames/<frame>.json` | `frame_id`, `scene`, `caption`, `apps`, `ocr`, `actions` |

**对齐规则（§6.2 详述）：**

1. 视频非关键帧**不进 timeline**（Phase 3 已判定为重复帧，全量信息仍在 `windows/*.json`）
2. 每个视频窗口在窗口首帧时间点生成一个 `window` 事件（窗口级上下文）；`static: true` 表示整窗无关键帧（静态桌面）
3. 音频片段 transcript 为空（静音/不可识别）保留事件，`empty: true`，供摘要阶段识别"沉默期"
4. 事件按 `(ts_ms, 类型优先级)` 排序，优先级：`chat < window < video < audio`（同毫秒时稳定有序）

### 2.4 session_summary.json Schema（summarize 产出）

```json
{
  "schema_version": 1,
  "session_id": "019f92f2-5b73-7886-8000-576e09d3f328",
  "session_date": "2026-07-24",
  "timezone": "Asia/Shanghai",
  "model": "qwen3.7-flash",
  "generated_at": "2026-08-03T12:05:00+00:00",
  "no_llm": false,

  "stats": {
    "duration_min": 68,
    "local_span": "15:06 - 16:14",
    "chat_messages": 8,
    "user_messages": 5,
    "audio_segments": 6,
    "audio_seconds": 360,
    "video_key_frames": 5,
    "silent_periods": [{"ts_ms": 1784876900000, "duration_s": 45}]
  },

  "language_mix": "中文为主，中英混用",
  "session_summary": "用户与 AI 进行了问候与故事创作对话，期间分享桌面截图请 AI 分析环境。",
  "user_behavior_timeline": [
    {"time": "07:06", "activity": "问候、聊天测试、讲故事请求"},
    {"time": "07:07", "activity": "抱怨响应慢"},
    {"time": "07:11", "activity": "第二轮互动：讲故事 + 图片分析（桌面照片）"}
  ],
  "topics": [
    {"topic": "AI 性能反馈", "mentions": 2},
    {"topic": "故事创作需求", "mentions": 1}
  ],
  "screen_activity": [
    {"time": "07:07", "activity": "浏览 GitHub 仓库页面"},
    {"time": "07:11", "activity": "打开 IDE 开始编辑代码"}
  ],
  "notable_events": [
    {"time": "07:07", "event": "用户明确表达对响应速度不满"}
  ]
}
```

- `stats` 全部本地计算（LLM 不参与，保证数字准确）；`local_span` 与全部时间展示按 `--timezone`（默认 `Asia/Shanghai`，UTC+8）换算本地时间
- `language_mix` / `session_summary` / `user_behavior_timeline` / `topics` / `screen_activity` / `notable_events` 为 LLM 叙事字段；`--no-llm` 时仅保留 `stats` 与空数组字段
- `session_summary.md` 即按此 JSON 渲染（模板见 §6.4）

### 2.5 report.json Schema（report 产出）

```json
{
  "schema_version": 1,
  "report_date": "2026-08-03T12:10:00+00:00",
  "root_dir": "/opt/lvdata",
  "timezone": "Asia/Shanghai",
  "model": "qwen3.7-flash",
  "sessions_analyzed": 3,

  "aggregate_stats": {
    "total_sessions": 3,
    "total_duration_min": 128,
    "total_chat_messages": 25,
    "total_audio_segments": 59,
    "total_video_key_frames": 6,
    "active_days": ["2026-07-24", "2026-07-28", "2026-07-29"]
  },

  "usage_patterns": {
    "active_hours": "UTC 7:00-15:00（北京时间 15:00-23:00）",
    "frequency": "7/24、7/28、7/29 — 间隔 3-4 天",
    "language_mix": "中英混用，中文为主",
    "session_duration": "单次 20-70 分钟，短交互为主",
    "core_needs": [
      {"need": "故事创作/内容生成", "evidence": "7/24 请求讲故事"},
      {"need": "信息查询", "evidence": "天气、财经、民族数据"}
    ],
    "fail_patterns": [
      {"pattern": "AI 无法提供实时信息", "sessions": ["7/24", "7/28"], "note": "天气/新闻类请求重复出现"}
    ]
  },

  "ai_performance": {
    "response_speed": {"rating": "below_expectation", "evidence": "7/24 用户抱怨响应慢"},
    "information_accuracy": {"rating": "good", "evidence": "民族数据来自 2020 年普查，引用合理"},
    "capability_boundary": {"rating": "honest", "evidence": "对实时信息无法获取时诚实回应"},
    "multilingual": {"rating": "good", "evidence": "中英文切换流畅"},
    "creativity": {"rating": "good", "evidence": "故事创作有温度、有寓意"}
  },

  "insights": ["...", "..."],
  "recommendations": ["...", "..."]
}
```

- `aggregate_stats` 本地计算；其余为 LLM 叙事字段（输入为各 session 的 `session_summary.json` 压缩文本）
- `report.md` / `report.html` 由此 JSON 渲染，两格式内容一致

---

## 3. 数据模型（inventory DB 扩展）

沿用 Phase 1 的 SQLite DB（`--db-path` 默认 `/opt/lvdata/lvdata_inventory.db`）。与 Phase 2/3 相同的 `_ensure_column` 增量模式，不动既有表结构。

### 3.1 sessions 表增量列

| 列 | 说明 |
|----|------|
| `multi_summary_status` | `pending / ok / failed` — 本工具的 session 摘要状态（⚠️ 不用 `summary_status`：**该列已被 Phase 2 音频工具占用**，用于其 session 级音频汇总状态；与 `analysis_status`、`video_status`、`video_summary_status` 互不干扰） |
| `multi_summary_path` | session_summary.json 绝对路径（ok 后写入） |

### 3.2 新增表 summary_runs（跟踪报告/对齐运行历史）

```sql
CREATE TABLE IF NOT EXISTS summary_runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    root_dir          TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    stages            TEXT NOT NULL,             -- 逗号分隔: align|summarize|report
    sessions_analyzed INTEGER DEFAULT 0,
    report_path       TEXT,                      -- 若本次生成了报告
    status            TEXT DEFAULT 'running'     -- running | completed | failed
);
```

对齐（align）阶段为确定性计算，不设状态列——每次运行按需重建 `timeline.json`。

### 3.3 待摘要 session 查询

```sql
-- summarize 阶段：所有未成功摘要的 session
SELECT s.session_id, s.device_uuid, s.session_date,
       s.multi_summary_status, s.root_path
FROM sessions s
WHERE s.status IN ('active', 'finalized')
  AND s.multi_summary_status != 'ok'
ORDER BY s.session_date;

-- report 阶段前置检查：已摘要 session 数
SELECT COUNT(*) FROM sessions WHERE multi_summary_status = 'ok';
```

---

## 4. CLI Interface

```
lvdata_summary.py [OPTIONS]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--root-dir` | PATH | `/opt/lvdata` | lvdata 根目录（衍生目录 `derived/` 在其下） |
| `--db-path` | PATH | `/opt/lvdata/lvdata_inventory.db` | 与 Phase 1-3 相同的索引库 |
| `--device-uuid` | UUID | (none) | 仅处理指定设备 |
| `--session-id` | UUID | (none) | 仅处理指定 session（可重复传入） |
| `--stage` | STR | `all` | `all / align / summarize / report` |
| `--model` | STR | `qwen3.7-flash` | 百炼模型 ID（文本-only，flash 足够） |
| `--base-url` | URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `--api-key` | STR | env `DASHSCOPE_API_KEY` | 百炼 API Key |
| `--max-chars-per-event` | INT | `300` | 送入 LLM 前每个时间线事件的最大字符数（token 预算控制，§6.5） |
| `--timezone` | STR | `Asia/Shanghai` | 摘要与报告的展示时区（IANA 名或固定偏移如 `+08:00` / `8` / `UTC`）；默认 CST（UTC+8）。时间线内部仍存 UTC epoch ms，仅在展示/送入 LLM 时换算 |
| `--no-llm` | flag | false | 离线模式：跳过全部 API 调用，仅本地统计与渲染 |
| `--force` | flag | false | 忽略 multi_summary_status / 产物新旧判断，全量重跑 |
| `--dry-run` | flag | false | 只列出将处理的 session 与阶段，不发请求、不写文件 |
| `--verbose` / `-v` | flag | false | 详细输出 |

### 使用示例

```bash
export DASHSCOPE_API_KEY=sk-...

# 全量：对齐 → 摘要 → 报告
python lvdata_summary.py

# 先只对齐（零成本，可先跑）
python lvdata_summary.py --stage align

# 仅对某个 session 做摘要
python lvdata_summary.py --stage summarize --session-id 019f92f2-5b73-7886-8000-576e09d3f328

# 摘要完成后单独出报告
python lvdata_summary.py --stage report

# 离线模式（无 API Key 也能跑：对齐 + 统计摘要 + 统计版报告）
python lvdata_summary.py --no-llm

# 指定展示时区（默认 Asia/Shanghai = CST, UTC+8；支持 IANA 名或 +08:00 / 8 / UTC）
python lvdata_summary.py --timezone Asia/Shanghai
python lvdata_summary.py --timezone UTC

# 先看将处理什么
python lvdata_summary.py --dry-run -v

# 改了摘要 prompt 后强制重跑摘要与报告
python lvdata_summary.py --stage summarize --force && python lvdata_summary.py --stage report --force
```

---

## 5. 模型调用设计（百炼 Qwen3.7-Flash，文本-only）

### 5.1 要点

| 结论 | 说明 |
|------|------|
| 调用形式 | 与 Phase 2/3 完全一致的 OpenAI 兼容接口（`AsyncOpenAI` + `response_format={"type": "json_object"}`），仅文本消息，**无多模态输入** |
| 模型选择 | `qwen3.7-flash`（Phase 3 已验证结构化输出；文本任务成本可忽略）。可用 `--model` 切换 |
| 围栏残留 | 沿用 Phase 2/3 验证过的 `sanitize()`：解析前剥离首尾 markdown 围栏，`null` 归一化为空串/空数组 |
| 思考模式 | 文本任务不需要关闭思考，但为与 Phase 3 行为一致、保证输出稳定性，同样传 `extra_body={"enable_thinking": False}` |
| 幂等重试 | `429 / 5xx` 指数退避 + 抖动（1s, 2s, 4s, 8s），最多 4 次；单次超时 300s |

### 5.2 调用 1：session 摘要（每 session 一次）

**输入**：压缩后的时间线文本（§6.5 生成），事件时间已换算为 `--timezone` 本地时间，并在头部标注（形如"时间均为 Asia/Shanghai 本地时间"），形如：

```
会话 2026-07-24，时长约 68 分钟。事件序列（按时间顺序，时间均为 Asia/Shanghai 本地时间）：
[15:06:24] (chat/user) Hello, 你好。
[07:06:29] (audio) 你好我想问一个问题 | 总结：用户提出一个问题 | 情绪：neutral
[07:07:08] (chat/assistant) Hi there!
[07:07:10] (window) Chrome 浏览器打开 GitHub 页面 | 关键帧 3/7
[07:07:11] (video) browser | GitHub 仓库页面 | 动作：浏览仓库
...
统计：chat 8 条，音频 6 段（含 1 段静音），关键帧 5 张
```

**System Prompt**（JSON Schema 内嵌，要求只输出 JSON）——输出 §2.4 的 `language_mix / session_summary / user_behavior_timeline / topics / screen_activity / notable_events` 字段；明确要求 `stats` 与时间信息直接引用输入，不重新计算、不编造。

### 5.3 调用 2：跨 session 洞察（全局一次）

**输入**：各 session 的 `session_summary.json`（压缩：去掉 stats 冗余，保留叙事字段）+ `aggregate_stats`（本地计算）拼接文本。

**System Prompt**——输出 §2.5 的 `usage_patterns / ai_performance / insights / recommendations`；要求每条结论附 `evidence`（引用具体 session 日期），无证据不下结论。

### 5.4 实测验证计划（probe_qwen_text.py）

仿照 Phase 2/3 先例，实现 `tools/data-analysis/probe_qwen_text.py`，在实现主工具前验证：

| 测试 | 验证点 |
|------|--------|
| 文本 + `json_object`（模拟压缩时间线输入） | 结构化输出可用性、围栏残留、schema 遵从度 |
| 长输入（3 个 session 摘要拼接） | 跨 session 归纳能力、evidence 引用质量 |
| `enable_thinking=False` | 参数形态（复用 Phase 3 结论即可，作为回归验证） |

---

## 6. Processing Logic

### 6.1 主流程

```
main()
  ├── parse_args()
  ├── connect_db(); ensure_schema()          # summary_runs + sessions 增量列
  ├── select_sessions()                      # 按 --device-uuid / --session-id 过滤
  │     └── --dry-run: 列出 session 与各阶段将做的事后退出
  ├── 若 stage ∈ {all, align}:
  │     for each session:
  │           build_timeline()               # §6.2，写 timeline.json
  ├── 若 stage ∈ {all, summarize}:
  │     for each session（multi_summary_status='ok' 且未 --force 则跳过）:
  │           ├── read timeline.json
  │           ├── if --no-llm: local_stats_only()     # 仅 stats
  │           ├── else: call_session_summary()        # §5.2，失败重试，仍失败 → failed
  │           ├── write session_summary.json（原子写）
  │           ├── render_session_md()                 # §6.4 → session_summary.md
  │           └── update multi_summary_status / multi_summary_path
  ├── 若 stage ∈ {all, report}:
  │     ├── 前置检查：所有选中 session 的 multi_summary_status='ok'
  │     │     （--no-llm 时无要求；缺失则 warning 并提示先跑 summarize）
  │     ├── compute_aggregate_stats()         # 本地
  │     ├── if --no-llm: 仅生成统计版报告 JSON
  │     ├── else: call_cross_session()        # §5.3 → report.json
  │     ├── render_report_md() + render_report_html()
  │     └── update summary_runs
  └── print_summary()                         # 每阶段处理数/跳过数/失败清单/token 用量
```

### 6.2 对齐算法（build_timeline）

```python
def build_timeline(session_id, db, derived_dir):
    events = []
    # 1) 聊天消息（DB）
    for row in db.execute(
            "SELECT ts_ms, role, text_content, chat_session_id "
            "FROM chat_messages WHERE session_id = ?", [session_id]):
        events.append({**row, "type": "chat"})

    # 2) 音频片段（derived/.../audio/<seg>.json；从 summary.json 拿 segment 列表）
    for seg_json in load_audio_segments(derived_dir):
        events.append({
            "ts_ms": seg_json["ts_ms"], "type": "audio",
            "segment_id": seg_json["segment_id"],
            "transcript": seg_json.get("transcript", ""),
            "summary": seg_json.get("summary", ""),
            "duration_s": seg_json.get("duration_s"),
            "emotions": seg_json.get("emotions", []),
            "empty": not seg_json.get("transcript", ""),
        })

    # 3) 视频窗口（window 事件，时间点 = 窗口首帧）
    for win_json in load_video_windows(derived_dir):
        events.append({
            "ts_ms": win_json["window_start_ms"], "type": "window",
            "window_id": win_json["window_id"],
            "caption": win_json.get("window_caption", ""),
            "frame_count": win_json["frame_count"],
            "key_frame_count": len([f for f in win_json["frames"] if f["is_key_frame"]]),
            "static": not any(f["is_key_frame"] for f in win_json["frames"]),
        })

    # 4) 关键帧（仅 is_key_frame=true，非关键帧不进 timeline）
    for frame_json in load_key_frames(derived_dir):
        events.append({**frame_json, "type": "video"})   # 含 ts_ms/scene/caption/apps/ocr/actions

    # 排序：ts_ms 升序，同毫秒按 chat < window < video < audio
    PRIORITY = {"chat": 0, "window": 1, "video": 2, "audio": 3}
    events.sort(key=lambda e: (e["ts_ms"], PRIORITY[e["type"]]))
    return events
```

容错规则：

- 某来源缺失（如该 session 无 `summary.json`、无 `video/` 目录、DB 无 chat 行）→ 该来源跳过，`sources` 计数为 0，不报错
- `derived/` 目录缺失 → 视为无任何衍生数据，只对齐 chat（若 chat 也无则整 session 跳过并 warning）
- 单个 JSON 损坏 → 跳过该文件，warning 计数
- 时间跨度为 0（单事件）也正常输出

### 6.3 幂等与恢复

| 阶段 | 跳过条件 | `--force` 行为 |
|------|---------|---------------|
| align | 无（每次按需重建，本地零成本） | — |
| summarize | `multi_summary_status='ok'` | 删除状态行与产物，重新调用 |
| report | `report.json` 存在 且 无任一 `session_summary.json` 比它新 | 无视新旧，重新调用 |

- 单 session 摘要失败（重试后仍失败）→ 标 `failed` 记 error，继续其他 session；结尾汇总失败清单
- report 阶段若存在 failed 的 session：warning 列出，仍按其余 session 生成（`aggregate_stats` 如实标注）

### 6.4 Markdown 渲染

**session_summary.md**（Task 4.2 模板对齐）：

```markdown
## Session: 2026-07-24 (7/24)
- Duration: 07:06 - 08:14 UTC (约 68 分钟)
- Audio: 6 segments (~6 分钟用户语音)
- Video: 5 key frames / 7 total (桌面截图)
- Chat: 8 messages (user 5 / assistant 3)

### 用户行为时间线
1. 07:06 - 问候、聊天测试、讲故事请求
2. 07:07 - 抱怨响应慢
3. 07:11 - 第二轮互动：讲故事 + 图片分析

### 主要话题
- AI 性能反馈 (×2)
- 故事创作需求 (×1)

### 屏幕活动
- 07:07 - 浏览 GitHub 仓库页面
- 07:11 - 打开 IDE 开始编辑代码

### 值得注意的事件
- 07:07 - 用户明确表达对响应速度不满
```

**report.md**：章节 = 总体概况（aggregate_stats 表格）/ 用户行为模式（usage_patterns）/ AI 表现评估（ai_performance）/ 洞察与建议（insights + recommendations）。

**report.html**：单文件自包含（内联 CSS，无外部资源、无 JS 框架），渲染与 Markdown 相同的内容，表格 + 分节卡片；纯 Python 字符串拼接生成，不引入模板引擎。

### 6.5 LLM 输入压缩（token 预算）

- 每事件 → 一行 `[HH:MM:SS] (type/role) 内容摘要`，按 `--max-chars-per-event`（默认 300）截断；`HH:MM:SS` 由事件 UTC epoch ms 按 `--timezone` 换算（`resolve_timezone()` 支持 IANA 名与固定偏移）
- 音频行取 `transcript`（截断）+ 片段 `summary`；视频行取 `scene + caption`；window 行取 caption + 关键帧占比
- 压缩后单 session 输入通常 < 3k tokens（当前数据 8 条 chat + 6 段音频 + 6 事件），远低于 flash 1M 上下文
- 跨 session 调用输入 = 各 `session_summary.json` 叙事字段拼接 + aggregate_stats，< 5k tokens

### 6.6 测试计划（tests/test_lvdata_summary.py）

| 用例 | 验证点 |
|------|--------|
| 对齐合并 | 四类事件按 ts 排序、同毫秒优先级、非关键帧排除、空 transcript 标记 `empty` |
| 来源容错 | 缺音频/缺视频/derived 缺失时的降级行为 |
| 压缩 | 截断长度边界、空事件处理 |
| 渲染 | session_summary.md / report.md / report.html 关键章节存在（快照对比） |
| 幂等 | summarize 跳过 ok、report 新旧判断 |
| no-llm 模式 | 无 API Key 下全流程可跑、LLM 字段为空 |

---

## 7. File Layout

```
tools/data-analysis/
  lvdata_inventory.py          # Phase 1: 索引（已完成）
  lvdata_analyze_audio.py      # Phase 2: 音频分析（已完成）
  lvdata_analyze_video.py      # Phase 3: 图像分析（已完成）
  lvdata_summary.py            # Phase 4-5: 本设计，待实现
  probe_qwen_text.py           # 探针：文本结构化输出验证（§5.4）
  probe_qwen_image.py          # Phase 3 探针（已有）
  probe_qwen_structured_output.py  # Phase 2 探针（已有）
  tests/
    test_lvdata_analyze_audio.py
    test_lvdata_analyze_video.py
    test_lvdata_summary.py     # 新增（§6.6）
```

与 Phase 1-3 共享 `--root-dir` / `--db-path` 默认值，天然衔接；全管线一条命令串起：

```bash
python lvdata_inventory.py && \
python lvdata_analyze_audio.py && \
python lvdata_analyze_video.py && \
python lvdata_summary.py
```

---

## 8. 成本估算

| 项 | 输入 tokens | 输出 tokens | 单价（flash <32K 档） | 成本 |
|----|------------|------------|----------------------|------|
| session 摘要 ×3 | ~3k × 3 | ~0.6k × 3 | 输入 ≈ ¥0.22/M、输出 ≈ ¥0.94/M | ≈ ¥0.003 |
| 跨 session 报告 ×1 | ~5k | ~1.5k | 同上 | ≈ ¥0.002 |
| **合计** | ~14k | ~3.3k | | **< ¥0.01** |

align 阶段零调用。`--no-llm` 模式完全零成本。usage 落库（沿用 `usage_input/output_tokens` 字段到 summary_runs 或日志）供精确核算。

---

## 9. 已知限制与 Open Questions

1. **timeline 不含视频非关键帧** — 非关键帧被 Phase 3 判定为重复帧，对齐时剔除。如需完整帧序列查看，可加 `--include-all-frames`（事件表会膨胀，当前不默认）。全量帧信息始终保留在 `video/windows/*.json`。

2. **对齐粒度 = 60s 音频段 + 关键帧级** — 与 Phase 2/3 产出粒度一致；词级/秒级对齐需引入带词级时间戳的 ASR（Phase 2 设计 Open Question #1），v1 不做。

3. **报告仅统计"已摘要"的 session** — `--stage report` 前置检查要求全部选中 session `multi_summary_status='ok'`；新数据进入后需先跑 `summarize` 再 `report`（命令已串联）。

4. **隐私合规** — 摘要与报告生成会将**转写文本与截图描述**上传至阿里云（文本-only）。与 Phase 2/3 的隐私约定一致；`--no-llm` 提供完全离线路径。

5. **LLM 叙事质量依赖衍生数据质量** — 若 Phase 2/3 未跑（如 019fa7d3 无视频），对应维度在摘要中会缺省。工具如实标注缺失来源，不编造。

6. **report.html 无交互** — v1 为静态自包含页面。若需要过滤/跳转等交互，后续可引入轻量 JS 或模板引擎（超出当前范围）。

7. **跨 root 合并** — report 按单个 `--root-dir` 生成；多设备/多 root 合并报告不在 v1（`--device-uuid` 可做单设备报告）。

8. **多语言输出** — 摘要与报告语言跟随输入内容（当前为中文）。是否加 `--lang` 参数控制输出语言，pilot 后按需定。

---

## 10. Dependencies

```python
# Python
openai>=1.52.0        # 仅 summarize/report 阶段需要；--no-llm 或 --stage align 可不装
# 其余 stdlib: sqlite3, json, argparse, asyncio, os, html
# 系统: 无（纯文本处理，不依赖 ffmpeg / Pillow）
```

---

## 11. 调研参考

- Phase 1 数据模型与 UUIDv7 约定：`docs/data/lvdata_inventory.md`
- Phase 2 音频衍生 schema 与调用范式：`docs/data/lvdata_audio_analysis.md`
- Phase 3 图像衍生 schema（scene_timeline 合并来源）：`docs/data/lvdata_video_analysis.md`
- 计划文档（Task 4.1/4.2/5.1/5.2/6.1）：`docs/data/lvdata-analysis-plan.md`

---

*Design version: 1.0 | Created: 2026-08-03*
