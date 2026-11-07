# LvData Audio Analysis Tool Design

> **Phase 2 音频分析** — 基于阿里云百炼 Qwen3.5-Omni-Plus 全模态模型，对 lvdata 中的 OGG Opus 音频片段进行结构化分析，提取转写文字、背景声音、事件、情绪、物体、人物等信息。所有 AI 生成的衍生数据统一落盘到 `<root_dir>/derived/<device_uuid>/<session_id>/...`。

> **修订裁定（2026-09-16，音频 schema 两轮调整）**：本文描述的
> `lvdata_analyze_audio.py` 为 v1 CLI（已被 lvdata-mcp 取代、不再更新），
> **§2.3 schema 表与 §6 prompt 的字段描述为 v1 存量**。运行时 schema 已按
> [audio_analysis.md](../audio_analysis.md) 调整：删 `background_sounds`/
> `events`/`quality`/`place`、`objects` 去 `time_s`、新增 `value`（low|high
> 总体价值评估）、`people` 改统计对象（count/male_count/female_count/
> description）；静音判定重定义为「transcript 词数 > 5（CJK 感知）或
> value=high 才落行」（objects/people/emotions 不参与）。对照本文档了解当前
> 行为时请以该文档为准。

---

## 1. Overview

`lvdata_analyze_audio.py` 是 Phase 2 的 Python CLI 工具。它读取 Phase 1 `lvdata_inventory.py` 建立的 SQLite 索引，筛选待分析的音频文件，将每个 OGG Opus 片段转换为 WAV 后调用百炼 `qwen3.5-omni-plus` 模型做多模态音频理解（ASR + 场景感知），输出结构化 JSON 衍生数据，并回写 inventory DB 的分析状态。

### 为什么选 Qwen3.5-Omni-Plus

| 维度 | 说明 |
|------|------|
| 能力 | 全模态大模型，一个调用同时完成转写 + 背景声音/事件/情绪/物体/人物提取，无需多模型拼接 |
| 格式 | OpenAI 兼容 API（`/compatible-mode/v1`），Python SDK 直接可用 |
| 音频限制 | 内容分析场景单请求最长 3 小时音频、最大 2GB（远超本项目 60s/段） |
| 结构化输出 | 支持 `response_format: {"type": "json_object"}` |
| 情感识别 | 转写过程中支持情感识别 |
| 语言 | 支持 113 种语言及方言 |
| 成本 | 输入约 ¥0.8/百万 tokens 量级（详见 §8 成本估算） |

> 参考：模型于 2026-03-30 发布，官方定位为 GPT-5.5 / Gemini 3.1 Pro 的对位替代。

### 设计原则

- **单一事实来源** — 衍生数据以 `root_dir/derived/` 下的 JSON 文件为准，inventory DB 只存索引与状态
- **幂等可恢复** — 已成功分析的片段自动跳过，中断后重跑只处理剩余部分；`--force` 强制重分析
- **容错优先** — 单片段失败不中断整体，记录错误后继续
- **费用可控** — 默认每片段单次调用（仅文本输出），可选 session 级汇总调用；pilot 后可依据 usage 统计校准成本

---

## 2. 目录结构与衍生数据布局

### 2.1 源数据目录（Phase 1 约定，只读）

```
<root_dir>/                          # 默认 /opt/lvdata
  <device_uuid>/
    <session_id>/
      session.json
      audio/<uuidv7>.ogg            # OGG Opus 48kHz, 60s segments
      video/<uuidv7>.jpg
      chat/history.jsonl
```

### 2.2 衍生数据目录（Phase 2 产出，只写）

所有 AI 生成的衍生数据统一放在 `<root_dir>/derived/` 下，目录层级镜像源数据：

```
<root_dir>/derived/
  <device_uuid>/                     # xxx
    <session_id>/                    # xxx
      audio/                         # xxx —— 每个音频片段一个分析结果
        <segment_uuidv7>.json       # 结构化分析结果（本工具主产出）
        <segment_uuidv7>.txt        # 纯转写文本（快速浏览用，内容为 json 中 transcript 字段）
      summary.json                   # session 级聚合（全部片段完成后生成）
```

- 衍生 JSON 中携带 `ts_ms` / `ts_utc`（来自文件名 UUIDv7 时间戳），Phase 4 对齐工具可直接消费，无需再解析文件名
- `derived/` 与源数据同 root，随数据一起打包迁移
- WAV 中间文件为临时文件（运行后清理），不落盘到 derived（见 §6.3）

### 2.3 单个片段分析结果 JSON Schema

```json
{
  "schema_version": 1,
  "segment_id": "019f92f2-5cad-7490-8000-1914e1d8b908",
  "session_id": "019f92f2-5b73-7886-8000-576e09d3f328",
  "device_uuid": "b53985c7-cb7f-4382-95c9-18d33a9bc36a",
  "ts_ms": 1784876784813,
  "ts_utc": "2026-07-24T07:06:24.813Z",
  "model": "qwen3.5-omni-plus",
  "analyzed_at": "2026-08-01T10:00:00+00:00",
  "duration_s": 60.0,

  "language": "zh",
  "transcript": "完整转写文本，保留口语原样",
  "summary": "一句话总结该片段内容",

  "background_sounds": [
    {"type": "music",   "description": "轻音乐背景", "time_s": 0},
    {"type": "noise",   "description": "键盘敲击声", "time_s": null}
  ],
  "events": [
    {"type": "laughter", "description": "用户笑了一声", "time_s": 12.5},
    {"type": "interruption", "description": "对话被打断", "time_s": null}
  ],
  "emotions": [
    {"emotion": "happy",  "confidence": 0.9, "evidence": "笑声和语气上扬"},
    {"emotion": "neutral","confidence": 0.7, "evidence": "正常陈述语气"}
  ],
  "objects": [
    {"name": "macbook", "description": "提及正在使用的电脑", "time_s": null}
  ],
  "people": [
    {"name": null, "role": "user", "gender": "male",
     "description": "主要说话者", "voice_characteristics": "成年男性，普通话"}
  ],

  "quality": {"noise_level": "low", "issues": []}
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `transcript` | 转写文字（用户需求：文字） |
| `background_sounds` | 背景声音，`type` 枚举：`music / noise / speech / silence / other` |
| `events` | 事件，`type` 枚举：`laughter / cough / interruption / question / acknowledgment / other` |
| `emotions` | 情绪，`emotion` 枚举：`happy / sad / angry / surprised / frustrated / neutral / other`，含置信度与依据 |
| `objects` | 听到或提及的物体 |
| `people` | 人物（设备端单人场景下即用户本人）；`name` 未知时为 null |
| `time_s` | 片段内秒级时间，**模型尽力而为的近似值**，非词级对齐（见 §9 限制） |

### 2.4 Session 级聚合 summary.json

全部片段分析成功后，将各片段转写与提取结果合并做一次文本-only 模型调用，输出：

```json
{
  "schema_version": 1,
  "session_id": "019f92f2-5b73-7886-8000-576e09d3f328",
  "segments_analyzed": 6,
  "total_audio_s": 360,
  "model": "qwen3.5-omni-plus",
  "analyzed_at": "...",
  "transcript": "整段会话拼接转写（按片段 ts 排序）",
  "timeline": [
    {"ts_ms": 1784876784813, "type": "audio", "segment_id": "...", "summary": "片段一句话总结"},
    {"ts_ms": 1784876828272, "type": "chat",  "role": "assistant", "text": "Hi there!"}
  ],
  "events":        [{"time_s": 12.5, "type": "laughter", "description": "..."}],
  "emotions_dist": {"happy": 3, "neutral": 2, "frustrated": 1},
  "people":        [{"role": "user", "gender": "male", "description": "..."}],
  "objects":       [{"name": "macbook", "description": "..."}],
  "session_summary": "整段会话的一句话总结"
}
```

`timeline` 中同时并入 `chat_messages`（查 DB），为 Phase 4 多模态对齐预生成时间线骨架。

---

## 3. 数据模型（inventory DB 扩展）

沿用 Phase 1 的 SQLite DB（`--db-path` 默认 `/opt/lvdata/lvdata_inventory.db`）。本工具通过 `CREATE TABLE IF NOT EXISTS` 增加分析状态表，不动 Phase 1 的表结构（保证旧库兼容）。

### 3.1 新增表 audio_analyses

```sql
CREATE TABLE IF NOT EXISTS audio_analyses (
    analysis_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id            INTEGER NOT NULL REFERENCES files(file_id),
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    segment_id         TEXT NOT NULL,              -- UUIDv7 文件名（不含扩展名）
    derived_path       TEXT NOT NULL,              -- 衍生 JSON 绝对路径
    model              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | failed
    transcript         TEXT,                        -- 冗余存一份，方便快速查询
    lang               TEXT,
    usage_input_tokens  INTEGER,                    -- 来自响应 usage（校准成本用）
    usage_output_tokens INTEGER,
    latency_ms          INTEGER,
    error              TEXT,
    started_at         TEXT,
    finished_at        TEXT,
    UNIQUE(file_id)
);
CREATE INDEX IF NOT EXISTS idx_aa_session ON audio_analyses(session_id);
CREATE INDEX IF NOT EXISTS idx_aa_status   ON audio_analyses(status);
```

### 3.2 sessions 表状态流转

| 状态 | 触发 |
|------|------|
| `pending` | Phase 1 初始值 |
| `in_progress` | 该 session 首个片段开始分析时 |
| `completed` | 该 session 全部 audio 文件均 `status='ok'`（或全部失败但无未处理）时，并写 `analyzed_at` |

> 这回答了 Phase 1 设计文档 Open Questions #3：**由下游工具自行更新 DB 状态**，inventory 工具保持只做索引。

### 3.3 待分析片段查询

```sql
-- 所有未成功分析的音频文件（排除已 ok 的）
SELECT f.file_id, f.file_path, f.file_size, f.uuid_ts_ms,
       s.session_id, s.device_uuid, s.session_date
FROM files f
JOIN sessions s ON f.session_id = s.session_id
WHERE f.file_type = 'audio'
  AND s.status IN ('active', 'finalized')
  AND NOT EXISTS (
      SELECT 1 FROM audio_analyses a
      WHERE a.file_id = f.file_id AND a.status = 'ok'
  )
ORDER BY s.session_date, f.uuid_ts_ms;
```

---

## 4. CLI Interface

```
lvdata_analyze_audio.py [OPTIONS]
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--root-dir` | PATH | `/opt/lvdata` | lvdata 根目录（派生目录 `derived/` 在其下） |
| `--db-path` | PATH | `/opt/lvdata/lvdata_inventory.db` | 与 Phase 1 相同的索引库 |
| `--device-uuid` | UUID | (none) | 仅分析指定设备 |
| `--session-id` | UUID | (none) | 仅分析指定 session（可重复传入） |
| `--model` | STR | `qwen3.5-omni-plus` | 百炼模型 ID |
| `--base-url` | URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点（企业 workspace 用 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`） |
| `--api-key` | STR | env `DASHSCOPE_API_KEY` | 百炼 API Key |
| `--concurrency` | INT | `4` | 并发调用数（注意百炼限流，见 §6.6） |
| `--sample-rate` | INT | `16000` | 转 WAV 采样率（源数据原始输入即 16kHz） |
| `--force` | flag | false | 忽略已有分析结果，重新分析 |
| `--skip-convert` | flag | false | 实验性：直接传 OGG 原文件，不转 WAV |
| `--no-session-summary` | flag | false | 跳过 session 级聚合调用（省一次 LLM 调用） |
| `--probe` | flag | false | 每 session 只分析第一个待处理片段后退出（Prompt 质量验证用） |
| `--dry-run` | flag | false | 只列出待分析片段，不发 API 请求 |
| `--verbose` / `-v` | flag | false | 详细输出 |

### 使用示例

```bash
# 环境变量注入 API Key
export DASHSCOPE_API_KEY=sk-...

# 全量分析所有待处理音频
python lvdata_analyze_audio.py

# 先 dry-run 看工作量
python lvdata_analyze_audio.py --dry-run

# 验证 Prompt 效果：每个 session 只跑 1 段
python lvdata_analyze_audio.py --probe -v

# 只分析某个 session，并发 8
python lvdata_analyze_audio.py --session-id 019f92f2-5b73-7886-8000-576e09d3f328 --concurrency 8

# 强制重跑
python lvdata_analyze_audio.py --force
```

---

## 5. 模型调用设计（百炼 Qwen3.5-Omni-Plus）

### 5.1 关键调研结论（已上网确认）

| 结论 | 说明 |
|------|------|
| 模型 ID | `qwen3.5-omni-plus`（2026-03-30 发布，旗舰全模态） |
| 调用方式 | OpenAI 兼容 HTTP，`stream=True` 必须开启；SDK ≥ openai 1.52.0 |
| 音频输入格式 | `input_audio` 块，base64 编码，格式字段支持 `wav`；第三方项目确认 OGG 亦可但官方示例以 wav/mp3 为主 → **默认转 WAV 16kHz mono** |
| base64 前缀 | **Qwen 特有格式 `data:;base64,`**（不是 OpenAI 的 `data:audio/wav;base64,`），来自 AgentScope 集成代码实证 |
| 结构化输出 | `response_format: {"type": "json_object"}` 实测可用（2026-08-02 探针验证，见 §5.5）；且 system/user 消息中**必须包含 "JSON" 关键字** |
| ⚠️ 围栏残留 | 实测模型常在 JSON 末尾多输出一个 markdown 围栏 ` ``` `，**解析前必须剥离**（`probe_qwen_structured_output.py` 已实现 `sanitize()`） |
| 音频限制 | 内容分析单请求最长 3 小时、最大 2GB；本项目每段 60s，远低于限制 |
| 情感识别 | 转写过程中支持 |
| 不支持 | **说话人分离（diarization）**、词级时间戳 |
| 区域 | 北京/新加坡（不同区域 API Key 不同） |

### 5.2 请求结构（每片段一次调用，仅文本输出）

```python
import base64
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=api_key, base_url=base_url)

# OGG 转 WAV 后 base64，前缀用 Qwen 特有的 "data:;base64,"
b64 = base64.b64encode(wav_bytes).decode()

resp = await client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},   # 含 "JSON" 关键字 + Schema 描述
        {"role": "user", "content": [
            {"type": "input_audio",
             "input_audio": {"format": "wav", "data": f"data:;base64,{b64}"}},
            {"type": "text", "text": "请按系统提示的 JSON Schema 分析这段音频。"},
        ]},
    ],
    stream=True,
    stream_options={"include_usage": True},
    response_format={"type": "json_object"},
    modalities=["text"],            # 只取文本，不生成语音输出（省钱）
)

# 流式收集:choices[0].delta.content;末 chunk 有 usage(input/output tokens)
```

### 5.3 System Prompt（草案，JSON Schema 内嵌）

```text
你是专业的音频内容分析引擎。你只输出 JSON，不输出任何其他文本或解释。

分析用户提供的音频，严格按照以下 JSON Schema 输出：

{
  "language": "主要语言代码，如 zh / en",
  "transcript": "完整转写文本，保留口语原样、不修正口误",
  "summary": "一句话总结该片段内容",
  "background_sounds": [{"type": "music|noise|speech|silence|other",
                         "description": "描述", "time_s": "片段内秒数或 null"}],
  "events": [{"type": "laughter|cough|interruption|question|acknowledgment|other",
              "description": "描述", "time_s": "秒数或 null"}],
  "emotions": [{"emotion": "happy|sad|angry|surprised|frustrated|neutral|other",
                "confidence": 0.0-1.0, "evidence": "依据，如笑声、语速"}],
  "objects": [{"name": "物体名", "description": "描述", "time_s": "秒数或 null"}],
  "people": [{"name": "若可辨识则填，否则 null", "role": "user|other",
              "gender": "male|female|null", "description": "描述",
              "voice_characteristics": "音色特征"}],
  "quality": {"noise_level": "low|medium|high", "issues": ["问题列表"]}
}

要求：
1. transcript 必须完整转写全部语音内容，不要遗漏。
2. 每个数组字段可以为空数组，但字段必须存在。
3. 若时间信息不确定，time_s 填 null，不要编造。
4. 只输出 JSON，不要使用 Markdown 代码块包裹。
```

### 5.4 Session 级聚合调用（文本-only）

把各片段 `transcript + summary + events` 按时间顺序拼接为文本，连同 chat 消息一起发给模型，prompt 要求输出 §2.4 的 summary.json Schema（同样要求输出纯 JSON）。

### 5.5 实测验证记录（2026-08-02）

`tools/data-analysis/probe_qwen_structured_output.py` 对 `qwen3.5-omni-plus` 做了两组实测：

| 测试 | 输入 | 结果 |
|------|------|------|
| 文本 + `json_object` | "今天天气怎么样？" | 返回纯 JSON（`answer`/`confidence` 字段），usage in=69/out=37 |
| 音频 + `json_object` | 真实 60s OGG 段（转 WAV 16k） | 返回完整 JSON（`language`/`transcript`/`background_sounds`/`emotions`/`summary`），usage in=478/out=94 |

**结论：结构化输出可用。** 发现两个实现要点：

1. **markdown 围栏残留** — 模型在 JSON 末尾附加 ``` ` ```，直接 `json.loads` 失败（`Extra data`）。工具必须在解析前剥离首尾围栏。
2. **空值用 null 而非空串** — 如 `"transcript": null`。字段校验应把 `null` 归一化为空串/空数组。

另：对低质量音频（呼吸/鼾声）模型识别为 "unintelligible"，语言检测不可靠——正常语音段影响有限，但提示质量差的片段 transcript 可能为空。

---

## 6. Processing Logic

### 6.1 主流程

```
main()
  ├── parse_args()
  ├── connect_db()                              # 同 inventory DB
  ├── ensure_schema()                           # 新增 audio_analyses 表
  ├── select_pending_files()                    # §3.3 SQL
  │     └── --dry-run: 列出后直接退出
  ├── check_ffmpeg()                            # 缺失则报错退出（--skip-convert 时跳过）
  ├── 按 session 分组
  │     └── for each session:
  │           ├── mark_session('in_progress')
  │           ├── for each segment (asyncio.Semaphore(concurrency)):
  │           │     ├── ogg → wav               # ffmpeg, 16kHz mono, 临时目录
  │           │     ├── call_qwen_omni()        # 流式 + 重试
  │           │     ├── parse_and_validate()    # JSON 解析 + 字段校验，失败重试 1 次
  │           │     ├── write_derived()         # derived/<dev>/<session>/audio/<seg>.json + .txt
  │           │     └── upsert_analysis()       # audio_analyses 行 (ok/failed + usage)
  │           ├── if all ok and 未 --no-session-summary:
  │           │     ├── call_session_summary()  # 文本-only 聚合
  │           │     └── write summary.json
  │           └── mark_session('completed'/'failed', analyzed_at)
  └── print_summary()                           # 各 session 成功/失败/耗时/token 用量
```

### 6.2 幂等与恢复

- 重跑时 `SELECT` 排除已有 `status='ok'` 的 file_id → 中断后重跑只处理剩余
- `--force`：删除对应 `audio_analyses` 行并重新分析（衍生 JSON 覆盖写）
- 单片段 3 次失败（API 错误 + 重试）→ 标记 `failed` 记 error，继续下一段；结尾汇总列出失败清单，可单独重跑

### 6.3 WAV 转换

```bash
ffmpeg -y -i <segment>.ogg -ar 16000 -ac 1 -f wav <segment>.wav
```

- 输出到 `tempfile.mkdtemp()`，运行结束清理（每段转换 <1s，不缓存）
- `--skip-convert`：直接以 OGG 原文件 base64 上传（实验性，依赖服务端格式支持；若 400 错误会自动降级为转 WAV 重试）

### 6.4 输出写入

- 目录：`<root_dir>/derived/<device_uuid>/<session_id>/audio/`，`os.makedirs(exist_ok=True)`
- 原子写：先写 `*.json.tmp` 再 `os.replace()`，避免中断产生半截文件
- `.txt` 内容 = JSON 的 `transcript` 字段

### 6.5 容错与校验

- 空音频 / 纯静音 → 模型返回空 transcript，标记 ok（transcript=""），不重试
- JSON 解析失败 / Schema 字段缺失 → 重试 1 次（同音频）；仍失败标记 failed
- ffmpeg 失败、文件损坏 → 标记 failed 继续
- 单个 session 整体失败不影响其他 session

### 6.6 限流与重试

- `asyncio.Semaphore` 默认并发 4（百炼新模型区域有并发配额，超限返回 429）
- 重试策略：`429 / 5xx` → 指数退避 + 抖动（1s, 2s, 4s, 8s），最多 4 次；`400`（参数错误）不重试，直接 failed
- 单次请求超时 300s（含流式接收）
- 每次调用记录 `latency_ms` 与 `usage`，`--verbose` 下实时打印

---

## 7. File Layout

```
tools/data-analysis/
  lvdata_inventory.py          # Phase 1: 索引（已完成）
  lvdata_analyze_audio.py      # Phase 2: 本设计，待实现
  lvdata_analyze_video.py      # Phase 3: 图像分析（future）
  lvdata_summary.py            # Phase 4-5: 跨 session 报告（future）
```

与 Phase 1 共享 `--root-dir` / `--db-path` 默认值，天然衔接。

---

## 8. 成本估算

| 项 | 估算 |
|----|------|
| 数据量 | 3 sessions，59 个音频片段，每段 60s |
| 每片段输入 | **实测 ~478 tokens**（60s WAV 16kHz mono，2026-08-02 探针）≈ 8 tokens/s，另加 prompt 文本 |
| 每片段输出 | 结构化 JSON ≈ 100-200 tokens（实测） |
| 单价 | 输入 ¥0.8/百万 tokens 量级（官方公布） |
| 总量级 | 59 段 × ~600 tokens ≈ 3.6 万 tokens → **预计 < ¥0.1**，成本可忽略 |
| 汇总调用 | 每 session 1 次文本-only 调用，可忽略 |

> 实测数据来自 `tools/data-analysis/probe_qwen_structured_output.py`（文本测试 in=69/out=37；音频测试 in=478/out=94/181）。工具已设计 `usage_input/output_tokens` 落库，全量跑完可精确核算。

---

## 9. 已知限制与 Open Questions

1. **无词级时间戳** — Omni 转写不带词级对齐，`time_s` 为模型近似值。当前对齐粒度 = 60s segment（文件名 UUIDv7 时间戳），满足 Phase 4 时间线重建；若需更细粒度，后续可加固定长度分块（如 qwen-asr 的 `--chunk-sec` 思路）。

2. **不支持说话人分离** — 官方明确不支持 diarization。当前数据为设备端单人录音，影响低；多说话人场景需外接 diarization 服务。

3. **OGG 直传支持待验证** — 第三方集成（qwen-omni-mcp）确认 `format: "ogg"` 可行，但官方文档示例仅 wav/mp3。默认转 WAV 稳妥，`--skip-convert` 留作实验。

4. **隐私合规** — 音频含用户真实语音，将上传至阿里云处理。设计保留 Whisper 本地转写作为未来 fallback（`--asr-backend local`），当前 Phase 2 仅实现 omni 后端。

5. **session 级汇总调用价值待验证** — 默认开启，pilot 时对比 `--no-session-summary` 的本地拼接效果再决定默认值。

6. **audio_analyses 表与 files 表的外键** — 若用户执行 `--full-scan` 重建 files 表（INSERT OR REPLACE 不删行），file_id 稳定；但若手动删除 files 行，audio_analyses 会留下悬空外键（SQLite 默认不校验）。当前可接受，后续可加 `ON DELETE CASCADE`。

---

## 10. Dependencies

```python
# Python
openai>=1.52.0        # AsyncOpenAI 客户端
# 系统
ffmpeg                # OGG Opus → WAV 16kHz mono
# 其余 stdlib: sqlite3, json, argparse, asyncio, base64, tempfile, os
```

---

## 11. 调研参考

- 全模态模型文档（百炼）：<https://help.aliyun.com/zh/model-studio/omni/>
- Qwen-Omni 非实时调用（英文）：<https://help.aliyun.com/en/model-studio/qwen-omni>
- 结构化输出（json_object）：<https://help.aliyun.com/en/model-studio/qwen-structured-output>
- Qwen input_audio `data:;base64,` 前缀实证：<https://github.com/sommio/qwen-omni-mcp>
- 模型发布新闻（2026-03-30）：<https://news.qq.com/rain/a/20260330A08RMW00>

---

*Design version: 1.0 | Created: 2026-08-01*
