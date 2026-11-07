# LvData Image (Video Frames) Analysis Tool Design

> **Phase 3 图像分析** — 基于阿里云百炼 Qwen3.7-Flash 视觉语言模型（VLM），对 lvdata 中的 JPEG 屏幕截图序列进行窗口化批量分析：判定模糊（blur）、重复（dup）、画质（quality）与关键帧（key frame），并对关键帧深度提取 scene / caption / object / events / OCR / people / action 等结构化信息。所有 AI 生成的衍生数据统一落盘到 `<root_dir>/derived/<device_uuid>/<session_id>/video/...`。

> **修订裁定（2026-09-18，视频 filter schema 精简）**：本文描述的
> `lvdata_analyze_video.py` 为 v1 CLI（已被 lvdata-mcp 取代、不再更新），
> **filter prompt 与帧 schema 为 v1 存量**。运行时 filter 已按
> [video_analysis.md](../video_analysis.md) 调整：帧键收敛为
> `frame_id / quality / duplicate_of / value / reason`（删 blur / quality_issues /
> novelty / is_key_frame / key_rank）；**关键帧改为派生量**（`value=high` 且
> 非重复帧）；**无派生关键帧的窗口不产事件**；术语"屏幕截图"→"图片"；bff 日卡
> 选图改为 **window 去重**（V7）。对照本文档了解当前行为时请以该文档为准。

> **修订裁定（2026-09-18 第二轮，视频 extract schema 精简 + place）**：运行时
> extract（lvdata-mcp）字段收敛为 `caption / place / objects / ocr / people`
> （删 `scene / apps / events / actions`）；新增 `place`（根据图片推测地点，
> 不确定或是屏幕截图 → null）。本文 §2.4 的 8 字段 schema 与 §2.5
> `scene_timeline`/`apps_used` 汇总口径均为 **v1 存量**。详见
> [video_analysis.md](../video_analysis.md) §9。

> **修订裁定（2026-10-07，视频 filter schema 第二轮）**：运行时帧键再换血 →
> `frame_id / quality / duplicated / key_frame / reason`——`duplicate_of`（frame_id
> 引用）改 `duplicated` 布尔（场景重复）；`value` 改 `key_frame`（模型判定：同一
> 场景最多一帧、取画质最高帧；过渡/黑屏场景可以没有；quality=low 的 key 由归一
> 守卫降级）。本文 §2 的 `duplicate_of/value/is_key_frame` 描述为 v1/历史口径。
> 详见 [video_analysis.md](../video_analysis.md) §10。

> **修订裁定（2026-10-07，视频 filter schema 第三轮）**：运行时帧键
> `duplicated` → `scene_id`（整数：同窗同场景同 id、不同场景不同 id）；新增
> 场景级抑制——同窗同 `scene_id` 多 key 只保留帧索引最小者、其余降级并写
> warning log。详见 [video_analysis.md](../video_analysis.md) §11。

---

## 1. Overview

`lvdata_analyze_video.py` 是 Phase 3 的 Python CLI 工具。它读取 Phase 1 `lvdata_inventory.py` 建立的 SQLite 索引，将 session 的视频帧按时间窗口（默认 30s）分组，**每组截图在一次 VLM 调用中整体提交**（多图输入），先做质量判定与关键帧筛选，再对关键帧做深度信息提取，输出结构化 JSON 衍生数据，并回写 inventory DB 的分析状态。

### 为什么选 Qwen3.7-Flash

| 维度 | 说明 |
|------|------|
| 模型 ID | `qwen3.7-flash`（另有版本号 `qwen3.7-flash-2026-07-15`），Qwen3.7 系列轻量高速档 |
| 能力 | 原生视觉语言模型，文本 + 图像 + 视频多模态输入、文本输出；官方明确推荐截图解析、界面识别、GUI 操作、视觉质检场景 |
| 多图输入 | 单次最多 **256 张图片**、单图最高 1600 万像素 —— 30 帧级窗口一次调用绰绰有余 |
| 图片 Token | 公式 `h × w / (32 × 32) + 2`（即 `h×w/1024 + 2`），与 qwen-vl 系列一致，可按分辨率精确预估成本 |
| 上下文 | 1M tokens / 最大输出 64k tokens |
| 结构化输出 | 非思考模式下支持 JSON 结构化输出（`response_format: json_object`） |
| OCR | 通用图片文字提取，满足截图文字内容提取 |
| 成本 | 按量分档计价（<32K 档输入约 ¥0.22/M、输出约 ¥0.94/M），详见 §8.5 |

### 模型选型对比（qwen3.7-flash / qwen3.7-plus / qwen3.8-max）

本工具通过 `--model` 参数切换候选模型。三者视觉输入方式一致（`image_url` base64 直传），成本与效果差异如下：

| 维度 | qwen3.7-flash（默认） | qwen3.7-plus | qwen3.8-max |
|------|----------------------|--------------|-------------|
| 定位 | 轻量高速档，官方明确推荐截图/界面识别场景 | 多模态旗舰，官方建议"先 plus 验证、稳定后切 flash" | 2.4T 参数 MoE 旗舰（Qwen3.8-Max-Preview，2026-07 首发），Vision Arena 全球第二、BabyVision 82.0 |
| 图片输入 | 单次最多 256 张、单图最高 16M 像素 | 单次最多 2048 张、单图最高 16M 像素，图片与文本同价 | 原生多模态（文本/图像/视频/文档），4K 高分辨率解析、多图对比分析 |
| 上下文 | 1M tokens | 1M tokens | 128K 原生（可扩至 1M） |
| 计费方式 | 按量，分档阶梯（<32K / 32K-256K / 256K-1M） | 按量，8 折后输入 ¥1.6/M、输出 ¥6.4/M（≤256K 档） | **Token Plan 订阅制**（个人 39/139/499 元/月，团队 150-1398 元/月）；预览期白天 1 折、夜间约 0.2 折 |
| 当前 30 帧全量成本 | ≈ ¥0.03 | ≈ ¥0.16 | 订阅月费量级（无按量价） |
| 适用 | 默认生产管线 | flash 质量不达标时的兜底；新用户免费 100 万 tokens 可用于验证 | 最高质量诉求（复杂 UI/长文档截图）+ 已有订阅；⚠️ 个人版禁止后端批量调用（§9.9） |

**选型结论：flash 默认 → plus 兜底 → max 按需。** 实测流程：先用 `--probe` 抽查 flash 的筛选/提取质量，不达标切 `--model qwen3.7-plus`（成本仍可忽略，§8.5）；qwen3.8-max 仅在需要最高解析质量且已有 Token Plan 订阅时启用，并需确认批量调用条款。三模型均需 probe 验证 JSON 结构化输出（§5.6）。

### 设计原则

- **VLM-only** — 模糊/重复/画质判定完全交给 VLM，不使用 OpenCV / 感知哈希等传统 CV 手段（用户明确约束）；图片缩放仅作为成本控制手段，不参与判定
- **窗口化批量** — 一组截图（30s/60s 窗口）一次调用，模型在窗口上下文中互相比较完成去重与关键帧选择
- **单一事实来源** — 衍生数据以 `root_dir/derived/` 下的 JSON 文件为准，inventory DB 只存索引与状态
- **幂等可恢复** — 筛选/提取/汇总三个阶段各自可独立重跑，中断后重跑只处理剩余部分
- **容错优先** — 单窗口/单帧失败不中断整体，记录错误后继续
- **费用可控** — 两步策略让关键帧提取只读少量帧；默认对图片缩放控 token；usage 全部落库可精确核算

---

## 2. 目录结构与衍生数据布局

### 2.1 源数据目录（Phase 1 约定，只读）

```
<root_dir>/                          # 默认 /opt/lvdata
  <device_uuid>/
    <session_id>/
      session.json
      audio/<uuidv7>.ogg
      video/<uuidv7>.jpg            # JPEG 屏幕截图（实测 ~1 fps 采集，20-55KB/张）
      chat/history.jsonl
```

**实测数据事实（2026-08-03 盘点）：** 截图按约 1 秒间隔采集（相邻帧间隔 0.7-1.9s）。当前 3 个 session 共 30 帧：019f92f2（7 帧，约 7 秒）、019fa7d3（0 帧）、019fabe0（23 帧，约 27 秒）。桌面场景下相邻帧高度冗余，去重是质量判定的核心任务。

### 2.2 衍生数据目录（Phase 3 产出，只写）

```
<root_dir>/derived/
  <device_uuid>/
    <session_id>/
      audio/                        # Phase 2 音频产出（已有）
      video/
        windows/<window_id>.json   # 步骤 1：窗口筛选结果（逐帧判定 + 关键帧选择）
        frames/<frame_id>.json     # 步骤 2：关键帧深度提取结果（仅关键帧有）
      video_summary.json            # session 级图像汇总（全部窗口完成后生成）
```

- `window_id` = 窗口首帧的文件名 UUIDv7（确定性，天然幂等）
- 衍生 JSON 携带 `ts_ms` / `ts_utc`（来自文件名 UUIDv7 时间戳），Phase 4 对齐工具可直接消费
- 窗口筛选结果先于提取结果落盘；提取可独立重跑（`--extract-only`），不重新花筛选 tokens

### 2.3 窗口筛选结果 JSON Schema（步骤 1 主产出）

```json
{
  "schema_version": 1,
  "window_id": "019fabe5-b00e-7b2d-8000-...",
  "session_id": "019fabe0-3b0f-7947-8000-1e567def787d",
  "device_uuid": "b53985c7-cb7f-4382-95c9-18d33a9bc36a",
  "window_start_ms": 1784877784590,
  "window_end_ms": 1784877811468,
  "frame_count": 23,
  "model": "qwen3.7-flash",
  "analyzed_at": "2026-08-03T10:00:00+00:00",

  "window_caption": "用户浏览网页后打开 IDE 开始编辑代码",

  "frames": [
    {
      "frame_id": "0",
      "ts_ms": 1784877784590,
      "blur": "none",
      "quality_issues": [],
      "duplicate_of": null,
      "novelty": 1.0,
      "is_key_frame": true,
      "key_rank": 1,
      "reason": "浏览器窗口打开，画面首次出现"
    },
    {
      "frame_id": "1",
      "ts_ms": 1784877785526,
      "blur": "none",
      "quality_issues": [],
      "duplicate_of": "0",
      "novelty": 0.0,
      "is_key_frame": false,
      "key_rank": null,
      "reason": "与 frame 0 几乎相同，滚动动画中"
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `blur` | 枚举 `none / slight / heavy`（模糊判定） |
| `quality_issues` | 画质问题枚举：`black(黑屏) / partial(截断) / low_res / low_contrast / corrupted / other` |
| `duplicate_of` | 与窗口内前面某帧内容几乎相同 → 填该帧 `frame_id`；否则 `null`。**连续重复段仅首帧保留** |
| `novelty` | 相对窗口内已见内容的新颖度 0.0-1.0（供下游排序/抽样） |
| `is_key_frame` | 画面出现**显著内容变化**（切换窗口/打开新页面/弹窗/新内容/应用状态变化） |
| `key_rank` | 窗口内关键帧排序，1 = 最重要；非关键帧为 `null` |

**判定规则（写入 prompt）：** 模糊/黑屏/纯重复帧不算关键帧；连续几乎相同的帧除段首外全部标 `duplicate_of`；允许整窗口无关键帧（静态桌面）。

### 2.4 关键帧提取结果 JSON Schema（步骤 2 产出）

```json
{
  "schema_version": 1,
  "frame_id": "019fabe5-b00e-7b2d-8000-...",
  "window_id": "019fabe5-b00e-7b2d-8000-...",
  "session_id": "019fabe0-3b0f-7947-8000-1e567def787d",
  "device_uuid": "b53985c7-cb7f-4382-95c9-18d33a9bc36a",
  "ts_ms": 1784877784590,
  "ts_utc": "2026-07-29T03:23:04.590Z",
  "model": "qwen3.7-flash",
  "analyzed_at": "2026-08-03T10:00:00+00:00",

  "scene": "browser",
  "caption": "Chrome 浏览器打开 GitHub 仓库页面，窗口最大化",
  "apps": [
    {"name": "Google Chrome", "window_title": "anthropics/claude-code", "active": true}
  ],
  "objects": [
    {"name": "macbook", "description": "外接显示器上的桌面"}
  ],
  "events": [
    {"type": "scrolling", "description": "页面滚动中，代码文件列表变化"}
  ],
  "ocr": [
    {"text": "anthropics/claude-code", "location": "浏览器标签栏"},
    {"text": "README.md", "location": "主内容区"}
  ],
  "people": [
    {"count": 0, "description": "无人物"}
  ],
  "actions": [
    "浏览 GitHub 仓库页面",
    "准备查看 README 文档"
  ]
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `scene` | 枚举 `desktop / browser / ide / terminal / chat / document / video / other` |
| `caption` | 画面一句话客观描述 |
| `apps` | 可见应用 + 窗口标题（截图分析核心价值：用户在用什么） |
| `objects` | 画面中可见物体 |
| `events` | 画面中可观察到的事件，枚举 `typing / scrolling / dialog / notification / error / animation / other` |
| `ocr` | **显著可见文字**（标题、菜单、弹窗、代码、错误信息等），每条 ≤80 字，最多 15 条；非全文 OCR（见 §9 限制） |
| `people` | 画面中的人物与动作 |
| `actions` | 用户正在进行的动作列表，按重要性排序 |

### 2.5 Session 级汇总 video_summary.json（文本-only 聚合）

全部窗口筛选 + 关键帧提取完成后，把各关键帧的提取结果（caption/scene/ocr/actions）按时间顺序拼接为文本，连同 chat 消息做一次文本-only 模型调用，输出：

```json
{
  "schema_version": 1,
  "session_id": "019fabe0-3b0f-7947-8000-1e567def787d",
  "total_frames": 23,
  "key_frames_analyzed": 5,
  "windows_analyzed": 1,
  "model": "qwen3.7-flash",
  "analyzed_at": "...",

  "scene_timeline": [
    {"ts_ms": 1784877784590, "scene": "browser", "caption": "...", "frame_id": "..."}
  ],
  "apps_used": [
    {"name": "Google Chrome", "window_title": "...", "appearance": 3}
  ],
  "activities": ["浏览 GitHub", "编写代码", "运行测试"],
  "key_events": [
    {"time_s": 3.2, "type": "dialog", "description": "出现错误弹窗"}
  ],
  "people": [{"count": 0, "description": "无人物"}],
  "objects": [{"name": "macbook", "description": "..."}],
  "session_summary": "会话中用户先浏览网页后进入 IDE 编码，无异常事件"
}
```

`scene_timeline` 即 Phase 4 多模态对齐的"画面轨"骨架，与音频 summary.json 的 `timeline` 直接可合并。

---

## 3. 数据模型（inventory DB 扩展）

沿用 Phase 1 的 SQLite DB（`--db-path` 默认 `/opt/lvdata/lvdata_inventory.db`）。本工具通过 `CREATE TABLE IF NOT EXISTS` 增加分析状态表，并对 `sessions` 表做**增量加列**（`_ensure_column` 模式，音频工具已采用），不动 Phase 1 既有表结构。

### 3.1 新增表 video_analyses（每帧一行）

> ⚠️ **用量统计口径**：`filter_usage_in/out` 存的是**窗口级**用量（同窗口所有帧共享同一数值）。从 DB 聚合成本会按窗口帧数重复放大（30 帧窗口 ≈ 30×），工具运行报告用的是内存统计（正确口径）。报表若从 DB 统计成本，需按 `window_id` 去重或改用衍生 JSON 中的 usage。

```sql
CREATE TABLE IF NOT EXISTS video_analyses (
    analysis_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id            INTEGER NOT NULL REFERENCES files(file_id),
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    frame_id           TEXT NOT NULL,                -- UUIDv7 文件名（不含扩展名）
    window_id          TEXT NOT NULL,                -- 窗口首帧 frame_id
    derived_path       TEXT,                         -- 关键帧提取 JSON 绝对路径（非关键帧 NULL）
    model              TEXT NOT NULL,
    filter_status      TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | failed
    extract_status     TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | failed | skipped
    is_key_frame       INTEGER DEFAULT 0,
    key_rank           INTEGER,                      -- 窗口内排序，1=最重要
    blur_level         TEXT,                         -- none | slight | heavy
    quality_issues     TEXT,                         -- JSON 数组
    duplicate_of       TEXT,                         -- 重复来源 frame_id 或 NULL
    extract_json       TEXT,                         -- 提取结果冗余（快速查询用）
    filter_usage_in    INTEGER, filter_usage_out  INTEGER, filter_latency_ms INTEGER,
    extract_usage_in   INTEGER, extract_usage_out INTEGER, extract_latency_ms INTEGER,
    error              TEXT,
    started_at         TEXT,
    finished_at        TEXT,
    UNIQUE(file_id)
);
CREATE INDEX IF NOT EXISTS idx_va_session ON video_analyses(session_id);
CREATE INDEX IF NOT EXISTS idx_va_window   ON video_analyses(window_id);
CREATE INDEX IF NOT EXISTS idx_va_status   ON video_analyses(filter_status, extract_status);
```

### 3.2 sessions 表增量列

| 列 | 说明 |
|----|------|
| `video_status` | `pending / in_progress / completed` — 本工具的阶段状态（**不复用** `analysis_status`，避免与音频工具互相覆盖） |
| `video_summary_status` | `pending / ok / failed` — session 级图像汇总状态 |

### 3.3 待分析帧查询

```sql
-- 所有未完成筛选的视频帧（窗口级：整窗口完成后其全部帧 filter_status='ok'）
SELECT f.file_id, f.file_path, f.uuid_ts_ms, f.uuid_ts_utc,
       s.session_id, s.device_uuid, s.session_date
FROM files f
JOIN sessions s ON f.session_id = s.session_id
WHERE f.file_type = 'video'
  AND s.status IN ('active', 'finalized')
  AND NOT EXISTS (
      SELECT 1 FROM video_analyses v
      WHERE v.file_id = f.file_id AND v.filter_status = 'ok'
  )
ORDER BY s.session_date, f.uuid_ts_ms;

-- --extract-only：已筛选但未提取的关键帧
SELECT f.file_id, f.file_path, f.uuid_ts_ms, f.uuid_ts_utc,
       s.session_id, s.device_uuid, s.session_date
FROM files f
JOIN sessions s ON f.session_id = s.session_id
JOIN video_analyses v ON v.file_id = f.file_id
WHERE v.is_key_frame = 1 AND v.extract_status != 'ok'
ORDER BY s.session_date, f.uuid_ts_ms;
```

---

## 4. CLI Interface

```
lvdata_analyze_video.py [OPTIONS]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--root-dir` | PATH | `/opt/lvdata` | lvdata 根目录（派生目录 `derived/` 在其下） |
| `--db-path` | PATH | `/opt/lvdata/lvdata_inventory.db` | 与 Phase 1 相同的索引库 |
| `--device-uuid` | UUID | (none) | 仅分析指定设备 |
| `--session-id` | UUID | (none) | 仅分析指定 session |
| `--model` | STR | `qwen3.7-flash` | 百炼模型 ID |
| `--base-url` | URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点 |
| `--api-key` | STR | env `DASHSCOPE_API_KEY` | 百炼 API Key |
| `--window-s` | INT | `30` | 窗口秒数（实测截图 ~1fps，30s ≈ 30 帧/调用） |
| `--max-frames-per-call` | INT | `24` | 单次调用图片上限；超限窗口按帧数硬切分（成本护栏） |
| `--pipeline` | STR | `two-step` | `two-step`（筛选→提取）或 `one-step`（一步完成，见 §8 对比） |
| `--max-image-size` | INT | `1280` | 图片长边缩放像素（Pillow），`0` = 不缩放（成本杠杆，见 §6.4） |
| `--extract-chunk-size` | INT | `4` | 提取阶段每次调用打包的关键帧数 |
| `--concurrency` | INT | `4` | 并发调用数（注意百炼限流） |
| `--filter-only` | flag | false | 只跑窗口筛选（步骤 1） |
| `--extract-only` | flag | false | 只对已筛选的关键帧重跑提取（步骤 2，改提取 prompt 后使用） |
| `--no-session-summary` | flag | false | 跳过 session 级聚合调用 |
| `--force` | flag | false | 忽略已有结果全量重跑 |
| `--probe` | flag | false | 每 session 只处理第一个窗口后退出（Prompt 质量验证） |
| `--dry-run` | flag | false | 只列出待处理窗口/帧，不发 API 请求 |
| `--summarize-only` | flag | false | 重试缺失/失败的 session 图像汇总 |
| `--verbose` / `-v` | flag | false | 详细输出 |

### 使用示例

```bash
export DASHSCOPE_API_KEY=sk-...

# 全量分析（两步，30s 窗口，1280px 缩放）
python lvdata_analyze_video.py

# 先 dry-run 看窗口划分
python lvdata_analyze_video.py --dry-run -v

# 验证 Prompt：每个 session 只跑 1 个窗口
python lvdata_analyze_video.py --probe -v

# 60s 窗口 + 一步完成
python lvdata_analyze_video.py --window-s 60 --pipeline one-step

# 只对指定 session，原生分辨率（不缩放）
python lvdata_analyze_video.py --session-id 019fabe0-3b0f-7947-8000-1e567def787d --max-image-size 0

# 改进提取 prompt 后只重跑提取
python lvdata_analyze_video.py --extract-only --force

# 强制全量重跑
python lvdata_analyze_video.py --force
```

---

## 5. 模型调用设计（百炼 Qwen3.7-Flash）

### 5.1 关键调研结论（2026-08-03 已上网确认）

| 结论 | 说明 |
|------|------|
| 模型 ID | `qwen3.7-flash`（版本 `qwen3.7-flash-2026-07-15`），Qwen3.7 轻量高速档，原生视觉语言模型 |
| 多模态 | 文本 + 图像 + 视频输入、文本输出；官方文档推荐截图解析、界面识别、GUI 操作场景 |
| 图片限制 | 单次最多 **256 张**、单图最高 1600 万像素；本设计 24 帧/调用远低于上限 |
| 图片 Token | `h × w / 1024 + 2` / 张（官方公式），据此精确预估成本 |
| 结构化输出 | 非思考模式下支持 `response_format: {"type": "json_object"}`；**需关闭思考模式**（qwen3 系列 `extra_body={"enable_thinking": False}`，待 probe 验证参数形态） |
| 上下文/输出 | 1M tokens / 64k tokens，30 帧窗口 ≈ 30k tokens 输入无压力 |
| OCR | 通用图片文字提取支持 |
| 价格 | flash 按量分档（<32K 档输入约 ¥0.22/M、输出约 ¥0.94/M）；plus 8 折后输入 ¥1.6/M、输出 ¥6.4/M；max 为 Token Plan 订阅制——详见 §8.5 |
| ⚠️ 围栏残留 | 与音频工具同样风险：模型可能在 JSON 末尾附加 markdown 围栏，解析前必须剥离（复用音频工具已验证的 `sanitize()` 逻辑） |

### 5.2 请求结构（每窗口一次调用，多图输入）

```python
import base64
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=api_key, base_url=base_url)

# 每帧：读 JPEG → （可选 Pillow 缩放）→ base64 data URL
# 图片之间用 "frame <index>" 文本标记，模型按索引回填判定
user_content: list = []
for i, img in enumerate(images):           # images 按 uuid_ts_ms 升序
    user_content.append({"type": "text", "text": f"frame {i}"})
    user_content.append({"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}})
user_content.append({"type": "text",
                     "text": "请按系统提示的 JSON Schema 逐帧分析以上截图。"})

resp = await client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": FILTER_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ],
    stream=True,
    stream_options={"include_usage": True},
    response_format={"type": "json_object"},
    extra_body={"enable_thinking": False},     # qwen3 系列关闭思考模式（probe 验证）
)
```

提取阶段请求结构相同，仅替换系统 prompt 与图片集（关键帧分批，每批 ≤ `--extract-chunk-size` 张）。

### 5.3 筛选 System Prompt（步骤 1，JSON Schema 内嵌）

```text
你是屏幕截图序列分析引擎。你只输出 JSON，不输出任何其他文本或解释。

用户提供按时间顺序排列的 N 张屏幕截图（图片之间以 "frame <index>" 文本标记索引，从 0 开始）。
请逐帧判定并严格按照以下 JSON Schema 输出：

{
  "window_caption": "该时间窗口内屏幕内容变化的一句话概括",
  "frames": [
    {
      "frame_id": "必须使用用户提供的图片索引字符串",
      "blur": "none|slight|heavy",
      "quality_issues": ["black|partial|low_res|low_contrast|corrupted|other"],
      "duplicate_of": "与窗口内前面某帧内容几乎相同则填该帧的 frame_id，否则 null",
      "novelty": "0.0-1.0，相对窗口内已见内容的新颖度",
      "is_key_frame": "是否为关键帧 true|false",
      "key_rank": "关键帧排序，1=最重要；非关键帧填 null",
      "reason": "一句话理由"
    }
  ]
}

要求：
1. 每个输入的 frame_id 都必须有且仅有一个条目，不得遗漏、不得编造索引。
2. 连续内容几乎相同的帧：除该段第一帧外，其余 duplicate_of=段首帧，is_key_frame=false。
3. is_key_frame=true 的条件：画面出现显著内容变化（切换窗口、打开新页面、弹窗、
   新内容出现、应用状态变化）。模糊、黑屏、纯重复不算关键帧。
4. 若整窗口无显著变化，允许所有帧 is_key_frame=false，window_caption 描述静态画面。
5. 数组字段可以为空但必须存在；不确定填 null，不要编造；只输出 JSON，不要 Markdown 代码块。
```

### 5.4 提取 System Prompt（步骤 2）

```text
你是专业的屏幕截图内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。

用户提供 1-4 张关键帧截图（图片之间以 "frame <index>" 标记索引）。
对每张图严格按照以下 JSON Schema 输出：

{
  "frames": [
    {
      "frame_id": "必须使用用户提供的图片索引字符串",
      "scene": "desktop|browser|ide|terminal|chat|document|video|other",
      "caption": "画面的一句话客观描述",
      "apps": [{"name": "应用名", "window_title": "窗口标题", "active": true}],
      "objects": [{"name": "物体名", "description": "描述"}],
      "events": [{"type": "typing|scrolling|dialog|notification|error|animation|other",
                  "description": "画面中正在发生的事件"}],
      "ocr": [{"text": "显著可见文字，每条不超过80字", "location": "画面位置"}],
      "people": [{"count": 0, "description": "人物与动作"}],
      "actions": ["用户正在进行的动作列表，按重要性排序"]
    }
  ]
}

要求：
1. ocr 提取画面中显著可见的文字（标题、菜单、弹窗、代码、错误信息等），最多 15 条；
   画面文字极少时可为空数组。
2. events 描述静态画面中可观察到的事件，无则为空数组。
3. 每个数组字段可以为空但必须存在；只输出 JSON，不要 Markdown 代码块。
```

### 5.5 Session 汇总调用（文本-only）

把各关键帧提取 JSON 的 `ts_utc + scene + caption + actions + ocr 摘要` 按时间拼接为文本，连同 `chat_messages`（查 DB），prompt 要求输出 §2.5 的 schema（纯 JSON）。与音频工具 `summarize_session` 模式一致。

### 5.6 实测验证计划（probe_qwen_image.py）

仿照音频工具的先例（`probe_qwen_structured_output.py`），实现 `tools/data-analysis/probe_qwen_image.py`，在实现主工具前验证：

| 测试 | 验证点 |
|------|--------|
| 单图 + `json_object` | 结构化输出可用性、围栏残留、usage 字段 |
| 多图（3 帧真实截图）+ `json_object` | 多图输入可用性、模型能按索引逐帧回填判定 |
| `enable_thinking=False` | 参数形态与必要性（qwen3 系列文档要求） |
| Token 实测 | 单图实际 tokens vs 公式 `h×w/1024+2`，用于 §8 成本校准 |
| 分辨率 | 缩放 1280 vs 原图：OCR 可读性与 token 差异 |

---

## 6. Processing Logic

### 6.1 窗口分组

```python
def group_windows(frames, window_s, max_frames):
    """按时间窗分组；超时或超帧数上限则开新窗口。返回 [[frame,...], ...]"""
    windows, cur, win_start = [], [], None
    for f in sorted(frames, key=lambda x: x["uuid_ts_ms"]):
        if (win_start is None
                or f["uuid_ts_ms"] - win_start > window_s * 1000
                or len(cur) >= max_frames):
            if cur:
                windows.append(cur)
            cur, win_start = [f], f["uuid_ts_ms"]
        else:
            cur.append(f)
    if cur:
        windows.append(cur)
    return windows
```

- `window_id` = 窗口首帧的 frame_id（确定性，重跑定位相同）
- 实际数据 1fps：`--window-s 30` → 约 30 帧/窗口；`--max-frames-per-call 24` 为护栏，超限硬切

### 6.2 主流程（默认 two-step）

```
main()
  ├── parse_args()
  ├── connect_db()                              # 同 inventory DB
  ├── ensure_schema()                           # video_analyses + sessions 增量列
  ├── select_pending_frames()                   # §3.3 SQL
  │     └── --dry-run: 列出窗口划分后直接退出
  ├── group_windows()                           # §6.1
  ├── 按 session 分组
  │     └── for each session:
  │           ├── mark_session_video('in_progress')
  │           ├── 【步骤 1 筛选】for each window (Semaphore(concurrency)):
  │           │     ├── 读帧 + 缩放 + base64
  │           │     ├── call_filter()           # 一窗一次调用，流式 + 重试
  │           │     ├── parse_and_validate()    # JSON 解析 + 逐帧校验，失败重试
  │           │     ├── write windows/<window_id>.json
  │           │     └── upsert_frame_rows()     # 每帧一行 (filter_status, is_key_frame...)
  │           ├── 【步骤 2 提取】(若 --extract-only 或筛选产出关键帧)
  │           │     ├── 关键帧按 extract_chunk_size 分批
  │           │     └── for each batch (Semaphore):
  │           │           ├── call_extract()    # 批内每帧一次结构化提取
  │           │           ├── write frames/<frame_id>.json
  │           │           └── upsert_frame_rows()  # extract_status=ok + extract_json
  │           ├── 若未 --no-session-summary:
  │           │     ├── call_video_summary()    # 文本-only 聚合
  │           │     └── write video_summary.json + video_summary_status
  │           └── mark_session_video('completed'/'failed')
  └── print_summary()                           # 帧/关键帧/窗口/token/成本
```

**one-step 变体**（`--pipeline one-step`）：每窗口一次调用，prompt 为筛选 schema 的扩展版——对 `is_key_frame=true` 的帧**额外输出**提取字段（scene/caption/apps/ocr/...，即 §2.4 内嵌为 `key_frames_detail` 数组）。调用后同时写 window json 与各关键帧 json，DB 两个 status 一并置 ok。

### 6.3 幂等与恢复

- 重跑时 `SELECT` 排除 `filter_status='ok'` 的帧 → 已完成窗口整窗跳过
- `--extract-only`：只重跑关键帧提取，筛选结果复用（改提取 prompt 不重花筛选 tokens）
- `--force`：删除对应 `video_analyses` 行 + 衍生 JSON，全量重跑
- 单窗口 3 次失败 → 该窗口全部帧标 `filter_status='failed'` 记 error，继续下一窗口；结尾汇总失败清单，可单独重跑

### 6.4 图片预处理（仅成本控制，不参与判定）

- `--max-image-size 1280`（默认）：Pillow 按长边缩放到 1280px，质量 85 JPEG 重编码
  - 1280×800 ≈ **1002 tokens/帧** vs 原生 retina 2560×1600 ≈ **4002 tokens/帧**，省 ~4× 输入成本
  - 1280px 对 blur/dup/画质判定与主文本 OCR 足够；小字 OCR 略降（§9 限制）
- `--max-image-size 0`：原图直传（OCR 最优，成本最高）
- 缩放不改写源文件；每窗口调用时内存中处理

### 6.5 容错与校验

- 黑屏/全白/损坏 JPEG → 模型判 `quality_issues=['black']` 等，正常落库不重试
- JSON 解析失败 / 字段缺失 / frame_id 遗漏 → 重试 1 次（同图）；仍失败该窗口标 failed
- 读图失败（文件损坏）→ 该帧标 failed，窗口其余帧继续
- 单个 session 整体失败不影响其他 session

### 6.6 限流与重试

与音频工具一致的策略：

- `asyncio.Semaphore` 默认并发 4（百炼限流，429 超限）
- 重试：`429 / 5xx` → 指数退避 + 抖动（1s, 2s, 4s, 8s），最多 4 次；`400` 不重试直接 failed
- 单次请求超时 300s（含流式接收）
- 每次调用记录 `usage_input/output_tokens` 与 `latency_ms`，`--verbose` 实时打印

---

## 7. File Layout

```
tools/data-analysis/
  lvdata_inventory.py          # Phase 1: 索引（已完成）
  lvdata_analyze_audio.py      # Phase 2: 音频分析（已完成）
  lvdata_analyze_video.py      # Phase 3: 本设计，待实现
  probe_qwen_image.py          # 探针：多图 + 结构化输出验证（§5.6）
  lvdata_summary.py            # Phase 4-5: 跨 session 报告（future）
```

与 Phase 1/2 共享 `--root-dir` / `--db-path` 默认值，天然衔接。

---

## 8. 成本与策略对比：one-step vs two-step

### 8.1 数据基准（实测）

- 当前数据：3 sessions、30 帧（7 + 0 + 23），约 1 fps
- **实测分辨率 640×480**（2026-08-03 probe 确认）→ 单帧 ≈ 302 tokens（640×480/1024+2），低于原估算；`--max-image-size 1280` 对其不触发缩放，作为未来高分辨率数据的护栏
- 实际关键帧占比：30 帧 → 6 关键帧（20%）
- 实测用量（全量两步法）：输入 9921 / 输出 2486 tokens，总成本 ≈ ¥0.005（flash）
- 价格锚点：flash <32K 档输入 ≈ ¥0.22/M、输出 ≈ ¥0.94/M；plus 折后 ¥1.6/¥6.4；max 订阅制（以百炼控制台实时价为准）

### 8.2 对比（一个 30 帧窗口，K=5 关键帧，缩放后）

| 维度 | A. 一步（one-step） | B. 两步（two-step） |
|------|--------------------|--------------------|
| 调用次数 | 1/窗口 | 1 筛选 + ⌈K/4⌉ 提取/窗口 |
| 输入 tokens | 30 × 1000 = 30k | 30 × 1000 + 5 × 1000 = 35k（≈1.17×） |
| 输出 tokens | 30×40 + 5×400 ≈ 3.2k | 30×40 + 5×400 ≈ 3.2k（同量级） |
| 合计 | ≈ 33k ≈ ¥0.037 | ≈ 38k ≈ ¥0.042 |
| 筛选质量 | 同 prompt 既判质量又深挖，窗口帧多时易漏判关键帧 | 筛选 prompt 专注，判定更稳 |
| 提取完整性 | 输出受窗口上下文挤占，OCR/action 易截断 | 单帧/小批专注提取，OCR 与动作更全 |
| 可重跑性 | 改提取 prompt 需整窗重跑（重花筛选 tokens） | `--extract-only` 只重跑提取，筛选结果复用 |
| 并发性 | 提取与筛选串行耦合 | 提取批次可并行，延迟更低 |

**结论：默认 two-step。** 当前数据量下两者成本差 <15%（可忽略），决定因素是提取质量与可重跑性；一步法保留为小窗口/快速预览选项。

### 8.3 全量成本估算（当前 30 帧，two-step + 缩放）

| 项 | tokens | 成本 |
|----|--------|------|
| 筛选：30 帧 × 1000 输入 + ~1.2k 输出 | ~31k | ~¥0.033 |
| 提取：~5 关键帧 × 1000 输入 + ~2k 输出 | ~7k | ~¥0.008 |
| 汇总：2 次文本调用 | ~4k | ~¥0.004 |
| **合计** | **~42k** | **< ¥0.05** |

不缩放（原生 retina）：总 tokens ≈ 4×，仍 < ¥0.2。**全部成本可忽略。**

### 8.4 大规模场景（如 10 万帧 ≈ 28 小时屏幕时间）

- 筛选必须读全量帧：100k × 1000 tokens ≈ 1 亿输入 tokens ≈ ¥100（缩放后）；原生分辨率 ¥400+
- 提取只读关键帧（2-5%）：约 2-5k 帧，成本占比 <10%
- **成本主杠杆**：① `--max-image-size` 缩放；② 窗口内去重后关键帧占比低；③ 大规模时输出压缩（如仅"变化描述"）
- 进一步降本方向（超出 VLM-only 约束，列为 Open Question）：哈希/embedding 预筛重复帧，仅把疑似变化帧送 VLM

### 8.5 三模型成本对比（同一工作负载：30 帧窗口，两步法，缩放 1280px）

| 模型 | 输入单价 | 输出单价 | 每窗口（~38k in / 3.2k out） | 全量 30 帧（2 窗口 + 汇总） | 备注 |
|------|---------|---------|------------------------------|----------------------------|------|
| **qwen3.7-flash** | <32K 档 ≈ ¥0.22/M；32K-256K 档 ≈ ¥0.72/M | <32K 档 ≈ ¥0.94/M；32K-256K 档 ≈ ¥2.9/M | ≈ ¥0.014 | **≈ ¥0.03** | 分档阶梯计价；缩放后窗口刚落在 <32K 基础档 |
| **qwen3.7-plus** | ¥1.6/M（8 折后，≤256K 档） | ¥6.4/M（8 折后） | ≈ ¥0.081 | **≈ ¥0.16** | 新用户免费 100 万 tokens，可覆盖当前全量数据的 24 倍 |
| **qwen3.8-max** | Token Plan 订阅（无按量价） | 同 | — | **订阅月费量级** | 预览期白天 1 折、夜间约 0.2 折；个人版禁批量调用 |

**关键洞察 — 分档阶梯计费放大缩放杠杆：** flash 的 <32K / 32K-256K 两档单价差约 3.3×。30 帧窗口缩放后 ~30k tokens 恰好落在 <32K 基础档；**不缩放的原生 retina 截图（30 × 4000 = 120k tokens）直接进入 32K-256K 档**——token 数 4× 与单价 3.3× 叠加，成本放大约 13×。`--max-image-size 1280` 默认值不仅省 token，更是压住单价档位。

**效果差异与验证路径：** 三模型效果差异集中在 ① 判定稳定性（blur/dup/key frame 误判率）② OCR 完整性（小字、密集代码）③ 窗口内多图对比（去重准确性）④ JSON Schema 遵从度。建议 probe 阶段对同一窗口分别用三模型各跑一次，人工对比筛选与提取结果后定默认模型（探针脚本支持 `--model` 参数，§5.6）。

---

## 9. 已知限制与 Open Questions

1. **跨窗口去重** — 去重判定仅在窗口内（VLM 需同时看到两帧才能比较）。相邻窗口边界的重复帧可能重复计入关键帧。1fps 高冗余场景影响小，可后续用重叠窗口或全局 embedding 方案解决。

2. **OCR 非全文** — 提取 prompt 限制显著文字 ≤15 条/帧，小字/密集文本（代码编辑器等）会丢细节。需全文 OCR 时可对特定关键帧单独跑 qwen-ocr（超出"VLM-only"约束，作为后续选项）。

3. **关键帧选择依赖模型** — 1fps 桌面场景模型可能把动画中间帧误判为关键帧。`--probe` 阶段建议人工抽查若干窗口的筛选结果，必要时调整 prompt 中的"显著变化"判定规则。

4. **思考模式参数** — qwen3.7 系列结构化输出需非思考模式；`extra_body={"enable_thinking": False}` 的参数形态在 probe 阶段确认（不同 qwen3 版本参数名可能为 `chat_template_kwargs` 等）。

5. **价格未最终确认** — qwen3.7-flash 官方定价页未在本次调研中拿到确切数值，§8 以输入 ¥1/M、输出 ¥2/M 为锚点估算。工具已设计 usage 落库，全量跑完可精确核算（音频工具同款做法）。

6. **隐私合规** — 截图含用户桌面内容，将上传至阿里云处理，与音频工具一致。

7. **session 级汇总与音频汇总的合并** — `video_summary.json` 的 `scene_timeline` 与 `summary.json` 的 `timeline` 的合并留给 Phase 4 对齐工具（`lvdata_summary.py`）。

8. **截图采集间隔无 config 字段** — 实测为 ~1fps（0.7-1.9s），但 session.json 无显式间隔配置。若未来采集策略变化（如 10s/张），窗口帧数与去重率变化，`--window-s` / `--max-frames-per-call` 需相应调整（已参数化，无代码改动）。

9. **qwen3.8-max 批量调用限制** — Token Plan 个人版明确禁止后端批量调用（仅限 Qoder CN / Cursor / Claude Code 等交互式工具内使用，`sk-sp-` 专属 Key）。本工具属于批量管线，生产环境使用 max 需确认团队版条款或等待正式版开放按量计费。另外 128K 原生上下文（可扩 1M）对 30 帧窗口无压力，但超长会话汇总需注意。

---

## 10. Dependencies

```python
# Python
openai>=1.52.0        # AsyncOpenAI 客户端
Pillow                # 图片缩放（--max-image-size 0 时可不装）
# 其余 stdlib: sqlite3, json, argparse, asyncio, base64, os
# 系统: 无 ffmpeg 依赖（JPEG 直传）
```

---

## 11. 调研参考

- 视觉理解文档（百炼，2026-07-25 更新）：<https://help.aliyun.com/zh/model-studio/vision-model/>
- Qwen3.7-Flash 功能介绍：<https://developer.aliyun.com/article/1749210>
- Qwen3.7 系列选型对比：<https://developer.aliyun.com/article/1744567>
- Qwen3.7-Flash 核心功能解析：<https://developer.aliyun.com/article/1752628>
- Qwen3.7-Flash 定价（OpenRouter 美元价，对应百炼同三档结构）：<https://www.eesel.ai/blog/qwen-3-7-flash-pricing>
- Qwen3.7 Max/Plus/Flash 能力差异与场景适配：<https://developer.aliyun.com/article/1745443>
- Qwen3.7-Plus 价格表（8 折后输入 ¥1.6/M、输出 ¥6.4/M）：<https://developer.aliyun.com/article/1744769>
- Qwen3.8-Max-Preview 功能介绍：<https://developer.aliyun.com/article/1752609>
- Qwen3.8-Max-Preview 全解析（Vision Arena 第二、BabyVision 82.0）：<https://developer.aliyun.com/article/1752743>
- 百炼 Token Plan 订阅（qwen3.8-max 仅订阅可用）：<https://www.aliyun.com/benefit/scene/tokenplan>
- qwen3.8-max 图像模态支持修复（qwen-code PR #7491）：<https://github.com/QwenLM/qwen-code/pull/7491>
- qwen-vl-plus 定价：<https://help.aliyun.com/zh/model-studio/qwen-vl-plus>
- 结构化输出（json_object）：<https://help.aliyun.com/en/model-studio/qwen-structured-output>

---

*Design version: 1.0 | Created: 2026-08-03*
