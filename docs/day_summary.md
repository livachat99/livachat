# day_summary prompt 改版方案（待 review）

> 状态：**已实施（2026-09-19）**——代码/测试/规格文档已按本文落地；仅剩 §7 全量重跑（用户操作）。
> **现行口径以 §3.2 为准**：`topics` 自 **2026-09-21** 起是**对象数组 `[{topic, search_words}]`**（设计见 docs/android_timeline_topics.md §10，查询串安全见 docs/bff_search_fts.md §10.3）；§13–§15 等历史章节里的「字符串数组」描述是当时状态，不反映现状。
> 角色：**变更提案**，非 schema 权威——day_summary JSON 的权威定义在 `docs/data/lvdata_mcp_service_v2.md §5.4.1`，落地后以该文档为准。
> 命名提示：本仓方案文档惯例为 `*_plan.md`（`audio_analysis_plan.md`、`video_analysis_plan.md`、`session_json_refactor_plan.md` 等），如需对齐可改名。
> 修订：2026-09-19 按 review 意见修订（P0×2、P1×6）；同日二次调整——`summary` 去掉「最重要的事件或时刻：」标题行，整体总结后直接列举条目；同日三次调整——LLM 输入行标签优化（§11，已实施）；§12 多设备输入分段（B 计划，已实施）；同日四次调整——summary 事件条数上限 5 → 10；§13 结构化输出改版（summary / events / topics，已实施）；§14 summary/events 解耦（已实施）；§15 命名修订 doc.events → doc.moments（已实施）；§12.7 真机全量验证记录（prompt-only 条目合规 3/4，支撑 §13）。

> **2026-09-28 注（I-4）**：本文多处把 `get_day_summary` 当作**活的对外 MCP 接口**讨论（§… 含
> "repo 外消费者无法静态证伪"之类边界声明）——该工具已于 **2026-09-27 删除**，对外只余 5 个只读
> 工具（见 `docs/data/lvdata_mcp_tools.md`）。**day_summary 表与生成链不受影响**（BFF 日卡继续直读
> 该表），故本文的生成/口径讨论仍然有效，仅"对外接口"相关结论失效。

> 涉及：`tools/lvdata-mcp/src/lvdata_mcp/summary/day.py`、相关测试、规格文档 `docs/data/lvdata_mcp_service_v2.md`

## 1. 需求

1. 从 LLM 输出要求中**移除 `stats` / `highlights` / `mood`**；
2. **`timezone`、`local_date` 作为输入**传给 LLM；
3. `summary` 单字段承载：**整体总结（2-3 句）+ 最重要的事件或时刻（每条一句）**。

已拍板（本次会话确认）：

- 需求 3 取"单字段合并"形态：`summary` 为一个文本字段，先整体总结，再列最重要事件/时刻；输出 JSON 仅 `{summary, topics}`；
- 落库 doc **保留本地计算的 stats**（不来自 LLM、不花 token）；
- 历史数据全量重跑**不改 force 语义**：开发/测试期通过手工 SQL 将目标行置 `pending`，由正常增量路径分批重生成（见 §7）。

## 2. 现状证据

> 行号说明：本节为**改动前快照**（行号为当时位置）；行号随编辑漂移，**以符号名为准**。当前关键锚点——`REQUIRED_KEYS` 48、`DAY_SYSTEM_PROMPT` 116-133、`_render_blocks` 96、渲染调用 430-440、doc 构建 450-462、`_summarize` 487、user_content 489-495、`_normalize` 518-533、force 重置 362、SELECT 397-400、`_active_timezones` 160-177、discover 循环 186、deferred 兜底 199-234。

**请求形态**（`day.py:71-82, 433-436`）

- system = `DAY_SYSTEM_PROMPT`，要求输出 `{stats, summary, highlights, topics, mood}` 五个键；
- user = 固定引导语 + 事件块（`[HH:MM] type: summary` 每行一条，summary 截断 300 字）；
- **user 消息不含日期与时区** → 模型只能从时间戳推断节奏，无法写出"9 月 19 日/周一"这类锚点。

**校验与落库**（`day.py:46, 392-408, 459-471`）

- `REQUIRED_KEYS = ("stats", "summary", "highlights", "topics")`，缺键重试，末次仍缺 → 整日 failed；`mood` 缺省不重试；
- 落库 doc 的 `stats` 是**本地计算**（`len(rows)` + TYPE_MAP 计数 + `audio_segments_total`），LLM 返回的 stats 被丢弃；`highlights`/`mood` 直取 LLM 原文。

**消费方调研（全仓）**

- BFF 只读两键：`summary`（`services/bff/timeline/service.py:82`）、`topics`（`timeline/service.py:148`、`search/service.py:61`、`graph/service.py:242`）；
- Android 日卡/周月年卡由 `PeriodAggregator.kt:189-210` 读日卡 `summary`/`topics`；
- **`highlights` / `mood` 无任何生产消费方**，仅出现在 lvdata-mcp 单测与 bff 测试 fixture 中；
- MCP `get_day_summary`（`tools.py:211`）原样透出 doc。

结论：移除三个键**零仓库内消费者破坏**；`stats` 因本地保留、仍可用。**边界声明**：`get_day_summary` 是面向 agent 的对外接口，repo 外是否有读 `highlights` 的会话/工作流无法静态证伪——如需可让使用者自查。

**force 现状**（`day.py:307-314`）——**本次不改，保持原语义**

- `generate(force=True)` = `UPDATE day_summary SET fail_count=0, status='pending', generated_at=NULL WHERE status != 'ok'` + `_all_dirty()`，即仅重跑非 ok 行（`test_day_summary.py:702` 注释与断言即此语义）；
- 全量重跑（含已有 ok 行）不通过 force 实现，走 §7 的手工 SQL + 正常增量路径。

## 3. 目标设计

### 3.1 输出 schema 与示例（§13 结构化改版后）

```json
{
  "summary": "这一天的整体总结（2-3 句）",
  "events": ["最重要的事件或时刻（每条一句）", "…"],
  "topics": ["性能", "协作"]
}
```

- `REQUIRED_KEYS = ("summary", "events", "topics")`；
- **summary 不拼接**（§14）：落库 `doc.summary` = LLM 总述（2-3 句）原文；
- 落库 doc（`_generate_one`）：`summary`（总述）/ `moments`（LLM `events` 的落库名，§15；条目数组、截断 10 条，**预留单独消费、当前无消费者**）/ `topics`（对象数组 `[{topic, search_words}]`，§10；legacy 字符串行兼容读取）/ 本地 `stats`；无 `highlights` / `mood`；
- `schema_version` 保持 `1`（见 §8 待确认项 1）。

### 3.2 现行 system prompt（与 `day.py` 逐行一致）

```python
DAY_SYSTEM_PROMPT = (
    "你是生活数据日总结引擎。你只输出 JSON，不输出任何其他文本或解释。\n"
    "用户会给出本地日期（local_date）、时区（timezone），以及该日的多模态事件"
    "摘要（单设备时按时间排序；多设备时按设备分段、段内按时间排序，段头为"
    "「设备 <id>：」）。严格按照以下 JSON Schema 输出：\n"
    "{\"summary\": \"这一天的整体总结（2-3 句）\", "
    "\"events\": [\"当天最重要的事件或时刻（每条一句，按时间先后"
    "排列，至少 1 条、最多 10 条）\"], "
    "\"topics\": [{\"topic\": \"主题名\", \"search_words\": \"搜索词，可以直接搜索到重点事件消息\"}]}\n"
    "输出示例：\n"
    "{\"summary\": \"这一天用户主要在排查应用启动慢的问题，下午完成了一次方案讨论。\", "
    "\"events\": [\"上午用户反馈启动耗时变长\", \"下午与同事讨论了性能优化方案\"], "
    "\"topics\": [{\"topic\": \"启动优化\", \"search_words\": \"启动耗时\"}]}\n"
    "要求：summary/events/topics 必须基于提供的事件与日期，不要编造；"
    "events 至少 1 条；条目不要写具体时间；"
    "topics 每项的 topic 是主题名（可概括），search_words 必须是从事件摘要里直接提取的"
    "原文连续片段（不做任何修改和拼接），保证用它搜索能直接命中该主题的重点事件；"
    "不要输出事件类型标签（如 chat、translate、agent_job、video frame），不要照抄整句；"
    "最多 5 个，可以没有；"
    "不同设备的事件来源不同，不要把不同设备的事件串联成同一段对话或同一叙事；"
    "数组字段必须存在；只输出 JSON，不要 Markdown 代码块。"
)
```

说明：

- **结构化三键**（§13）：`summary`（2-3 句总述）/ `events`（条目数组，至少 1、最多 10）/ `topics`（**对象数组**，最多 5）；行内多行转义类要求已整体下线——"条目"由程序拼接，模型不做字符串内换行；
- **`topics` 对象化**（§10 设计，2026-09-21 已实施）：`[{"topic": "主题名", "search_words": "原文连续片段"}]`——**展示名与搜索词解耦**：chip 显示可概括的主题名，点击时用 `search_words` 搜索（缺省/空白回退 topic 名）；`_normalize` 归一为对象数组并对 legacy 字符串形态容错（str → `search_words=None`）；bff 读取侧 `topic_items()` 同样兼容存量字符串行；
- 输出示例为 few-shot；`topics` 示例改为**具体可提取短语**（`启动耗时/性能优化`，2026-09-21 起）——合成样本 A/B（3 天 × 3 种 prompt）提取率 92% vs 占位示例 70%；**真实数据全量重生成复测（7 天）后精确可搜率仅 8%→29%**（合成样本高估了模型：真实事件文本更脏，模型仍会拼接/改写）——详见 docs/bff_search_fts.md §10 的真实数据复测表（含搜索侧放宽回退的量化）；若日后出现照抄示例，回退占位示例仅保留规则句；
- `topics` 提取规则的动因：旧规则产出的抽象名（如「影视娱乐」「信号处理」）在事件文本里不存在 → **FTS 关键词检索 0 命中**（点 topic 搜不到内容）——分析见 docs/bff_search_fts.md §10；
- 行号锚点以符号名为准（§2 行号说明）。

### 3.3 新 user message（输入注入，`day.py:487, 489-495`）

```python
user_content = [
    {"type": "text",
     "text": (f"local_date: {d}\ntimezone: {tz}\n"
              "以下是该本地日期的事件摘要，"
              "请按系统提示的 JSON Schema 生成日总结：\n\n"
              f"{block}")},
]
```

`_summarize(block)` → `_summarize(block, tz, d)`（`tz`/`d` 在 `_generate_one` 作用域内已有，调用点传参）。事件块行格式（`[HH:MM] label: summary`）见 §11。

### 3.4 不做：force 语义保持不变

- 代码侧不新增"全量重跑"入口，`generate(force=True)` 维持"仅非 ok 行"；
- 全量重跑 = 开发/测试期手工 SQL 置 pending（§7），复用既有 `_check_pending_retry` → `_day_generatable` → `generate(force=False)` 路径，无需任何代码改动。

## 4. 改动点（文件级）

> 锚点为改动前位置（快照）；当前文件行号见 §2 行号说明。

| # | 文件:位置 | 改动 |
|---|---|---|
| 1 | `summary/day.py:46` | `REQUIRED_KEYS = ("summary", "topics")` |
| 2 | `summary/day.py:71-82` | `DAY_SYSTEM_PROMPT` 全文替换（§3.2） |
| 3 | `summary/day.py:433-436, 459-471` | `_summarize(block, tz, d)` + user 文本加日期/时区；`_normalize` 只归一 `summary`（str）/`topics`（list），删 stats/mood/highlights 分支 |
| 4 | `summary/day.py:392` | 调用点传 `tz, d` |
| 5 | `summary/day.py:403-406` | doc 构建删 `highlights`/`mood` 两行；`stats` 本地计算保留 |
| 6 | `summary/day.py:1-22` | 模块 docstring 同步（输入含日期/时区、输出两键、stats 本地口径） |
| 7 | `summary/day.py:378` | 注释引用 `§6.5.3` → 校正为 `§6.5 步骤 3`（spec 无 6.5.3 小节，编号漂移） |
| 8 | `tests/test_day_summary.py:49-56` | `_ok_json()` 改为 `{summary, topics}`（summary 用含 `\n` 的多行形态） |
| 9 | `tests/test_day_summary.py:537-568` | 断言更新：删 `doc["mood"]`；增 `"highlights" not in doc`、user 块含 `local_date`/`timezone`、`doc["stats"]` 仍为本地计数、多行 summary 原样落库 |
| 10 | `tests/test_day_summary.py` | 新增 2 个用例（§6.2/§6.3） |
| 11 | `tests/test_rebuild_flow.py:77-78` | fixture JSON 改为 `{summary, topics}` |
| 12 | `docs/data/lvdata_mcp_service_v2.md` | §5.4.1 JSON schema（390-412）删 highlights/mood；§6.5 第 3 步（588）补"输入含日期/时区、输出两键"；§12 `get_day_summary`（736，"含 stats/status" 不变）；force 描述不动 |

**不改**：`generate(force=)` 语义与其测试（`test_generate_force_resets_and_runs_all` 保持原断言）、BFF 三个 service、Android（渲染行为见 §5）。

**特别保留**：`services/tests/unit/bff/conftest.py:192-207` 的 day_summary fixture **不清洗**——它模拟存量旧格式行（含 highlights/stats），正是"新旧共存"兼容性覆盖；建议加一行注释标注"legacy 格式样例（prompt 改版前行）"。

## 5. 边界矩阵

| 场景 | 行为 |
|---|---|
| 旧行（含 highlights/mood）与新行共存 | 消费者按 key 读 summary/topics，无影响；旧字段自然沉寂（bff fixture 持续覆盖） |
| 手工 SQL 置 pending 后（全量重跑） | 正常路径接管：`_check_pending_retry` 每轮取 ≤`day_summary_max_per_poll`(10) 天 → `generate(force=False)` |
| 重跑行属**非活跃时区** | `_check_pending_retry` 不可见（discover 只遍历活跃时区 ≤2 个，`day.py:160-177, 186`）→ 行保持 pending 直到该时区被激活（§8.3 语义，预期行为；SQL 建议限定活跃时区，§7） |
| 全量重跑进行中 | 被置 pending 的行 status≠ok → **BFF 日卡 summary/topics 对该日空窗**、`has_pending` 可能翻 true；重生成成功后逐日恢复 |
| 全量重跑中断（重启/宕机） | pending 行持久化，重启后继续被 `_check_pending_retry` 取件，无清单丢失 |
| 单轮批量上限 | 单轮最多 10 天，`stage_timeout_s`(1800s) 压力极小（无超时降级问题） |
| `day_deferred` 残留 | discover 对非 ok 行的 deferred 项自动删除（`day.py:199-234`），无需手工清理 |
| 活跃录制日被置 pending | debounce 未过 → 落 `day_deferred` 延后，静默窗口过后再生成（预期） |
| LLM 坏 JSON / schema 缺键 | fence/坏 JSON 退避重试（`day.py:510-514`，`sanitize_json` 见 `providers.py:47-54`）；末次缺 `summary`/`events`/`topics` 任一 → 该日 failed（退避 60/300/900s，3 次暂停） |
| 生成期并发新事件 | `generated_at = T0` 快照语义不变（`day.py:454`），下轮 dirty 必中 |
| 0 事件日 | 不变：删 pending 占位行、跳过 |
| audio-with-transcript 输入去重 | 不变（`day.py:49-61, 409-411`） |
| LLM 输入行标签 | chat → `chat(<payload.role>)`（缺 role 裸 `chat`）；window/video → `video window`/`video frame`；其余原样（§11）。仅存在于 LLM 输入，BFF/落库/Android 无感知 |
| 多设备日 LLM 输入 | 按设备分段：段头 `设备 <uuid8>：`、段间空行、段序=首现序、段内原排序；单设备日与旧格式**逐字一致**（§12）。落库/BFF/Android 无感知 |
| LLM 输出与落库 | 三键 `summary`/`events`/`topics`；`doc.summary` 仅 LLM 总述（§14 起不拼接）；LLM `events` 以 doc 字段 `moments` 独立落库（§15，截断 10 条、预留单独消费、当前无消费者）；`topics` 对象数组 `[{topic, search_words}]`（§10，缺/空白 `search_words` → null，畸形条目丢弃） |
| 旧行 topics（dict / str） | bff `topic_items()` 归一化：主题名照常透出、`search_words` 透出（legacy str/detail-only → null，客户端回退用 topic 名搜索），`detail` 统一置 null（Android chip 只显示主题名，无感知） |
| 周/月/年卡 UI | **非回归**：WEEK+ 以"每日 summary 一条 + 每条 2 行截断"渲染（`PeriodAggregator.kt:189-196`、`PeriodCard.kt:146-158`），合并后首 2 行仍是整体总结，展示与现状等价；DAY 卡不限行数（`maxLines=Int.MAX_VALUE`），新增的事件列表完整显示 |
| MCP `get_day_summary` 消费者 | 仓库内无读 highlights/mood 的用法；repo 外无法静态证伪（§2 边界声明） |

## 6. 测试计划

### 6.1 断言更新与回归

1. `test_generate_writes_day_summary`：断言 user 块首部含 `local_date: {DAY}` 与 `timezone: Asia/Shanghai`；doc 无 highlights/mood 键；`doc["stats"]["events"]` 仍为本地计数；多行 summary 原样落库；
2. 既有 `test_generate_llm_input_truncates_events`（798）应继续通过（按 "chat:" 定位行，不受新增头两行影响）；
3. 既有 `test_generate_force_resets_and_runs_all`（682）**不改**——force 语义未变，作为回归项运行。

### 6.2 新增：新 REQUIRED_KEYS

`test_generate_required_keys_new_schema`：LLM 末次返回缺 `topics` → 该日 failed；缺 `summary` 同理。

### 6.3 新增：多行 summary

`test_generate_multiline_summary`：fake 返回含 `\n` 的 summary（∈ `_ok_json` 默认形态）→ 落库 doc 原文保留换行、BFF 可 `json.loads` 读出；另补一条"坏转义（字符串内真实换行）→ JSONDecodeError → 重试"路径用例。

### 6.4 命令

```bash
# 必须用项目 .venv（source .venv/bin/activate；裸 python 解析到别的环境，
# 依赖版本不同会造成假失败——如 test_gzip.py 依赖 starlette 1.6.0，见 android_timeline_topics.md §9.2）
cd tools/lvdata-mcp && pytest tests/test_day_summary.py tests/test_watcher.py tests/test_rebuild_flow.py -q
pytest services/tests/unit/bff -q
```

## 7. 全量重跑操作路径（开发/测试，不改代码）

**前提**：watcher 常驻（`serve`）；属对共享库的写操作，**操作前备份 `lvdata_inventory.db`**（或记录 `status` 分布与行数以便比对）。

**代码改动后必须先重启服务**（2026-09-19 真机踩坑）：watcher 进程内加载模块，editable 安装（`tools/lvdata-mcp/src`）改文件**不会热更新**——在跑的旧进程会用旧 prompt 重跑。重启命令（cwd = 仓库根，env 由 `services/.env` 注入，缺 `DASHSCOPE_API_KEY` 会 fail-fast）：

```bash
set -a; . ./services/.env; set +a
nohup .venv/bin/python3.12 .venv/bin/lvdata-mcp --config configs/dev/lvdata_mcp.yaml serve \
  > /tmp/lvdata_mcp_restart.log 2>&1 & disown
```

```bash
# DB 默认 <data.root_dir>/lvdata_inventory.db（config.py:124-125，可被 data.db_path 覆盖）
sqlite3 <db_path> "UPDATE day_summary SET status='pending', fail_count=0, generated_at=NULL \
  WHERE status='ok' AND timezone IN ('Asia/Shanghai');"
# 时区列表 = 活跃集（最近 enabled 时区 + 配置默认时区，≤2 个）；非活跃时区的行不会被重跑（§5）
# 限量（可选）：AND local_date >= '2026-08-01'
```

机制与观察点：

- 下一轮 poll → round → `_flush_due` 因存在非 ok 行放行 → day_summary stage `discover` 经 `_check_pending_retry` 取 pending 行（≤10/轮）→ `generate(force=False)` 重生成（`up_to_date` 护栏因 `prev.status != 'ok'` 失效，必然真正重跑）；
- **进度观察**：`get_status` 的 `data.summaries_ok`（ok 行计数，`store.py:423-425`）回升；精确口径直接查库 `SELECT status, COUNT(*) FROM day_summary GROUP BY status;`。注意：~~tool 描述里的 `day_summary{pending,failed}`/`last_day_summary_at` 实现中并不存在~~ **2026-09-28 注：该漂移已不存在**——这几个字段已在 `/api/status` 的 `data.analysis` 下实现（原 `get_status` tool 已迁 REST，见 `issue_list.md` I-4）；
- 想立即促发一轮可调 REST `POST /api/analysis/trigger`（body `{"stages":["day_summary"]}`，不带 force 即可；
  MCP `trigger_analysis` 已删除）；
- 耗时 ≈ `ceil(N/10) × (60s + 单轮生成耗时)`（轮间 poll 间隔 + 单轮 4 并发生成）；
- 中止：无破坏性手段——停服务即可，pending 行下次启动续跑；已重生成的行正常 ok。

## 8. 风险与待确认

**风险**

- 全量重跑是唯一有 LLM 成本的动作：先跑行数估算（`SELECT timezone, COUNT(*) FROM day_summary GROUP BY timezone;`），低峰执行；随时可停；
- 重跑期间日卡空窗（§5）：手工触发、可控窗口，可接受；若需"旧内容保持可见直至新结果"，属另案（需改 `_finish_failed`/护栏路径），本方案不做；
- 回滚 = `git revert`（无 DB 迁移、无新表）；回滚后新格式行缺 highlights/mood，仓库内无消费者读取，属已知可接受。

**待确认项**（无异议即按推荐执行）

1. `doc.schema_version` 保持 `1`（推荐）还是升 `2`？推荐保持 1——消费者按 key 读取，字段集合收窄无歧义；
2. `summary` 内"最重要事件/时刻"条数上限：**推荐“最多 10 条”**（2026-09-19 由 5 条上调；bound 长度），备份方案为不限（长事件日 summary 可能较长，Android 日卡不限行数显示）；
3. 是否顺带调整周/月/年卡"每条 2 行截断"？推荐不动，留作后续 UI 议题。

## 9. 改动清单（实施记录，2026-09-19）

- [x] `day.py` 七处改动（§4 #1-#7，含 docstring 与注释校正）
- [x] 单测更新 + 2 个新用例（§6.1-6.3）
- [x] `test_rebuild_flow.py` fixture 更新（§4 #11）；bff conftest **保留**并加 legacy 注释（§4 特别保留）
- [x] 规格文档更新（§4 #12，另增「修订裁定（2026-09-19，day_summary prompt 改版）」）
- [x] 本地跑 §6.4 命令全绿：lvdata-mcp 全量 **178 passed / 0 failed**（含新用例 `test_generate_required_keys_new_schema`、`test_generate_multiline_summary_and_bad_escape_retry` 与 force 回归）；bff 单测 **98 passed**
- [ ] （用户执行）§7 备份 → 手工 SQL → 观察 `summaries_ok` 回升至全量、待重跑行归零

实施备注：

- §8 待确认项按推荐执行：`schema_version` 保持 1；事件条数上限「最多 10 条」（2026-09-19 由 5 条上调）；周/月/年卡不动；
- 过程发现：`ruff check` 对 `tests/test_day_summary.py` 报 F401（`pytest` 未使用）/E741（变量 `l`）——经 `git show HEAD` 比对为**存量问题**，非本次引入，未动。

## 10. 验收标准

1. 单测全绿：含新增 2 用例 + force 回归 + bff 单测；
2. 抽查 2-3 日 doc JSON：`summary` 含整体总结 + 直接列举的条目；无 `highlights`/`mood`；`stats` 为本地计数；
3. 全量重跑后：`summaries_ok` = day_summary 总行数（活跃时区口径），`pending`/`failed` 归零；
4. Android 日卡人工查看一日：多行 summary 正常换行渲染、topics chips 正常。

## 11. LLM 输入行标签优化（已实施，2026-09-19）

### 11.1 需求

1. `chat` 行区分角色：`chat(user): …` / `chat(assistant): …`；
2. `window` 标签改 `video window`；
3. `video` 标签改 `video frame`。

### 11.2 现状

输入行渲染原为 `[HH:MM] type: summary`，type 取事件类型原值（`chat`/`transcribe`/`translate`/`window`/`video`/`audio`/`agent_job`）；现行实现见 §11.3/§12.2 代码（`day.py:430-440`，渲染经 `_render_blocks`）。chat 的角色在 `payload.role`（∈ `user|assistant`，`docs/recording_data_process.md:66`；chatlog 铸造写入 `chatlog.py:88-90`），此前渲染**未使用**；`_generate_one` 的 SELECT 含 `payload` 列（`day.py:397-400`），无需改查询。

### 11.3 设计

| 事件 type | 新标签 | 规则 |
|---|---|---|
| chat | `chat(user)` / `chat(assistant)` | `payload.role` 非空 → `chat(<role>)`；缺 role / payload 非 JSON / payload NULL → 裸 `chat`（回退） |
| window | `video window` | 常量替换 |
| video | `video frame` | 常量替换 |
| 其余（transcribe/translate/audio/agent_job） | 原样 | 不变 |

```python
TYPE_LABEL = {"window": "video window", "video": "video frame"}


def _payload_role(payload: str | None) -> str:
    if not payload:
        return ""
    try:
        p = json.loads(payload)
    except (ValueError, TypeError):
        return ""
    role = p.get("role") if isinstance(p, dict) else None
    return role.strip() if isinstance(role, str) else ""


def _event_label(etype: str, payload: str | None) -> str:
    if etype == "chat":
        role = _payload_role(payload)
        return f"chat({role})" if role else "chat"
    return TYPE_LABEL.get(etype, etype)
```

行渲染（`day.py` 现有 render 循环）改为：

```python
lines.append(f"[{hhmm}] {_event_label(r['type'], r['payload'])}: {text}")
```

**不变式**：`max_chars_per_event` 截断只作用于 text；`stats`（TYPE_MAP → `video_windows`/`video_frames`）与排序（EVENT_PRIORITY）不变；标签**仅存在于 LLM 输入**——BFF/Android/MCP/落库 doc 均无感知；无需数据回填。

### 11.4 改动点

1. `summary/day.py`：新增 `TYPE_LABEL` + `_payload_role` + `_event_label`，渲染行换用 label；
2. `tests/test_day_summary.py`：新增 `test_generate_llm_input_labels`——播种 chat(user)/chat(assistant)/window/video 四类事件，断言四种标签齐备；同用例覆盖 chat 缺 role（payload `{}`）→ 裸 `chat` 回退；
3. 既有用例**不改**：`_seed` payload 保持 `"{}"`，`test_generate_writes_day_summary`（断言 `[00:00] chat: …`）与 `test_generate_llm_input_truncates_events`（按 `"chat:"` 定位）继续覆盖回退路径；
4. `docs/data/lvdata_mcp_service_v2.md` §6.5 步骤 3：`每事件 [HH:MM] type: summary` → `每事件 [HH:MM] label: summary`（label 规则见 day.py），并在 2026-09-19 修订裁定尾部追加一句。

### 11.5 边界矩阵

| 场景 | 行为 |
|---|---|
| chat 无 payload / `payload="{}"` / 坏 JSON | 裸 `chat`（旧行/测试回退路径） |
| role 为 `unknown` 或其他非 user/assistant 值 | 按原值渲染 `chat(<role>)`（待确认项 1） |
| 未来新增事件类型 | 不在 `TYPE_LABEL` → 原样透传（默认安全，无需同步改） |
| system prompt | 不动（`video window`/`video frame` 自明，待确认项 3） |
| 成本 | 每 chat 事件一次 `json.loads`（微秒级；audio 去重已在做同类解析） |

### 11.6 测试计划

- 新增 1 用例（§11.4-2）+ 既有回退路径回归；
- 命令：`pytest tests/test_day_summary.py tests/test_watcher.py tests/test_rebuild_flow.py`（全量 178）+ bff 98；
- 探针复核：渲染块样例打印确认四种标签与回退。

### 11.7 决策与实施记录（2026-09-19 用户确认并实施）

1. 非 user/assistant 的 role（含 `unknown`）：**按原值渲染** `chat(<role>)`（用户裁定）；
2. transcribe/translate/agent_job 带角色：**不动**（用户裁定）；
3. system prompt 补标签说明：**不补**（用户裁定）。

实施证据：`day.py` 新增 `TYPE_LABEL`/`_payload_role`/`_event_label` 并替换渲染行；新增用例 `test_generate_llm_input_labels`（四标签齐备 + 缺 role 回退）；全量 **179 passed / 0 failed**；端到端探针实测渲染块：`[01:00] chat(user): …`、`[01:02] chat(assistant): …`、`[01:06] video window: …`、`[01:08] video frame: …`，transcribe/audio/agent_job 原样。

## 12. 多设备输入分段（B 计划，已实施，2026-09-19）

### 12.1 背景与实测

同一天多设备（同账号多端）产生事件时，现实现将**所有设备**的 events 合并为**一份** LLM 输入（day_summary 主键 `(timezone, local_date)` 无设备维度；生成 SELECT 不含 `device_uuid`，行 label 无设备信息）。双设备探针实测（平板=订会议室对话、手机=会议录制）：

```
[09:00] chat(user): 帮我订下午的会议室        ← 平板
[09:00] chat(assistant): 已预订 3 号会议室    ← 平板
[09:00] chat(user): 这个方案我们再想想        ← 手机
[09:00] audio: 会议室多人讨论，白板书写声      ← 手机
```

两台设备的两段不同对话在输入里无差别相接，模型很可能读成同一条连续对话/同一叙事（跨设备混淆）。排序键 `(ts_ms, EVENT_PRIORITY, event_id)` 中 `event_id` 为自增（入库顺序），同 ms 跨设备先后无设备语义。

**B 计划**：按设备分段——段内时间排序，段头写设备标识；保住单份日总结，同时给模型设备边界。

### 12.2 设计

**分段格式**（仅多设备日启用）：

```
local_date: 2026-08-01
timezone: Asia/Shanghai
以下是该本地日期的事件摘要（按设备分段，段内按时间排序），请按系统提示的 JSON Schema 生成日总结：

设备 aaaa1111：
[09:00] audio: 会议室多人讨论，白板书写声
[09:01] video window: 屏幕为会议投屏
[09:05] video frame: 画面出现白板与多人

设备 bbbb2222：
[09:00] chat(user): 帮我订下午的会议室
[09:00] chat(assistant): 已预订 3 号会议室
[09:30] agent_job: 完成「会议室预订」
```

规则：

- **段头**：`设备 <device_uuid 前 8 位>：`（全仓无设备可读名，仅 UUID；前 8 位唯一性足够且跨日稳定）；
- **段序**：按该设备当日首事件在全局排序中的出现顺序（= 首事件 `(ts_ms, EVENT_PRIORITY, event_id)` 序，确定性）；
- **段内**：原排序键 `(ts_ms, EVENT_PRIORITY, event_id)` 与行格式 `[HH:MM] label: summary` 不变；
- **段间**空一行。

**单设备不变式（关键）**：设备数 = 1 时**不输出段头、不留段间空行**，输出与现行行为**逐字一致**——绝大多数日零变化，既有测试与 prompt 语义不受扰动。

**实现**（示意）：

```python
def _render_blocks(rows: list[sqlite3.Row], tzinfo: ZoneInfo,
                   max_chars: int) -> str:
    """事件块渲染（§12）：多设备按首现序分段（段头 `设备 <uuid8>：`，段间空行）；
    单设备无段头、无空行，与旧格式逐字一致。"""
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in rows:          # rows 已按 (ts_ms, EVENT_PRIORITY, event_id) 排序
        groups.setdefault(r["device_uuid"], []).append(r)
    segs: list[str] = []
    for dev, grp in groups.items():      # dict 保序 = 首现序
        lines = []
        for r in grp:
            hhmm = datetime.fromtimestamp(r["ts_ms"] / 1000,
                                          tzinfo).strftime("%H:%M")
            text = (r["summary"] or "")[:max_chars]
            lines.append(
                f"[{hhmm}] {_event_label(r['type'], r['payload'])}: {text}")
        seg = "\n".join(lines)
        segs.append(f"设备 {dev[:8]}：\n{seg}" if len(groups) > 1 else seg)
    return "\n\n".join(segs)
```

`_generate_one` 内：counts 统计（合并口径，不变）与渲染循环解耦，渲染改调 `_render_blocks`。

### 12.3 改动点

| # | 文件:位置 | 改动 |
|---|---|---|
| 1 | `summary/day.py` SELECT（397-400） | 列增 `device_uuid` |
| 2 | `summary/day.py` 渲染区（430-440） | 新增 `_render_blocks`（多设备分段/单设备原格式）；counts 与渲染解耦 |
| 3 | `summary/day.py` `DAY_SYSTEM_PROMPT`（116-133） | 输入描述改"事件摘要（按设备分段，段内按时间排序）"；防串联句见待确认项 2 |
| 4 | `tests/test_day_summary.py` | 新增 `test_generate_multi_device_segments`；`test_generate_writes_day_summary` 加单设备不变式断言（块内无 `设备 ` 段头） |
| 5 | `docs/data/lvdata_mcp_service_v2.md` | §6.5 步骤 3 补分段规则；2026-09-19 修订裁定补一句 |
| 6 | 本文件 | 落地时 §3.3/§5/§12 状态同步 |

不改：`stats`（合并口径）、去重、截断、落库 doc、BFF、Android。

### 12.4 边界矩阵

| 场景 | 行为 |
|---|---|
| 单设备日（绝大多数） | **逐字等价现状**（无段头）——零风险 |
| 多设备日 | 按首现序分段；段头 `设备 <uuid8>：`；段间空行 |
| 同 ms 跨设备事件 | 分属各自段，不再跨设备相邻；段内 `event_id` 定序 |
| audio-with-transcript 去重 | 不变（先过滤后分段） |
| `stats` / `event_count` | 合并口径不变（不按设备拆） |
| 落库 doc / BFF / Android | 无感知（分段仅存在于 LLM 输入） |
| 设备数多（5+） | 段数 = 设备数；仅多段头行，事件行无隐藏放大 |
| 旧 day_summary 行 | 不受影响（无需回填） |
| token 成本 | 仅多设备日 +（设备数 × 1 行段头 + 空行） |

### 12.5 测试计划

1. 新增 `test_generate_multi_device_segments`：两设备（含同 ms 跨设备 chat）→ 断言两个段头、段内不含他设备事件、段序按首事件时间、段间空行；
2. 单设备不变式：`test_generate_writes_day_summary` 增 `assert "\n设备 " not in fake.blocks[0]`（其余断言不变）；
3. 探针：复用 `/tmp/probe_multidevice.py` 复跑核对段头与分段；
4. 回归：lvdata-mcp 全量（现 179 + 1）+ bff 98。

### 12.6 决策与实施记录（2026-09-19 用户确认并实施）

1. 段头形态：`设备 <uuid 前 8 位>：`（无异议，按推荐执行）；
2. prompt 防串联句：**已加入**（用户裁定「可以加入」）：开头描述补「单设备时按时间排序；多设备时按设备分段、段内按时间排序，段头为「设备 <id>：」」，要求补「不同设备的事件来源不同，不要把不同设备的事件串联成同一段对话或同一叙事」；
3. 段间空行：保留（无异议按推荐）。

实施证据：`day.py` SELECT 增 `device_uuid`；新增 `_render_blocks`（段间以 `\n\n` 连接、无尾随空行）；user 引导语改中性（不再断言"按时间排序"，排序/分段规则统一由 system prompt 描述）；新增用例 `test_generate_multi_device_segments`（段序=首现序、段间空行、段内不含他设备事件、同 ms 跨设备不再相邻）+ `test_generate_writes_day_summary` 单设备不变式断言；全量 **180 passed / 0 failed**；双设备探针实测分段，单设备探针逐字等价。过程小坑：测试 fixture 的两台设备 UUID 前 8 位相同导致段头重复，已改（实践提示：uuid8 前缀按随机分布碰撞概率极低，但 fixture 需显式取不同前缀）。

### 12.7 真机全量验证记录（2026-09-19，当前 dev DB）

**环境**：`/opt/lvdata/lvdata_inventory.db`，4 个 ok 日（09-13/15/16/18），服务 8090；操作按 §7（备份 → 重启加载新代码 → SQL 置 pending → watcher 分批重生成 → 校验）。单轮全量重跑 ≈ 2 分钟（`concurrency=1` 串行 4 次调用）。

**prompt 合规率演进（正文字段 = summary 内嵌"- "条目列表）**：

| 版本 | prompt 措辞 | 合规率 | 失败形态 |
|---|---|---|---|
| v1 | "随后直接逐条列出…（最多 10 条）" | 2/4 | 09-16、09-18 只写总述、**无条目** |
| v2 | schema 行加"必须换行…至少 1 条、最多 10 条" | 2/4 | 同为上述两天（非随机，稳定复现） |
| v3 | 要求行加"必须包含以 \"- \" 开头的条目列表（至少 1 条）" | 3/4 | 09-13 条目**同行内联**（`…对话。- 19:25 …`，漏换行转义） |
| v4 | 再加"每条单独一行（行间用 \\n 转义分隔）" | 3/4 | 09-18 仍无条目 |

**根因（真实复现 09-16 + 单次实调验证）**：聊天/翻译密集日，模型把输出预算花在 **topics 的冗长 detail** 上（实测 output_tokens 2728，5 条长 topic），跳过 summary 条目；行内变体则是模型漏写 `\n` 转义。**prompt-only 加固不可靠**（2/4 → 3/4 即触顶），且"让模型在一个 JSON 字符串里做多行转义"本身就是脆弱设计。

**其他校验通过**：无 `highlights`/`mood`；无示例内容照抄；无「最重要的事件或时刻：」标题行残留；`stats` 为本地口径。新增的 `warning: day summary missing bullet list` 日志如期捕获缺失（末轮 1 条，对应 09-18）。

**结论**：该记录直接支撑 §13 结构化改版（`events` 数组 + `topics` 去 detail + 程序拼接）——把"多行转义 + 条目保留"从模型的不确定行为变成程序的确定性输出。§13 落地后需再全量重跑一次覆盖当前 4 行。

## 13. 结构化输出改版：summary / events / topics（已实施，2026-09-19）

### 13.1 需求

1. LLM 输出新增 `events`：`["event1 description", "event2 description"]`——重要事件/时刻的描述；
2. `summary` 回归**只含整体总结（2-3 句）**，不再内嵌条目；
3. `topics` 改为**字符串数组** `["topic1", "topic2"]`，**不要 detail**（⚠ 本节为 2026-09-19 的历史记录；**2026-09-21 起 `topics` 已是对象数组 `[{topic, search_words}]`，以后者为准**，见 §3.2）；
4. **程序拼接**：`summary` + `events` 合成最终 day summary（消费者侧形态与现状一致）。

### 13.2 影响面调研（topics 元素类型 dict → string 是破坏性变更）

| 消费方 | 现状 | 影响 |
|---|---|---|
| `bff/timeline/service.py:_day_topics`（143-153） | `isinstance(t, dict) and t.get("topic")` → `DayTopicItem(topic, detail)` | 字符串元素被过滤 → **日卡 topics chips 变空**；需归一化 |
| `bff/search/service.py:59-64` | 同 dict 判定，供 topic 命中检索 | topic 命中丢失；需归一化 |
| `bff/graph/service.py:149-150, 158-165, 391-393` | first_topic（event 节点 label）/ topic 节点与边 / mentions 计数均按 dict | topic 节点与边消失；需归一化 |
| Android `DayTopicDto{topic, detail?}`（`MylifeDtos.kt:54-57`） | 消费 BFF DTO | **无改动**（BFF 继续输出 `{topic, detail:null}`，chip 只显示 topic 文本，detail 不被渲染） |
| MCP `get_day_summary` | doc 原样透出 | `topics` 元素类型变化（**破坏性**；repo 内无消费者，repo 外未知——同 §2 边界声明） |

结论：需在 BFF 侧新增**归一化单点**，同时兼容新（str 数组）旧（`{topic, detail}` 字典）两种形态——旧行在重跑前一直存在。

### 13.3 设计

**新 LLM 输出 schema**（`REQUIRED_KEYS = ("summary", "events", "topics")`）：

```json
{
  "summary": "这一天的整体总结（2-3 句）",
  "events": ["最重要的事件或时刻（每条一句）", "…"],
  "topics": ["性能", "协作"]
}
```

**prompt**：与实现逐行一致，正文见 §3.2（本节不重复；后续微调亦只维护 §3.2）。

**关键收益**：删去"行间用 `\n` 转义"整段——`summary` 为单行、`events` 为数组项，**结构性消除**多行 JSON 转义失败与"条目内联/丢失"两个真机缺陷类（§13.2 之外，见上轮真机验证）。

**程序拼接**（`day.py`，`_generate_one`）：

```python
events = [e for e in parsed["events"] if isinstance(e, str) and e.strip()][:10]
composed = (summary + "\n" + "\n".join(f"- {e.strip()}" for e in events)
            if events else summary)
```

- 落库 `doc["summary"] = composed`（消费者侧形态不变：总述 + `- ` 条目）；`doc["topics"] = topics`（字符串数组）；
- `events` 为空：**不判 failed**，仅 `log.warning("day summary events empty", tz=…, date=…)`（替换现行 missing-bullet 告警）；
- `_normalize`：`summary`→str；`events`→list（过滤空串）；`topics`→list（非 str 元素取 `t["topic"]` 容错）。

**BFF 归一化单点**（新增 `services/bff/common/topics.py`）：

```python
def topic_names(topics: list | None) -> list[str]:
    """day_summary topics 归一化：新格式 str 数组 / legacy {topic, detail} 字典。"""
```

`timeline._day_topics`、`search`（topic 命中）、`graph`（3 处）统一改用它；BFF 输出 `DayTopicItem(topic=name, detail=None)`，Android 无感。

### 13.4 改动点（文件级）

| # | 文件 | 改动 |
|---|---|---|
| 1 | `summary/day.py` | `REQUIRED_KEYS` 三键；prompt 全文替换（§13.3）；`_normalize` 适配；`_generate_one` 拼接 + events 截断（`[:10]`）+ 空 events 告警 |
| 2 | `services/bff/common/topics.py`（新增） | `topic_names()` 归一化单点 |
| 3 | `bff/timeline/service.py:_day_topics` | 改用 `topic_names`，`detail=None` |
| 4 | `bff/search/service.py` | topic 命中改用 `topic_names` |
| 5 | `bff/graph/service.py` | first_topic / topic 节点 / mentions 三处改用 `topic_names` |
| 6 | `tools/lvdata-mcp/tests/test_day_summary.py` | `_ok_json` 三键；新增 `test_generate_composes_events_and_topics`（拼接形态、topics 字符串、events 空 → 仅 summary）；`required_keys` 用例扩到缺 events/topics |
| 7 | `services/tests/unit/bff/` | conftest 保留 legacy 字典 fixture（兼容覆盖，既有断言不动）+ 新增 str-topics 日 fixture，断言 timeline/search/graph 三处透出 |
| 8 | `docs/data/lvdata_mcp_service_v2.md` | §5.4.1 schema（topics 改字符串数组、新增 events 说明——拼接后不落 doc）；§6.5 步骤 3；2026-09-19 修订裁定追加 |
| 9 | 本文件 | 落地后 §3.1/§3.2/§5/§13 同步 |

不改：`stats`（本地口径）、去重、截断、BFF DTO 形状、Android、`get_day_summary` 工具签名。

### 13.5 边界矩阵

| 场景 | 行为 |
|---|---|
| 旧行（dict topics） | `topic_names` 兼容 → 渲染/检索/图谱正常（detail 置 null，Android chip 不显示 detail，无感知） |
| 新行（str topics） | 正常透出 |
| MCP `get_day_summary` 消费者 | `topics` 元素 dict→str（破坏性；repo 内无消费者） |
| `events` 空 | 仅 summary 落库 + warning（不 failed） |
| `events` 超发 | 程序截断 `[:10]`（防御） |
| 拼接形态 | `summary + "\n" + "- 条目"`，与现行渲染逐字同构 → BFF/Android 零改动 |
| 输出预算/坏 JSON | topics 去 detail + events 结构化 → 输出更短更稳；多行转义要求删除 → 失败类收敛 |
| LLM 仍按旧习惯输出（如 summary 内嵌 "- " 或 topics 带 detail 字典） | `_normalize` 容错（dict→topic 名）；内嵌条目不再特殊处理（截断逻辑不变） |

### 13.6 测试计划

- lvdata：`_ok_json` 三键改造 + 新增拼接用例（断言 `doc["summary"]` == 总述 + `- ` 行、`doc["topics"]` == 字符串数组、events 空 → summary 原文 + 告警日志）；缺键用例扩展；
- bff：legacy fixture 断言保持 + 新格式 fixture 三处透出断言；
- 探针：更新单/双设备探针的输出样例（拼接后形态打印）；
- 真机：本方案落地后全量重跑 4 日，校验 4/4（含 events 条目数与 topics 无 detail）；
- 命令不变：lvdata 全量 + bff 单测。

### 13.7 决策与实施记录（2026-09-19 用户确认并实施）

1. ~~`events` 不落 doc~~（§13 时点决策；**已由 §14 取代**——events 独立成字段落 doc，预留单独消费）；
2. `events` 条目不写具体时间（2026-09-19 用户裁定：要求行新增「条目不要写具体时间」；同日先删「可含时间点」提示与示例时间点，后定为明确禁止）；
3. 程序截断超发 `events` 到 10 条（推荐执行，已实现 `[:10]`）；
4. `topics` 上限 5：**prompt 约束、程序不强制**（用户裁定）；schema 占位符给数、不给示例值；
5. 上一轮"条目单独一行"等措辞**随 prompt 全文替换整体下线**（已随本改版删除）。

实施证据：`day.py` 三键 + 拼接 + 告警（`day summary events empty`）；新增 `services/bff/common/topics.py` 归一化单点并接入 timeline/search/graph；新增参数化用例 `test_generate_composes_events_and_topics`（正常/空 events/超发截断）与 `required_keys` 参数化（缺 events/缺 topics）；本地回归 **lvdata 184 passed / bff 98 passed**。真机全量重跑结论见 §13.9。

### 13.8 实施清单

- [x] `day.py` 改动（§13.4 #1）
- [x] BFF `topic_names` 单点 + 3 处调用（#2-#5）
- [x] lvdata/bff 测试更新（#6-#7）
- [x] 规格与本文档同步（#8-#9）
- [x] 本地回归全绿（lvdata 184 + bff 98）
- [x] 重启服务加载新 prompt → 全量重跑 4 日 → 合规校验（events 条目 / topics 形态 / 拼接结果；见 §13.9）

### 13.9 真机全量重跑（2026-09-19）

操作：重启服务加载新代码 → 4 日置 pending → watcher 重生成（`concurrency=1` 串行，约 3 分钟）→ 校验。

结果：**4/4 全合规**——

| 日期 | events 条目 | topics |
|---|---|---|
| 09-13 | 1 | 休闲活动 / 出行计划 |
| 09-15 | 3 | 股票市场 / 桌面环境 / AI对话 |
| 09-16 | 4 | 日常交流 / 语言学习 / 国际时政 / 太空军事 / 语音转录（= 上限 5） |
| 09-18 | 5 | 英语学习 / 历史典故 / A股市场 / 房地产政策 |

- 落库 doc：`summary` 为拼接形态（总述 + `- ` 条目，条目均带时间点）；`topics` 全为字符串数组；doc 无 `events`/`highlights`/`mood`；
- `events empty` 告警 **0 条**；对比 §12.7（prompt-only 末轮 3/4、需告警捕获缺失）：09-18 当时仍丢条目，本版稳定 5 条——**结构化 + 程序拼接把"条目保留"变成确定性行为**。

**复跑（同日，加「条目不要写具体时间」后）**：4 日再次全量重生成，**条目含具体时间点 0 条**（4/4 合规）——

| 日期 | events 条目 | topics |
|---|---|---|
| 09-13 | 2 | 生活闲聊 |
| 09-15 | 5 | 海马汽车行情 / 股票数据分析 / 桌面环境 / AI交互体验 |
| 09-16 | 5 | 翻译 / 太空军事 / 语音转写 / 设备交互 |
| 09-18 | 4 | 英语学习 / 历史典故 / 股市行情 |

条目以相对时段（"上午/下午"）或纯事件描述替代钟点时间（如"用户提议下午出去走走"）；`events empty` 告警 0 条。

## 14. summary / events 解耦（已实施，2026-09-19）

### 14.1 需求

- day_summary 目前是 `summary + events` 程序拼接的结果 → **改为只使用 LLM 的 `summary`**（2-3 句总述，不拼接）；
- `events` **保留**：继续生成、独立落 doc，**以后单独消费，目前不做**（当前无消费者）。

### 14.2 设计

- 落库 doc：`summary` = LLM 总述原文；新增字段（条目数组，程序截断 10 条；**字段名后按 §15 定为 `moments`**）；`topics` / `stats` / `usage` 不变；
- LLM 契约（三键 / prompt）**不变**——变的只是程序侧：取消拼接，events 从"中间产物丢弃"改为"独立落 doc"（§13.7-1 被本节取代）；
- 空 events：不判 failed；`log.warning("day summary events empty")` 保留（prompt 要求 ≥1，缺失仍值得观测）。

### 14.3 边界矩阵

| 场景 | 行为 |
|---|---|
| BFF / Android | 无感：日卡只读 `doc.summary`（现为 2-3 句总述，比拼接版短）；`events` 无人读 |
| events 超发 | 程序截断 `[:10]`（同 §13） |
| 旧行（无 `events` 字段） | 消费者不读 events，无兼容问题；重跑后自然补齐 |
| MCP `get_day_summary` | doc 新增 `moments` 键（§15 命名；加法变更，老消费者忽略未知键） |

### 14.4 改动点

| # | 文件 | 改动 |
|---|---|---|
| 1 | `summary/day.py` | `_generate_one`：取消拼接（`doc.summary` = LLM 总述原文），新增 `doc["events"]`（`[:10]`）；模块 docstring 同步 |
| 2 | `tools/lvdata-mcp/tests/test_day_summary.py` | 用例改为 `test_generate_events_stored_and_summary_passthrough`（总述透传 + events 落 doc / 空 / 超发截断）；断言去 `OK_COMPOSED` |
| 3 | `docs/data/lvdata_mcp_service_v2.md` | §5.4.1 schema（新增 `events`、summary 描述改"不拼接"）+ 修订裁定 |
| 4 | 本文件 | §3.1 / §5 / §13.7 标注 + 本节 |

### 14.5 实施记录（2026-09-19）

本地回归 **lvdata 184 passed / bff 98 passed**。

真机全量重跑（重启加载新代码 → 4 日置 pending → 重生成）：**4/4 合规**——

| 日期 | summary | events | topics |
|---|---|---|---|
| 09-13 | 27 字纯总述 | 2 条 | 2 |
| 09-15 | 85 字纯总述 | 5 条 | 5 |
| 09-16 | 48 字纯总述 | 4 条 | 4 |
| 09-18 | 75 字纯总述 | 5 条 | 4 |

- `doc.summary` 全部为纯总述（无 `- ` 条目、无换行）；`doc.events` 全部为数组（1–10 条）；`topics` 全为字符串数组；`events empty` 告警 **0 条**；
- 目测：events 条目仍不含钟点时间（延续「条目不要写具体时间」）；BFF 日卡 summary 现为 2-3 句（比拼接版短，符合预期）。

## 15. 命名修订：doc.events → doc.moments（已实施，2026-09-19）

### 15.1 动因

`doc.events`（LLM 重要时刻数组）与 `stats.events`（LLM 输入事件计数）**同名不同义**，消费者读 doc 时易误读（QA 发现）。

### 15.2 设计

- 落库字段定名 **`doc["moments"]`**；**LLM 契约键仍为 `events`**（prompt / `REQUIRED_KEYS` 不变——避免再动已 review 的提示词，映射在 `_generate_one` 单点注释说明）；
- 告警日志改 `day summary moments empty`（原 `…events empty`）；
- 该字段当前**无任何消费者**（§14 已确认），改名零破坏；旧行（含 `events` 键或无该字段）无影响。

### 15.3 改动点

| # | 文件 | 改动 |
|---|---|---|
| 1 | `summary/day.py` | `doc["moments"]` + 局部变量/告警改名 + 映射注释（LLM `events` → doc `moments`） |
| 2 | `tools/lvdata-mcp/tests/test_day_summary.py` | 断言改 `doc["moments"]`；用例改名 `test_generate_moments_stored_and_summary_passthrough` |
| 3 | `services/tests/unit/bff/conftest.py` | 新格式 fixture 字段改 `moments` |
| 4 | `docs/data/lvdata_mcp_service_v2.md` | §5.4.1 字段名 + 修订裁定 |
| 5 | 本文件 | §3.1 / §5 / §14 引用 + 本节 |

### 15.4 实施记录（2026-09-19）

本地回归 **lvdata 184 passed / bff 98 passed**；单/多设备探针通过。

真机全量重跑（重启 → 4 日置 pending → 重生成）：**4/4 合规**——`doc.moments` 全部为数组（2/3/5/6 条，≤10）、无 `doc.events` 残留、`doc.summary` 纯总述、`moments empty` 告警 0 条。
