# LvData MCP 多语言（分析产物输出语言 + MCP/REST 文案）

> **状态**：已实施（2026-10-28）；单测通过，真实 LLM 探针待执行（§6.5）。
> **范围**：① `tools/lvdata-mcp` 的**分析产物**语言——音频分析、画面分析、日总结，
> 以及它们写入 `events` / `day_summary` 后给 BFF/App/Agent 的内容；
> ② MCP/REST 面向 agent/调用方的文案统一英文（`instructions`、tool 描述、error/hint）。
> **不包含**：CLI help/README/运行日志、分析 prompt 模板翻译、OCR/转写原文翻译。
> **关联文档**：`docs/data/lvdata_mcp_service_v2.md`、`docs/data/lvdata_mcp_tools.md`、
> `docs/audio_analysis.md`、`docs/video_analysis.md`、`docs/server_multilingual.md`。

---

## 0. 结论先行

| 项 | 结论 |
|---|---|
| 配置 | `lvdata_mcp.yaml` 顶层新增单值 `language`；缺省 `en`；空/非法/未知 → `en`（warning） |
| 支持集合 | App 当前 16 个 canonical code（无 `tr`，见 §4.1） |
| 作用面 | **分析生成的自然语言字段**（音频 summary、画面 caption/place/objects、日总结等） |
| 不翻译 | `transcript`、`ocr[].text`、chat/transcribe/translate/agent_job 原文 |
| 旧数据 | **配置变化不触发重分析**；只有 `force` 才重跑旧文件/旧日总结 |
| 强制重分析 | `POST /api/analysis/trigger` + `force=true`；`day_summary` 的 force 需扩展现有语义 |
| 数据混合 | 旧产物保留旧语言，新/强制重分析产物使用新语言；不新增 DB 字段 |
| MCP/REST 文案 | **统一英文，只使用英文**：instructions/tool 描述/error/hint（D9） |
| 协议/DB | **零 schema 变更**；MCP tool 入参/REST 入参不变 |
| 关键风险 | ① Qwen 是否严格遵守目标语言；② `day_summary` force 当前不覆盖 ok 行；③ 目标语言化会降低原文语言的部分检索命中 |

---

## 1. 需求与约束

原始需求：

> 在 lvdata_mcp server config file 里加入 `language:` 配置；字段注释说明支持的语言列表，
> 就是 App 的 16 个；数据分析的产物使用 `language` 的语言，默认 `en`；
> 旧的数据不再分析，除非 force；只支持一种语言；以 codebase 为基础，最小化修改。

落地口径：

1. 一个服务实例只配置**一种**输出语言，不做 per-device / per-session / per-event 多语言。
2. 配置缺省 = `en`；空值、非法值、未知值回落 `en` 并记录 warning（D1）。
3. `language` 只影响**由 LLM 生成的自然语言产物**；原始用户/媒体文本不翻译。
4. 配置变化本身不等于重分析信号；旧 `files` / `day_summary` 保持原样。
5. 只有 `force=true` 才重跑旧数据；`force` 的范围见 §4.6。
6. 不新增 DB 列、协议字段、MCP tool 入参；不引入服务器翻译服务。
7. MCP/REST 面向 agent/调用方的全部文案统一英文，只使用英文（D9）；分析 prompt
   模板保持中文 + 目标语言指令（D11）。

---

## 2. 现状（codebase 事实）

### 2.1 配置层

- `LvdataMcpConfig`（`tools/lvdata-mcp/src/lvdata_mcp/config.py`）目前只有
  `server/data/watcher/providers` 四段，**没有 language/target_language**。
- `LvdataMcpConfig.model_config.extra = "ignore"`：顶层未知字段会被忽略；因此只在 yaml
  加 `language:` 而不改 pydantic 模型不会生效，必须新增字段。
- `effective_summary()` 只打印非敏感生效值；新增 language 后应加入启动摘要。
- yaml 支持 `${ENV_VAR}` 与 `LVDATA_MCP__` 双层下划线 env override；新增顶层字符串字段
  后 `LVDATA_MCP__LANGUAGE=ja` 自动可用（`_set_nested` 按既有类型处理）。

### 2.2 生成产物与 prompt

| 产物 | 生成处 | 现有 prompt | 实际语言 | 原始字段 |
|---|---|---|---|---|
| 音频 `summary` / `emotions[].evidence` / `objects[].name,description` / `people.description` | `analysis/audio.py` | `AUDIO_SYSTEM_PROMPT` 全中文（`:61`） | 中文 | `transcript` 原语言；`language` 是源语言检测码 |
| 画面 `window_caption` / `reason` | `analysis/vision.py` | `FILTER_SYSTEM_PROMPT` 全中文（`:53`） | 中文 | `frame_id`/`quality`/`scene_id`/`key_frame` 为结构值 |
| 画面 `caption` / `place` / `objects*` / `people*` / `ocr[].location` | `analysis/vision.py` | `EXTRACT_SYSTEM_PROMPT` 全中文（`:69`） | 中文 | `ocr[].text` 保留画面原文 |
| 日总结 `summary` / `moments` / `topics[].topic` | `summary/day.py` | `DAY_SYSTEM_PROMPT` 全中文（`:119`） | 中文 | `topics[].search_words` 要求事件摘要原文连续片段 |
| chat / transcribe / translate / agent_job | `analysis/chatlog.py` | 无 LLM | 原语言 | 原样 |

补充事实：

- 音频 prompt 的 `language` 字段是**检测到的源语言**（如 `zh`/`en`），不是输出语言；
  当前代码只把它存进 audio payload，没有用于选择 summary 语言。
- 视频/画面 prompt 没有 language 字段；`ocr` 保留图片文字原语言。
- 日总结 prompt 的输入可能混合多语言事件；输出 `summary/moments/topics` 为中文。
- 现有 DB 实测可印证：非中文 audio 事件的 `transcript` 保留原文，`summary` 为中文；
  video/window caption 为中文；`day_summary.summary` 为中文，即使当天事件包含多语种。

### 2.3 数据模型

- `events` 无 `language` 列；audio `payload.language` 是段级源语言检测结果。
- `day_summary` 无 `language` 字段；`search_docs` 无语言列。
- MCP tools 的 `SearchParams` / `TimelineParams` / `RecentEventsParams` 等没有
  `language` 参数；服务端不处理 `Accept-Language`。
- lvdata-mcp 不从 `session.start.language` 获取 App 语言；`chat_server.db.devices`
  也没有 language 列。因此**分析阶段目前没有 App UI 语言可用**；本设计使用服务配置
  单值，而不是自动跟随 App 语言。

### 2.4 旧数据与 force 现有语义

- 音频/画面分析取件：`analysis/common.select_pending_files()` 只取
  `analysis_status IN ('pending','failed')`；已经 `ok` 的文件不会被普通 poll 重跑。
- `audio.process(force=True)` / `vision.process(force=True)` 会把对应 `files` 行重置为
  `pending` 后重跑（可按 `session_id` 限定，`analysis/audio.py:385`、
  `analysis/vision.py:335`）。
- `chatlog.process(force=True)` 重置 chat 三 jsonl 的读取水位并重放；chat 原文不变，
  对本语言需求通常不需要 force。
- `day_summary.generate(force=True)` **当前只重置非 ok 行**，`_all_dirty()` 也只选
  `status != 'ok'`；`_generate_one()` 对 `ok` 且无新事件的日还有 `up_to_date` 跳过。
  **因此直接 force 不会重生成已 ok 的旧日总结**——与本需求“force 重分析旧数据”冲突，
  需要最小扩展（§4.6）。
- `reindex` / `embed` 的数据源是 `events.summary`；event 被重分析更新后，需要重建
  FTS/向量才能让检索反映新语言。

### 2.5 现有语言相关的检索/过滤（本设计不改，但会影响体验）

- `index.py:13` `CJK_RE` 只覆盖汉字；`cjk_bigram()` 只对汉字连续串做 bigram。
- `index.py:14` `STOPWORDS` 只有中文停用词。
- `analysis/audio.py:255-266` `_word_count` 只统计汉字 + `[A-Za-z0-9']+`：
  日文假名、韩文、阿拉伯文、泰文、俄文、印地文等低价值语音可能被判静音；
  `is_silent` 会在 `value != high` 时丢弃事件。
- 这些问题属于既有多语言缺口，本设计只新增输出语言，不顺手修改（保持最小化）；
  但文档需要明确：修改 `language` 不会自动修复音频静音过滤/FTS 分词。

### 2.6 MCP/REST 文案现状（D9 英文化对象）

- `server.py:37` `INSTRUCTIONS`：中文，agent 连接后的常驻上下文。
- `tools.py`：5 个 tool 的 `description` 全中文；tool error/hint 全中文。
- `store.py`：`StoreError` 的 message/hint 中文（如 DB 不存在、路径越界、cursor 无效）。
- `server.py`：REST `/api/*`、auth 的错误/hint 中文（如 Bearer token、请求体、会话不存在）。
- `schemas.py`：自定义 pydantic validator 消息中文；pydantic 内建消息本身英文。
- `timeutil.py`：日期窗/日期格式 ValueError 中文，`tools.py` 会把它作为
  `invalid_param` message 返回给调用方。
- `config.py` / `vec.py`：启动/配置错误消息中文（env 缺失、配置文件缺失、
  embedding key 缺失等）。
- `main.py`：CLI 错误 print（`DB 不存在`、`未配置 embedding provider` 等）中文；
  CLI help/成功日志不在 D9 范围。
- `docs/data/lvdata_mcp_tools.md` D2 曾定“instructions 中文 only，中英双语否决”；
  本设计 D9 已取代该口径，§14.2/D2 已同步为英文。

这些文案不影响历史产物语言，但会直接影响 agent 行为与调用方体验；本次统一改英文。

---

## 3. 问题清单

| # | 问题 |
|---|---|
| P1 | `lvdata_mcp.yaml` / `LvdataMcpConfig` 没有输出语言配置 |
| P2 | 音频/画面/日总结 prompt 全中文，生成字段固定中文，无法按部署语言输出 |
| P3 | 旧数据需要语言迁移时，`day_summary` 的 force 不覆盖 ok 行；直接 force 不满足“重分析旧数据” |
| P4 | 没有语言元数据；旧/新产物语言混合时无法区分（本设计接受，见 §4.9） |
| P5 | 既有音频静音过滤/FTS 分词的语言缺口与输出语言无关，但会放大非中文体验问题 |
| P6 | MCP/REST instructions/tool 描述/error/hint 全中文；agent 常驻上下文和错误面向中文，不可按调用方英文化 |

---

## 4. 设计

### 4.1 配置字段

在 `configs/dev/lvdata_mcp.yaml`、`configs/test/lvdata_mcp.yaml` 顶层新增：

```yaml
# 数据分析产物输出语言（单值；默认 en）。
# 支持 App 当前 16 个 canonical code：
# en, zh-Hans, zh-Hant, ja, ko, ru, de, fr, it, es, pt, hi, id, th, vi, ar
language: en
```

`config.py` 增加：

```python
SUPPORTED_LANGUAGES = (
    "en", "zh-Hans", "zh-Hant", "ja", "ko", "ru", "de", "fr",
    "it", "es", "pt", "hi", "id", "th", "vi", "ar",
)

class LvdataMcpConfig(BaseModel):
    language: str = "en"       # 用 mode="before" validator 处理 YAML null/空串
    # ... existing fields
```

- 精确匹配 16 个 code；**不含 `tr`**。来源：`apps/android/LivaChatApp/.../AppLanguage.kt`
  与 `apps/ios/LivaChatApp/Core/UI/AppLocale.swift` 当前 canonical code 集合。
- 缺省 = `en`；空值（含 YAML `language:` → null）、非法值、未知值归一为 `en` 并记录
  warning（D1，不 fail-fast）：`language if language in SUPPORTED_LANGUAGES else "en"`。
  validator 必须用 `mode="before"`（否则 `None` 会在字段校验前被 pydantic 拒绝）。
- 归一后再进入 `LANGUAGE_NAMES` 查表；原始客户端/配置字符串不进入 prompt。
- **该字段不影响 MCP/REST 文案**：D9 固定英文，与 `language` 取值无关。
- 新字段加入 `effective_summary()`，启动日志可见生效语言。
- 配置在进程启动时读取；修改 yaml 后需重启 lvdata-mcp（无热加载）。
- 不需要 per-device/per-session 配置；单实例单语言。

### 4.2 语言名与 prompt 指令

新增一个小模块 `tools/lvdata-mcp/src/lvdata_mcp/language.py`：

```python
LANGUAGE_NAMES = {
    "en": "English",
    "zh-Hans": "Simplified Chinese",
    "zh-Hant": "Traditional Chinese",
    "ja": "Japanese", "ko": "Korean", "ru": "Russian",
    "de": "German", "fr": "French", "it": "Italian",
    "es": "Spanish", "pt": "Portuguese", "hi": "Hindi",
    "id": "Indonesian", "th": "Thai", "vi": "Vietnamese", "ar": "Arabic",
}

def output_language_rule(language: str) -> str:
    lang = language if language in LANGUAGE_NAMES else "en"   # 防御性归一
    return f"{LANGUAGE_NAMES[lang]} ({lang})"
```

不跨包引用 `services/session/proactive_i18n.py`；lvdata-mcp 独立部署，复制 16 项静态表
并由测试锁定，避免服务包与工具包耦合。

### 4.3 目标字段 / 原始字段

**目标语言（生成）**：

- 音频：`summary`、`emotions[].evidence`、`objects[].name`、`objects[].description`、
  `people.description`
- 画面 filter：`window_caption`、`reason`
- 画面 extract：`caption`、`place`、`objects[].name`、`objects[].description`、
  `people[].description`、`ocr[].location`
- 日总结：`summary`、`events`（moments）、`topics[].topic`

**始终保留原文（不翻译）**：

- 音频 `transcript`；`language` 字段继续表示**源语言检测码**
- 画面 `ocr[].text`
- chat / transcribe / translate / agent_job 的 `events.summary` 与 payload 原文
- 所有 JSON 字段名、`value`/`quality`/`emotion` 等枚举值、`frame_id`/`scene_id`/
  `key_frame` 等结构值

**特殊例外**：日总结 `topics[].search_words` 必须保持“事件摘要原文连续片段”，
不翻译；否则它无法用于 `search` 命中该主题事件。`topics[].topic` 使用目标语言。
（这是唯一不遵循“产物全部目标语言”的字段，见 D5。）

因此新 `events.summary` 可能是混合文本：audio = 原文 `transcript` + 目标语言
`summary`（+ 英文枚举）；video = 目标语言 caption/place + 原文 OCR。这是有意保留
原文可检索性的结果，不是遗漏。

### 4.4 prompt 注入（只追加，不重写模板）

在现有中文 prompt 末尾追加一条紧凑指令；不翻译 prompt 本身：

**音频**

```text
输出语言要求：summary、emotions[].evidence、objects[].name、objects[].description、
people.description 使用 {name} ({code}) 书写；transcript 保留原语言；language 字段填
检测到的源语言代码；value/emotions[].emotion 等枚举与 JSON 字段名保持不变。
```

**画面 filter**

```text
输出语言要求：window_caption、reason 使用 {name} ({code}) 书写；
frame_id/quality/scene_id/key_frame 与 JSON 字段名保持不变。
```

**画面 extract**

```text
输出语言要求：caption、place、objects[].name、objects[].description、
people[].description、ocr[].location 使用 {name} ({code}) 书写；
ocr[].text 保留画面原文；JSON 字段名与结构值保持不变。
```

**日总结**

```text
输出语言要求：summary、events、topics[].topic 使用 {name} ({code}) 书写；
topics[].search_words 必须保持事件摘要原文连续片段，不翻译；
JSON 字段名与统计字段保持不变。
```

注入点：

- `AudioProcessor._call_audio()` 使用 `AUDIO_SYSTEM_PROMPT + rule`；
- `VisionProcessor._filter()` / `_extract()` 使用对应 prompt + rule；
- `DaySummary._summarize()` 使用 `DAY_SYSTEM_PROMPT + rule`；
- 三者都从 `self.cfg.language` 读取，不需要新增构造参数。

### 4.5 旧数据与 force

**普通运行（无 force）**

- 新文件仍是 `pending` → 使用新 `language` 分析。
- 旧 `ok` 文件不会被 `select_pending_files()` 选中；旧 events 保持原语言。
- 旧 `day_summary` 不会被语言配置变化触发；只有该日出现新事件
  （`events.updated_at > generated_at`）时才会按当前语言重新生成该日总结。
- 这是有意的混合状态：旧产物旧语言，新产物新语言；不自动回填。

**操作顺序**

1. 修改 `lvdata_mcp.yaml` 的 `language`，重启 lvdata-mcp。
2. 新文件/新事件按新语言分析；旧数据不动。
3. 需要转换旧数据时，再触发带 `force=true` 的 REST 请求（见下）。
4. 检查新 events/day_summary 语言与检索结果；必要时重跑 force。

**force 重分析**

- `audio` / `vision` 现有 `force=True` 已会把文件重置为 pending 并重跑；
  重跑后 events 的 `updated_at` 更新，后续 reindex/embed/day_summary 才会看到新文本。
  注意：只 force audio/video 时，相关日总结也可能因 events 更新被现有 dirty 逻辑
  自动重生成；要保证全量转换请把 `day_summary` 一并放入 force stages。
- `chatlog` 的 force 对语言没有实际变化（原文不变），通常不必单独触发；全链 force 包含它也无害。
- `day_summary` **需要最小扩展**：
  1. `generate(force=True)` 当前只选非 ok 行；改为选择 `day_summary` 中所有
     `(timezone, local_date)` 行（ok + 非 ok）。
  2. `_generate_one(..., force=True)` 跳过 `up_to_date` 护栏。
  3. 非 ok 行仍按现有逻辑重置 `fail_count/generated_at`；ok 行在生成成功后 upsert 覆盖。
  4. `target/session_id` 对 day_summary 仍不生效（现状：全局）；force 也是全局。
- 推荐最小转换命令（REST，同 token）：

```json
POST /api/analysis/trigger
{"stages": ["audio", "video", "reindex", "embed", "day_summary"], "force": true}
```

- `reindex`/`embed` 必须包含，否则 FTS/向量仍是旧 summary；`day_stats` 无 LLM，
  不需要为了语言单独 force。
- 如果只想转换日总结（不重跑音频/画面），可只 `{"stages":["day_summary"],"force":true}`；
  输入仍是旧语言的 events 摘要，但输出是新语言。
- `session_id` 仅对 audio/video/chatlog 的 force 作用域有效；day_summary 仍全局。

**边界**：旧日如果在配置切换后收到新事件，日总结会自然重生成（当前 dirty 逻辑），
即使没有 force；这是“数据更新”而非“配置变化触发重分析”。

### 4.6 检索与下游

- `search`/`semantic_search` 的文本源是 `events.summary`：新生成字段为目标语言；
  `transcript`/`ocr.text`/chat 原文保持原语言，因此原文关键词仍可被检索。
- `events.updated_at` 变化后，现有 `reindex`/`embed` 会重建 FTS/向量；
  force 转换须显式带上这两个 stage。
- `day_summary` 不进入 MCP `search`；由 BFF/App 读取，force 后为目标语言。
- 既有 FTS 的 CJK bigram / 音频静音词数限制**不在本设计修复范围**；它们会影响
  非中文体验，但输出语言配置不会自动改变这些行为。

### 4.7 兼容与边界

- 默认 `en` 是有意的行为变化：未配置 language 的部署，新分析产物从中文变英文；
  旧数据保持原语言。要维持中文需显式配置 `language: zh-Hans`。
- 单实例只支持一种语言；不同设备/不同 App 语言无法同时满足。
- 不新增语言元数据；旧/新语言混合时无法在 DB 层面区分（本设计接受）。
- MCP/REST 文案统一英文（D9）；分析 prompt 模板仍为中文 + 目标语言指令（D11）。
- 不修改音频静音过滤、FTS 分词、embedding 模型/阈值；这些是独立问题。
- 不支持 per-request 翻译：`search` 查询语言可任意，但不会因为查询语言改变已存产物语言。

### 4.8 MCP/REST 文案英文化（D9）

**范围（统一英文，只使用英文）**

- `server.py` 的 MCP `INSTRUCTIONS`。
- `tools.py` 5 个 tool 的 `description`、error、hint。
- `store.py` 的 `StoreError` message/hint。
- `timeutil.py` 的日期窗/日期格式 `ValueError` 消息（经 tools 作为 invalid_param 返回）。
- `server.py` 的 `/api/*`、auth 错误/hint。
- `schemas.py` 自定义 pydantic validator 消息（内建消息本身已是英文）。
- `config.py` / `vec.py` 的启动/配置错误消息（环境变量/配置文件/embedding key 缺失等）。
- `main.py` 的 CLI 错误 print（DB 不存在、未配置 embedding 等）；CLI help/成功日志不改。

**不在范围**：分析 prompt 模板（保持中文 + 目标语言指令）、运行日志、CLI help、
README 正文。原数据值（chat/transcript/OCR/events）可以为任意语言，不属于“文案”；
测试只断言静态 MCP/REST 文案无中文。README 中引用的 `instructions`/tool 描述
需同步英文（见“相关文档”）。

**`INSTRUCTIONS`（英文，已落库）**

```text
LivaChat's recorded multimodal lifelog data. Read-only.
Locate with get_recent_events / get_timeline; search with search (fast, preferred) /
semantic_search (alternative wording); fetch details with get_event by event_ref.
Conventions: start_date/end_date are YYYY-MM-DD inclusive; timezone is the timezone of
the current query; list/search results are lightweight projections; fetch details and
keyframe images with get_event by event_ref; failures return error{code,message}
(do not rely only on isError).
```

**Tool descriptions（英文要点）**

| Tool | English description |
|---|---|
| `search` | Cross-modal full-text search over chat text, audio transcripts, visual captions/OCR, and agent job results. Narrow by date window and type; hits include event_ref for get_event. |
| `semantic_search` | Semantic retrieval (FTS + vectors, RRF fusion). Hits include via, vec_score, fts_score; details via get_event. with_context=true adds ±2min context events. Returns embedding_disabled when not configured and embedding_failed on API failure (no silent fallback); FTS failure degrades to vector-only. |
| `get_timeline` | Unified multimodal timeline (events view): grouped by day (ascending; ascending within day); cursor is the [ts_ms, type, event_id] keyset, and pagination may split a day across pages. |
| `get_event` | Full detail for one event_ref ({session_id}:{type}:{source_id}); video events with a keyframe include image content. |
| `get_recent_events` | Event list for the last N local days (days=1 = today, max 31): grouped by day descending, ascending within day; truncated=true means the list was limited by page size (total is the window total). |

**Error/hint**

- 保留 `error.code` 的 snake_case 英文值（`invalid_param`、`not_found`、
  `embedding_disabled` 等），只翻译 message/hint。
- 典型翻译：`Query is empty or contains only stopwords (nothing to search)`、
  `Query contains no valid search terms (separators/punctuation only)`、
  `search_docs index is not ready` / `Run lvdata-mcp reindex or wait for the watcher`、
  `Missing or invalid Bearer token`、`Request body must be a JSON object`、
  `Watcher is not running`、`Session not found: ...`、`Invalid cursor: ...`、
  `event_ref must be in the form {session_id}:{type}:{source_id}`。
- 自定义 validator 消息翻译为英文；pydantic 内建消息保持原样。
- 测试扫描 `INSTRUCTIONS`、tool descriptions、已知错误的 message/hint，断言无 CJK；
  不扫描数据值（事件文本/OCR 可以为中文）。

**相关文档**

- `docs/data/lvdata_mcp_tools.md` §14.2（instructions 原文）与 D2 已同步为英文。
- `README.md` 的 MCP 使用示例/工具表已标明英文 instructions 与 `language` 配置。

### 4.9 决策（已定）

| # | 决策 | 口径 |
|---|---|---|
| D1 | 非法 `language` | 空/非法/未知归一为 `en` 并记录 warning；缺省 `en`，不 fail-fast |
| D2 | 配置位置 | 顶层单值 `language`；不加 per-provider/per-stage 覆盖 |
| D3 | 目标字段 | 仅生成的用户可见自然语言字段；`transcript`/`ocr.text`/chat 原文不翻译 |
| D4 | 旧数据 | 配置变化不触发重分析；只有 force 重跑；允许新旧语言混合 |
| D5 | `topics[].search_words` | 保持事件摘要原文连续片段，不翻译（功能约束） |
| D6 | `day_summary` force | 扩展现有 force 到 ok 行并绕过 `up_to_date`；全局作用域 |
| D7 | 旧日新事件 | 日总结可因新事件自然重生成（现有 dirty 语义），不视为配置触发重分析 |
| D8 | 元数据/schema | 不新增语言列/字段；零 DB/协议变更 |
| D9 | MCP/REST 文案 | instructions/tool 描述/error/hint 全部英文，只使用英文；error.code 不变 |
| D10 | 检索 | 不翻译查询；目标语言化后重建 FTS/向量；原文保留可检索 |
| D11 | prompt 方式 | 在现有中文 prompt 末尾追加目标语言指令，不翻译 prompt 模板 |
| D12 | 音频 `language` | 继续表示源语言检测码；不改为目标语言 |

---

## 5. 变更清单（已实施）

| 文件 | 改动 |
|---|---|
| `configs/dev/lvdata_mcp.yaml` / `configs/test/lvdata_mcp.yaml` | 顶层新增 `language: en` + 16 code 注释 |
| `tools/lvdata-mcp/src/lvdata_mcp/config.py` | `SUPPORTED_LANGUAGES`、`language` 字段（缺省/空/非法 → en + warning）、`effective_summary` |
| `tools/lvdata-mcp/src/lvdata_mcp/language.py`（新） | 16 语言名表 + `output_language_rule()` |
| `tools/lvdata-mcp/src/lvdata_mcp/analysis/audio.py` | `AUDIO_SYSTEM_PROMPT` 追加目标语言指令；保留 transcript/源语言 |
| `tools/lvdata-mcp/src/lvdata_mcp/analysis/vision.py` | filter/extract prompt 追加目标语言指令；OCR 保留原文 |
| `tools/lvdata-mcp/src/lvdata_mcp/summary/day.py` | day prompt 追加目标语言；`search_words` 原文约束；force 覆盖 ok 行 + 跳过 up_to_date |
| `tools/lvdata-mcp/src/lvdata_mcp/server.py` | `INSTRUCTIONS` 改英文；REST/auth error/hint 改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/tools.py` | 5 个 tool descriptions + error/hint 改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/store.py` | `StoreError` message/hint 改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/timeutil.py` | 日期窗/日期格式 ValueError 消息改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/schemas.py` | 自定义 validator 消息改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/config.py` | 启动/配置错误消息改英文 |
| `tools/lvdata-mcp/src/lvdata_mcp/vec.py` | 启动错误消息改英文（embedding key 缺失） |
| `tools/lvdata-mcp/src/lvdata_mcp/main.py` | CLI 错误 print 改英文（help/成功日志不改） |
| `tools/lvdata-mcp/README.md` / `docs/data/lvdata_mcp_service_v2.md` | 配置说明、language 字段与行为边界 |
| `docs/data/lvdata_mcp_tools.md` | §14.2 instructions/D2 改英文；中文-only 口径被 D9 取代 |
| `tools/lvdata-mcp/tests/test_language.py`（新） | 16 code 目标 prompt；transcript/OCR/search_words 原文约束 |
| `tools/lvdata-mcp/tests/test_config.py` | language 缺省/空/非法/null → en + warning；effective_summary |
| `tools/lvdata-mcp/tests/test_tools.py` | instructions/descriptions/error 英文断言 |
| `tools/lvdata-mcp/tests/test_day_summary.py` | force 覆盖 ok 行 + 绕过 up_to_date |
| 测试汇总 | 见 §6（384 passed） |

不改 DB schema、MCP tool schema、REST 入参；错误 `code` 与 JSON 字段名保持不变。

---

## 6. 测试计划

### 6.1 配置单测

- `LvdataMcpConfig()` → `language == "en"`（缺省不告警）。
- 16 个 code 全部接受；`tr`、`zh`、空串、YAML null、`en-US` 等空/非法/未知值
  → `en` + warning（按 D1；含 `mode="before"` null 用例）。
- `LVDATA_MCP__LANGUAGE=ja` 生效；`effective_summary()` 含 `language`。
- yaml 注释/README 与代码 16 项列表一致（固定断言）。

### 6.2 MCP/REST 英文文案单测

- `INSTRUCTIONS`、5 个 tool descriptions 断言无 CJK，且包含英文用法要点。
- 已知错误（空查询/索引未就绪/未配置 embedding/无效 cursor/无效 event_ref/
  非法日期窗/auth 失败/非法请求体）的 message/hint 断言无 CJK；`error.code` 保持原值。
- 自定义 pydantic validator 消息断言英文；内建消息不改变。
- `config.py` / `vec.py` / `main.py` 启动或 CLI 错误消息断言英文
  （env missing / config missing / DB missing 等）。
- 数据值（events summary/OCR/transcript）可以为中文，不属于该断言范围。
- 更新现有 `test_tools.py::test_initialize_metadata` 的中文字面量；tool 描述/错误
  断言同步英文。
- `docs/data/lvdata_mcp_tools.md` §14.2/D2 与代码 `INSTRUCTIONS` 逐字一致（英文）。

### 6.3 prompt 单测

- 音频/画面 filter/画面 extract/日总结 prompt 均包含 `English (en)` 或目标 `name (code)`。
- 原文/枚举约束存在：`transcript`、`ocr[].text`、`value`/`quality`/`emotion`、JSON 字段名。
- 日总结 prompt 明确 `search_words` 保留原文连续片段。
- 16 个 code 分别构造 prompt，均含对应语言名/code；无模板遗漏。

### 6.4 集成测试（fake LLM）

- pending 音频/画面文件 + `language=ja`：捕获系统 prompt 含 `Japanese (ja)`；
  fake 返回目标字段；`transcript`/OCR 原值不被改写。
- `day_summary.generate()` + `language=zh-Hans`：捕获 prompt 含
  `Simplified Chinese (zh-Hans)`；写入的 doc 使用 fake 返回语言；`search_words` 不被改写。
- 旧数据：
  - 预置 `analysis_status='ok'` 文件 + ok `day_summary`；普通 process/generate
    不产生重分析/LLM 调用（updated_at/状态不变）。
  - 修改 `language` 后普通 process/generate 仍不重跑旧数据。
  - `audio.force=True` / `vision.force=True` 重置并重跑；
    `day_summary.force=True` 覆盖 ok 行，且 `_generate_one` 不再被 `up_to_date` 跳过。
- force 后 `reindex`/`embed` 能反映新 summary；原文关键词仍可检索。

### 6.5 真实 LLM 探针（实施后）

- 至少覆盖 `en` / `ja` / `ar` / `th`（音视频使用现有测试素材）：
  - 音频 summary/objects/people.description 目标语言；transcript 原语言；
  - 画面 caption/place/objects/people.description 目标语言；OCR 原文字；
  - 日总结 summary/moments/topic 目标语言；`search_words` 可在原文事件中命中。
- 记录原始输出；脚本级语言检查只作辅助，人工复核。
- **发布门槛**：目标字段稳定漂移/译文混入原文时先调 prompt，不静默接受。

### 6.6 实施结果

- `tools/lvdata-mcp/tests`：384 passed（2026-10-28）。
- 配置回落、目标语言 prompt、MCP/REST 英文静态文案、force 覆盖 ok 行/绕过
  `up_to_date` 均已覆盖。
- 真实 LLM 探针（§6.5）尚未执行；不阻塞代码落地，但发布前需按该节验证目标字段语言。

---

## 7. 风险与待确认

| 风险/待确认 | 说明 |
|---|---|
| prompt 遵从度 | Qwen 是否稳定遵守目标语言；需真实探针；无服务器兜底 |
| 旧数据混合 | 配置切换后旧 events 仍旧语言；BFF/App 可能同时看到两种语言 |
| day_summary 语义 | 旧日新事件会触发重生成（D7 已接受） |
| 检索体验 | 目标语言化后，原文语言的 caption/place 检索命中可能下降；transcript/OCR 仍保留 |
| 音频静音过滤 | 非汉字/非拉丁脚本仍可能被 `is_silent` 丢弃；本设计不修复 |
| FTS 分词 | 日文假名/泰文等仍弱；本设计不修复 |
| MCP/REST 英文化 | 需全量扫描静态文案（instructions/descriptions/error/hint），避免遗漏中文；数据值可中文 |
| 配置非法值 | 已定空/非法/未知回落 `en` + warning；需确保生效值与 warning 可见（D1） |
| force 成本 | 重跑旧音频/画面/日总结会消耗 LLM 调用；按需分 stage / session 执行 |
| force 作用域 | day_summary 全局 force；是否需要按 session/日期 scope 另行设计 |

---

## 8. 非目标

- 不做 per-device/per-session/per-event 多语言并存。
- 不翻译 `transcript`、`ocr[].text`、chat/transcribe/translate/agent_job 原文。
- MCP/REST 文案统一英文，不提供中文/双语版本（D9）；数据值不在此限。
- 不翻译分析 prompt 模板（保持中文 + 目标语言指令）；不翻译 CLI/README/日志。
- 不修复音频静音过滤、FTS 分词、embedding 阈值等既有语言问题。
- 不新增 DB schema、MCP/REST 入参、语言筛选。
- 不做服务器翻译/后处理；不使用 ASR/音频检测语言作为输出语言。
- 不改旧数据，除非 force。
