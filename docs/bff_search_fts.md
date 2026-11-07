# bff /search 切 FTS 设计（结果集收敛到五类展示事件）

> 需求来源：2026-09-21 用户口述——「/search 切 FTS；结果里**不要 topic、不要 audio transcript**；
> 只要 chat / transcribe / translate / agent_job / **video frame** 事件」。
> 基础事实全部取自当前代码（含 file:line）。
> 状态：**已实施（§9；§10.1 放宽回退 2026-09-21 已落地，见 §10.1⑩）**。决策见 §7 / §7.1 / §10.1⑨。
> 范围：bff `/search` 检索实现 + Android 结果列表分组方式；wire schema 不变；不接向量检索（另案）。

---

## 1. 现状调研（代码事实）

### 1.1 现有链路

| 环节 | 事实 | 位置 |
|---|---|---|
| Android 入口 | 点 topic chip → `onTopicSearch(topic)` = `onQueryChange(topic.topic)` → 350ms 防抖 → `MylifeRepository.search(q, cursor)` → `GET /api/mylife/search?q=&limit=50&cursor=`（无缓存直通） | TimelineViewModel.kt（onTopicSearch/pendingForDay 区）、MylifeRepository.kt:158-159 |
| 命中分派 | `kind == "topic"` → 清 query + 定位日卡（:169-172）；其余 → `EventListNavigation(ts, pending)`（EventListViewer，±10min） | TimelineViewModel.kt:167-185 |
| 结果渲染 | 按日**连续分组**（隐含"到达序 = ts 倒序"假设）+ `HH:mm` + snippet 整串高亮；`kindIcon` 已含 chat/transcript/caption/topic/transcribe/translate/agent_job | SearchResultList.kt:38-51（groupHits）、:146-157（highlight）、:159-169（kindIcon） |
| bff 实现 | `build_search`：**全量扫** `list_sessions × timeline_events`，大小写不敏感**子串**匹配 7 类文本；**ts 倒序**（无相关度）；cursor `0|offset`；snippet = 命中词 ±120 字符 | services/bff/search/service.py:39-110（topic:58-65、事件分支:69-91、排序:93-94、分页:96-109） |
| 端点 | `GET /search`，limit 1..200（默认 50）；**响应 schema `SearchHit{date, ts_ms, kind, snippet}`** | services/bff/search/router.py:11-32；services/bff/common/schemas.py:100-112 |

现状已知缺口（docs/android_timeline_topics.md §1.5/§8）：`audio`/`window` 命中不可见（Q3）、`topic` 命中无事件锚（Q4）、全表扫描成本随事件量线性增长（G3）。

### 1.2 lvdata-mcp 侧可复用的检索资产

| 件 | 事实 | 位置 |
|---|---|---|
| FTS 索引 | `search_docs`（FTS5，`text`=CJK bigram 预切分 / `orig_text`=原文）+ 元数据列 `event_ref, session_id, device_uuid, type, ts_ms`（全 UNINDEXED，可 WHERE/ORDER） | tools/lvdata-mcp/src/lvdata_mcp/index.py:16-24 |
| 落库位置 | **`<lvdata_root>/lvdata_inventory.db`**——与 bff 只读打开的是同一个库（pipeline `_conn()` 打开 `store.db_path`；bff `LvdataStore.db_path` 同为该文件） | pipeline.py:69-73；services/bff/common/store.py:68-69 |
| 索引文本 | = `events.summary` **单列**（非空才收录）——payload 内文本不入索引（如 `audio.transcript`；写入方对五类恒写 summary=正文，故对五类无损失） | index.py:86-112（:100-106） |
| 各类型 summary 实际内容 | chat / transcribe / translate = 原文；agent_job = 任务文本（chatlog.py:67-82、93-115）；**video = caption + place + ocr**（写入 vision.py:522-528，生成 :590-596）；audio = LLM 摘要 + 情感名（audio.py:380-389）；window = 窗口一句话概括（vision.py:394 以 caption 写入） | 同左 |
| 查询侧 | `cjk_bigram`（索引/查询同函数）、`build_fts_query`（CJK 连续串 → 引号 bigram 短语；多词隐式 AND）、`is_stopword_query`；`bm25()` 排序、`snippet()` | index.py:27-73、175-204 |
| 新鲜度 | reindex stage 由 watcher 轮询驱动（水位 `index_state`，`lag_sessions` 增量），受 flush 门控（`flush_min_events`=10）；`ensure_index` 在 stage 入口建表 | index.py:115-172；pipeline.py:153-170；configs/dev/lvdata_mcp.yaml:20-21 |
| 向量（本方案不用） | `semantic_search` = FTS + sqlite-vec（DashScope embedding，`lvdata_index.db`）+ RRF 融合；需 API key/网络/索引 | tools.py:106-143；vec.py |

---

## 2. 需求（本次）

1. `/search` 结果集**只含五类事件**：`chat` / `transcribe` / `translate` / `agent_job` / `video`（= 展示口径 `HOT_SLOT_TYPES`，timeline/service.py:28）；
2. **不要 `topic` 命中**（day_summary topics）；**不要 `audio`（transcript）命中**；`window` 随之排除；
3. 检索改走 **FTS（bm25 相关度排序）**。

口径收敛为设计原则：**可搜 ⊆ 可展示**（顺带关闭 Q3/Q4 两个已知缺口）。

---

## 3. 设计

### 3.1 检索源：只读消费 lvdata-mcp 的 `search_docs`

```sql
SELECT event_ref, session_id, device_uuid, type, ts_ms, orig_text,
       bm25(search_docs) AS score
FROM search_docs
WHERE search_docs MATCH ?                       -- build_fts_query(q)
  AND type IN ('chat','transcribe','translate','agent_job','video')
  [AND device_uuid IN (...) ]                   -- 设备白名单（对齐 timeline 语义）
ORDER BY score, ts_ms DESC, event_ref           -- bm25 升序=更相关；tie 稳定
LIMIT ? OFFSET ?
```

- **设备白名单**：与 `/timeline` 同款——`store.list_sessions(device_id)` 得可见设备，`device_uuid IN (...)`；无可见设备 → 空结果（不加过滤是安全漏洞，禁止）。
- **总数**：同条件 `COUNT(*)`（保 UI「共 N 条」）。
- **分页**：cursor 格式 `0|offset` 不变；排序含 `ts_ms DESC, event_ref` 保证跨页稳定。
- **索引缺失（Q6 定案：无回退）**：`search_docs` 不存在（新库/未跑 reindex）→ **空结果** + warning 日志；索引为硬依赖（§8 风险）。
- **空/停用词查询（Q4 定案）**：空串 400（现状保持）；`is_stopword_query` 判定为停用词/无有效 token → **空结果**（total=0）——不可直接落 FTS：「的」这类查询会命中大量无关文档。

### 3.2 查询侧函数与索引侧同源（硬约束）

索引侧与查询侧必须用**同一个** `cjk_bigram`/`build_fts_query`（index.py:28 `cjk_bigram` docstring「索引与查询两侧同函数」即此约束）。bff 侧方案（Q1 定案）：
bff 内复制（约 30 行）+ 双侧注释互指 + 契约测试（测试内 import `lvdata_mcp.index` 对比两实现输出，缺包则 skip——漂移由 CI 捕获，不新增运行时依赖）。

### 3.3 响应映射（wire 契约不变）

- `SearchHit{date, ts_ms, kind, snippet}` 不变；`date` 由请求时区换算（沿用 `_local_date`）。
- `kind` = 事件 type（`video` 直映射，Q2 定案）。
- `snippet` = `_snippet(orig_text, q)`：**整串优先、否则首个命中词**定位（±120 字符）。单词语义下必含查询词（高亮契约 ✓）；多词查询（空格分隔、FTS 隐式 AND）可命中非相邻词 → 高亮可能缺省（客户端 `highlight` 已有"无匹配则不高亮"兜底，SearchResultList.kt:147-149）。
- 排序由 ts 倒序改为 **bm25 相关度序**（tie: ts 倒序）。

### 3.4 Android 影响（小改）

| 清单号 | 改动 | 原因 |
|---|---|---|
| A1 | `SearchResultList`：`groupHits` 连续同日分组 → **按日聚合**（LinkedHashMap<date, hits>，section 按首现顺序，组内保持 bm25 序）；`kindIcon` 补 `video` → 🖼️ 并**删除死条目** `transcript`/`caption`/`topic`/`summary`（保留 📍 兜底） | 相关度序下同日命中不再相邻（现实现产出重复日头）；kind 集合收敛为五类（Q2 定案） |
| A2 | `TimelineViewModel`：**删除** `onSearchHitClick` 的 `kind=="topic"` 分支及对应测试 | 结果里不再有 topic（Q3 定案） |

事件→查看器的链路（`EventListNavigation` → ±10min → 锚点定位/追击）**零改动**。

### 3.5 非目标

- 不接向量（`semantic_search` 路线：**每次查询需实时 embedding**、失败面/延迟/成本见 §8 备选 B）；
- 不改 `SearchResponse` schema、不改分页 cursor 语义、不改 Android 导航与查看器。

---

## 4. 改动清单（待实施）

| # | 文件 | 改动 |
|---|---|---|
| B1 | `services/bff/common/store.py` | 新增只读方法：`search_fts(match, types, devices, limit, offset)` + `count_fts(...)` + `has_search_docs()`（表存在判定，复用 `_table_exists`）。**`devices=[]`（空列表）必须返回空**——对齐 `_events_where` 既有语义（store.py:200-202「空列表 ≠ 不过滤」），安全关键 |
| B2 | `services/bff/search/service.py` | `build_search` 改走 B1：bm25 序 + 五类过滤 + 设备白名单 + COUNT/分页；snippet 走 `orig_text`，**`_snippet` 需扩展为「整串不匹配时按首个命中词定位」**（多词 AND 场景下原文可能不含连续整串）；停用词 → 空结果；`search_docs` 缺失 → 空结果 + warning（无回退）；**删除既有扫描与 topic/audio/window 分支**；连带死码清理：`topic_names` 导入、`_parse_started`、`_hit` 的 `session_date` 参数与无-ts 回退分支（新实现所有命中都带 `ts_ms`）、`day_summaries` 调用（`_snippet`/`_local_date` 保留） |
| B3 | `services/bff/search/fts.py`（新，或并入 service.py） | 查询侧 `cjk_bigram`/`build_fts_query`/`is_stopword_query` 副本 + 注释互指 `lvdata_mcp/index.py` |
| B4 | `services/bff/common/schemas.py` | `SearchHit.kind` 注释同步为实际产出集（`chat \| transcribe \| translate \| agent_job \| video`） |
| A1 | `SearchResultList.kt` | `groupHits` 改按日聚合（抽 `internal` 便于单测）；`kindIcon` 收敛（`video` + 删死条目） |
| A2 | `TimelineViewModel.kt` | **删除** `onSearchHitClick` 的 topic 分支（Q3） |
| D1 | 文档 | 本文；同步 `docs/android_timeline_topics.md`（§1.5 事实 a/b/f、§3.5 kind 集合、§5 矩阵 #5/#6/#15/#16）与 `docs/mylife/timeline-android-v2.md`（§5.3） |

---

## 5. 边界矩阵

| # | 场景 | 行为 |
|---|---|---|
| 1 | 查询命中 audio/window/topic 内容 | **不出现在结果**（类型过滤；旧行为分别产出 transcript / caption / topic 命中） |
| 2 | video 命中来源 | 索引文本 = caption+place+ocr：命中词可能不在查看器展示的 caption 里（OCR/place 命中）→ 命中事件本身仍可见（帧），仅高亮语义弱化 |
| 3 | 无帧 video（不可展示） | 索引里有（summary 非空即收录），但查看器剔除 → 锚点退化定位（既有 Q5 兜底） |
| 4 | role 白名单外的 chat/transcribe/translate | 可被搜到但查看器不渲染该行 → 退化定位（既有兜底） |
| 5 | 多词查询（空格分隔）/ 查询含标点等**整串不匹配**场景 | FTS 隐式 AND、词可不相邻（旧行为：整串必须连续出现）→ 召回扩大；snippet 按首个命中词定位、客户端整串高亮可能缺省 |
| 6 | ASCII 词查询 | 整词匹配（`hell` 不再命中 `hello`）；CJK 仍是连续子串语义（bigram 短语） |
| 7 | 大小写 | FTS unicode61 不敏感 ✓ 与现状等价 |
| 8 | 设备白名单 | `device_uuid IN（可见设备）`；无可见设备 → 空结果 |
| 9 | `search_docs` 缺失 | **空结果** + warning（Q6：索引为硬依赖，无回退） |
| 10 | 索引陈旧（reindex 滞后 / watcher 停摆） | 短暂漏命中（Q5 已接受）；无回退路径 |
| 11 | 空串查询 | 400（现状保持） |
| 12 | 停用词 / 无有效 token 查询 | **空结果**（Q4 定案；不落 FTS，防「的」类查询命中一片） |
| 13 | 分页 | `ORDER BY score, ts_ms DESC, event_ref` + `0|offset`；跨页不重叠、total 恒定 |
| 14 | limit 边界 | 1..200（router 不变，422 语义保持） |
| 15 | 结果排序 | bm25 相关度优先（不再 ts 倒序）；同日命中不再相邻 → A1 分组改版 |
| 16 | 高亮契约 | snippet 必含查询词（单词语义）；`kindIcon` 收敛为五类 + 📍 兜底（旧服务端若仍发 caption/transcript 不崩） |
| 17 | 查询串含 ASCII 标点（`. / @ ? : * +` 等）或大写 FTS 关键字（`AND`/`OR`/`NOT`） | **已修复（§10.3）**：`build_fts_query` 改为词一律加引号 → 零语法歧义；修复前是 FTS5 语法错误 → `count_fts` 吞错返 0（空转 + warning）→ 放宽回退兜底 |
| 18 | **单字 CJK 查询**（如「天」） | **不命中**——索引为 bigram 语义（多字串只产生双字 token，无单字 token；实测 `天` → 0、`天气` → 命中）。客户端 `MIN_QUERY_LENGTH=2` 已挡（短查询落 Idle），故 app 侧不可达 |
| 19 | 纯标点查询（`——`/`。。。`/`· ·`） | 切词后无 token → **空结果早退**（不落 FTS：空匹配串 `MATCH ''` 是语法错误，会走异常兜底并刷 warning）；回归用例断言"无 warning" |

---

## 6. 测试计划

**bff pytest**（`cd services && pytest tests/unit/bff`；junitxml 核对计数）
- **夹具**：conftest `_build()` 增建 `search_docs`（按 index.py 的 bigram 规则灌 `events.summary`；测试内 10 行实现、注释互指 index.py，不 import lvdata_mcp）。**只新增派生表，不改 events/sessions/day_summary 种子** → 其余端点断言零牵动。
- **改写既有 `test_search_api.py`（现 21 例，逐例处置）**：
  - **删除（3）**：`test_search_summary_and_topic`、`test_topic_hit_ts_ms_is_session_started_at`（topic 命中不再存在）、`test_hit_without_ts_falls_back_to_session_date`（`_hit` 的 session_date 回退分支一并删，B2）
  - **重写（6）**：`test_search_chat_and_transcript_hits`（去掉 transcript/topic，kind 集合断言改）、`test_search_caption_hits_video_and_window`（window 排除、kind=`video`）、`test_search_ts_desc_order` → 相关度序、`test_search_timezone_affects_date`（命中载体改 chat/video，原例靠 transcript/topic）、`test_search_pagination`（原「天」查询只剩 0 命中，需换语料重写）、`test_sessions_without_events_never_produce_hits`（断言 kind 集合改）
  - **保留微调（7）**：`test_search_case_insensitive`、`test_search_no_result`、`test_search_empty_query_400`、`test_search_invalid_cursor_400`、`test_search_device_filter`、`test_search_limit_bounds`、`test_snippet_window`（若做多词 snippet 扩展则补用例）
  - **本批（2026-09-21）新增 5 例处置**：`test_search_transcribe_translate_hits`、`test_search_new_kinds_pagination_no_overlap` 保留（前者的夹具改为 summary 列=正文，索引只覆盖该列）；`test_search_agent_job_fallback_and_highlight_contract` **改写**——agent_name→summary 回落链仅存于查看器展示，**搜索侧已不存在**（索引只覆盖 summary 列），并入 `summary 列命中` 断言；`test_search_mixed_kinds_ts_desc` → 同相关度按 ts 倒序的 tie-break；`test_search_transcribe_vs_transcript_distinct` **删除**（transcript 侧不再有命中，区分场景消失）
- **新增**：五类过滤（audio/window/topic 查询 → 0 命中）；bm25 排序（构造"高相关但较早"的命中应排在"低相关但最新"前）；`kind=video` 产出；设备白名单；分页稳定不重叠；多词查询（非相邻词命中）；ASCII 整词语义；停用词 → 空结果；`search_docs` 缺失 → 空结果 + warning（无回退）；契约测试（两侧 bigram 实现一致，缺 lvdata_mcp 时 skip）。
- `services/bff/common/schemas.py` 注释改动无测试影响。

**Android JVM**
- `groupHits` 建 `internal` 后单测：同日命中不相邻时聚合为单一日头（首现顺序）、组内保持到达序。
- `TimelineViewModelTest`：**删除** `搜索 topic 命中定位日卡而非 EventList（Q4）` 用例（分支已删，Q3 定案）。

**Android 仪器**（可选）
- `kindIcon(video)` 图标断言（并入现有 chips/结果列表用例即可）。

**手工验收**：点 topic → 结果只含五类；相关度排序（同词多命中时高相关在前）；点命中 → ±10min 查看器与锚点高亮链路不变；audio/window 内容查询 → 无结果（预期内）。

---

## 7. 决策记录（Q1–Q7，2026-09-21）

| # | 事项 | 决策 |
|---|---|---|
| Q1 | 查询侧 bigram 函数的出处 | **bff 内复制 + 注释互指 + 契约测试**（未收到异议，按推荐执行；若改为 `import lvdata_mcp.index` 请指出） |
| Q2 | video 命中的 `kind` | **`video`**（= 事件 type 直映射；客户端 `kindIcon` 补图标）（用户拍板） |
| Q3 | 客户端 `topic` 分支（死代码） | **删除**（含对应测试）（用户拍板） |
| Q4 | 停用词/无有效 token 查询 | **空结果**（total=0，不落 FTS、无错误态）（用户拍板） |
| Q5 | 索引陈旧窗口 | **可接受**（reindex 轮询 60s + flush 门控；后续可加会话封存钩子）（用户拍板） |
| Q6 | 回退扫描去留 | **移除**——`search_docs` 为硬依赖，缺失即空结果 + warning（用户拍板） |
| Q7 | 备选 D「bff 经 MCP server 调 `search()`」（用户 2026-09-21 提出评估） | **不采纳**：① 工具签名不足（单 `type`、无分页/无 total、无 device 过滤、`snippet` 为 bigram+`<b>`、`text` 截断 200 字符——工具面为"agent 探查"非数据面）→ 需先扩 MCP 工具签名并兼容 agent 消费者；② 可用性/延迟耦合（MCP 挂=搜索挂，受 `max_ip_conn` 限流与 agent 抢额度）；③ bff 需引入 MCP 客户端（async 桥接）+ token 依赖；④ 同机同库，解耦收益为零。**重评条件**：MCP 提供面向服务的数据面 API（types 集合/device/cursor/total/原文片段）或与 bff 分机部署——届时仅替换 `store.search_fts(...)` 实现（§8 备选 D）（用户拍板） |

---

## 8. 风险与备选

- **跨服务 schema 耦合**：`search_docs` 归 lvdata-mcp（index.py 单点），bff 只读消费——索引结构/分词规则变更必须双侧同步；缓解 = 双侧注释互指 + 契约测试（§6）。bff 只读打开（`mode=ro`，store.py:83-88）不受影响。
- **bff 运行时需 sqlite3 内置 FTS5**：`MATCH` 依赖 FTS5 模块（CPython 官方构建默认包含）；CI 的 bff 搜索测试（夹具建 `search_docs`）即覆盖此依赖，自编译解释器部署需确认。
- **召回语义变化**（产品可见）：ASCII 由子串变整词、多词由"整串连续"变 AND、结果由时间序变相关度序；上线前需产品确认（尤其"点 topic 搜"的既有预期）。
- **索引陈旧 / watcher 停摆（Q5 接受 + Q6 无回退）**：命中短暂缺失且**无兜底**——索引停更即"搜不到"；状态已可观测，建议接入部署告警——**2026-09-27 起改用 REST `GET /api/status`**（原 `get_status` MCP tool 已迁 REST，index.lag_sessions/last_rebuild_at 字段同构）。
- **索引为硬依赖（Q6）**：`search_docs` 缺失 → 空结果。dev/CI 由测试夹具自建；生产由 watcher **每轮自动维护**（`ROUND_STAGES` 含 `reindex`，watcher.py:40；首轮即建）——**无需手工 reindex**，但 watcher 未运行时索引不更新。
- **备选 A（最小改动）**：只做类型过滤（chat/transcribe/translate/agent_job/video），排序仍 ts 倒序——零索引依赖、无需客户端分组改版，但拿不到相关度收益。
- **备选 B（`semantic_search` 路线，另案；2026-09-21 评估）**：FTS + 向量混合（RRF 融合）。要点（tools.py:106-143、vec.py）：
  - **每次查询都要实时 embedding**：先 `provider.embed([query])`（tools.py:127，DashScope 兼容端点、需 API key）拿到查询向量，**之后**才开始检索；FTS 腿写在 embedding 之后的顺序（tools.py:130-137），且 embedding 抛异常即整体失败（`embedding_failed`）——**FTS 不兜底**；未配置 provider 直接返回 `embedding_disabled`（tools.py:118-129）。
  - **失败面/延迟/成本**：搜索整体依赖外网 embedding 端点存活 + 每查询一次往返（数十~数百 ms）；索引侧另有批量 embedding 成本（`sync_changed` batch 10，vec.py:197-245）与 `lvdata_index.db`（sqlite-vec，1024 维）运维。
  - **若启用则作为开工前置的优化**：查询向量加 LRU（topic 名高度重复，单卡 ≤50 个查询串、全量可枚举）、embedding 失败降级纯 FTS、服务端 embedding 配额/延迟预算确认。
  - 价值：同义/近义召回（子串与 bm25 都拿不到的能力）——若产品实测反馈"搜不到想找的"，再按上述前置启用。
- **备选 C**：由 lvdata-mcp 暴露 HTTP 数据面供 bff 代理——当前 MCP 无 HTTP 数据面，成本高于直读 SQLite，不采纳。
- **备选 D（2026-09-21 评估，不采纳，见 §7 Q7）**：bff 经 MCP server 调 `search()`（streamable HTTP + JSON-RPC，server.py:37-39）。问题：① **工具签名不足**——单 `type`（五类需 5 次调用）、无 cursor/total（App 分页与「共 N 条」缺失）、无 device 过滤（设备白名单安全语义丢失）、`snippet` 为 bigram 化 + `<b>` 标记的机器读片段、`text` 为 `orig_text[:200]` 截断（tools.py:53-96，工具面 = "agent 探查"而非数据面）；② **可用性/延迟耦合**——MCP 不可用/超时/401/429 即搜索不可用，且受每 IP 并发 32 限流与 agent 抢额度（server.py:137-159）；③ **接入复杂度**——bff 需引入 MCP 客户端（async）并在同步端点桥接事件循环 + 配置/轮转 token；④ **解耦收益为零**——两者同机、同读 `<root>/lvdata_inventory.db`。**重评条件**：MCP 提供数据面 API（types 集合/device/cursor/total/原文 snippet）或二者分机部署；届时仅需替换 `store.search_fts(...)` 的实现（本方案已将其收敛为单一入口，B1）。

---

## 9. 实施记录（2026-09-21，落地完成）

| 项 | 结果 |
|---|---|
| B1 | `store.search_fts/count_fts/has_search_docs/_fts_where`：bm25 升序（tie: ts 倒序 + event_ref）；`devices=[]` → 空；表缺失/查询异常 → 空 + warning ✓ |
| B2 | `build_search` 重写：空串 400（保持）→ **cursor 先解析**（400 语义与索引状态无关）→ 停用词 → 空结果 → 索引缺失 → 空 + warning → 设备白名单（可见设备集合）→ COUNT + bm25 分页；删除扫描与 topic/audio/window 分支及死码（`topic_names` 导入、`_parse_started`、`_hit` 的 `session_date` 参数与无-ts 回退分支）；`_snippet` 扩展多词退化（整串优先 → 首个命中词）✓ |
| B3 | `bff/search/fts.py`：查询侧三函数副本（`CJK_RE` 用等价转义写法）+ `query_terms`（供 snippet 复用）；与 `lvdata_mcp.index` 的等价性经 20 样本手工核对（含标点/混合样本，mismatch=0）+ 契约测试守护 ✓ |
| B4 | `SearchHit.kind` 注释 → 五类实际产出集 ✓ |
| A1 | `HitGroup`/`groupHits` 提 `internal` + 按日聚合（LinkedHashMap 首现序）；`kindIcon` 收敛为五类（+`video`→🖼️、删 transcript/caption/topic/summary 死条目，保留 📍 兜底）✓ |
| A2 | `onSearchHitClick` 删 `kind=="topic"` 分支（及 `TimelineViewModelTest` 对应用例）✓ |
| 夹具 | conftest `_build()` 建 `search_docs`（用 **bff 自身** `cjk_bigram` 灌 `events.summary`，保证索引/查询同函数；只新增派生表，不改种子）；`test_search_api._insert_event` 同步维护派生索引 ✓ |
| 测试 | `test_search_api.py` 全量重写为 **22 例**：五类过滤（audio/window/topic → 0）、相关性优先于时间、同相关度 ts 倒序 tie-break、多词隐式 AND（+snippet 首个命中词）、ASCII 整词、停用词空结果、索引缺失空结果、payload-only 不入索引、时区、分页、契约测试（`importorskip("lvdata_mcp.index")`）等 ✓ |

**验证**：bff `cd services && pytest tests/unit/bff` → **113 passed / 2 failed**（2 条 `test_gzip.py`）。**该 2 例已查明非代码问题（2026-09-21）**：它们钉的是 starlette **1.6.0** 行为（排除表含 `image/jpeg`/`audio/*`/`video/*` 且跳过 206），项目 `.venv` 即 1.6.0、**裸 `python` 是 `~/ven/python312`（starlette 1.1.0，排除表只有 `text/event-stream`、无 206 跳过）**——用错解释器必失败。**正确姿势 = `source .venv/bin/activate` 后跑（REPO.md:70）**，此后 bff 全绿（§10.2 末验证行）。Android `testDebugUnitTest` → **528 tests / 0 failures**（新增 `SearchResultListGroupingTest` 2 例；`TimelineViewModelTest` 32 例）。

**与文档的偏差（1 处，已同步 §6）**：`test_search_agent_job_fallback_and_highlight_contract` 原标"保留"，但新索引只覆盖 `events.summary` 列——**搜索侧**的 agent_name/summary 回落链已不存在（仅存查看器展示回落），该用例改写为 summary 列命中断言（并入 `test_search_agent_job_hit`）。

**复核修订（2026-09-21，实现 review 后）**：① **纯标点查询早退**——原实现会把空匹配串送进 FTS（`MATCH ''` 语法错误 → 走异常兜底：结果虽为空但每次请求刷 warning），现 `if not match: 空结果` 前置（用例断言无 warning）；② **负/越界 cursor 钳制** `max(0, min(start, total))`（SQLite 负 OFFSET 当 0，但原 next_cursor 会错位）；③ 新增 5 个边界用例（纯标点、大写关键字、单字 CJK、负 cursor、`SEARCH_TYPES == HOT_SLOT_TYPES` 防漂移）；④ 矩阵补 #18/#19、#5 措辞扩至含标点。

**未做（待人工）**：手工验收（点 topic → 五类结果、相关度排序、命中 → ±10min 查看器）。部署侧**无需手工 reindex**——索引由 watcher 每轮自动维护（`ROUND_STAGES` 含 `reindex`，watcher.py:40）；仅 watcher 未运行时 `/search` 恒空（Q5/Q6 已接受）。

---

## 10. 后续：topic 点击的召回问题（2026-09-21 分析；处置 = 暂停点击）

**现象（真机实测反馈）**：点 topic chip → `/search?q=<topic 名>` **大部分 0 结果**。

**根因 = 抽象层级错配（非索引能力问题）**：topic 是 day_summary 的 LLM 对**一天内容做的抽象命名**（"股票市场分析"/"微博资讯浏览"），而 FTS 索引的是**具体事件的文本**（`events.summary`，如"股票怎么看"）——要求抽象短语**字面连续**出现在事件文本里，命中率天然极低。事件级向量（`semantic_search`）也只是**部分缓解**：查询与文档仍不同抽象层、召回带噪声，且要付每查询 embedding 的既有代价（§8 备选 B）。

**备选（按成本递增）**：

| 方案 | 做法 | 成本 | 预期 |
|---|---|---|---|
| A. topic 查询放宽 | topic 名拆 bigram 做 **OR 检索** + bm25 覆盖度排序（可加"至少命中一半 bigram"后过滤）；"股票市场分析" → 命中"股票怎么看" | 小（bff 加 topic 专用查询模式，当前索引零新依赖；与 `build_fts_query` 契约的关系需单列） | 从"大部分 0 结果"到"有排序的相关结果"；可能带常识性噪声 |
| B. 按日匹配（层级对齐） | topic 与其来源日的关联**只**在 day_summary；而客户端 overview 已持有 → 点 topic 定位"包含该 topic 的日期"，展示那些日的事件（复用 EventListViewer 行件；需支持多日/当日窗口） | 中（客户端为主 + 列表窗口形态） | 必然有内容、语义最准；不做同义泛化 |
| C. day 级向量 | 把 day_summary（topics + summary + moments）做**日级** embedding——查询（topic 名）与文档同层，文档量小、增量便宜 | 大（lvdata-mcp 加 day 级 embedding 通道 + 每查询 embedding 代价） | 最好的语义泛化（同义/跨表述） |

**当前处置（2026-09-21 用户拍板）**：**topic chip 暂不允许点击**——客户端暂停入口（`TimelineScreen` 不再接线 `onTopicSearch`；`TopicChipRow` 保留可点模式、重启用 = 接回一行回调）；chip 随之改**中性色**（金色语义 = 可点的 hot slot，避免误认）——**定稿样式：透明底 + `Line` 1dp 描边 + `Dust` 字（描边标签档，实机渲染拍板，见 docs/android_timeline_topics.md §3.1 视觉修订）**。方案 A/B/C 待后续按实测选定后再恢复点击。**（2026-09-21 更新：点击已恢复**——数据侧提取规则 + §10.1 放宽回退落地后，全语料可搜率 96%（旧 topics）/88%（新）；`TimelineScreen` 已接回 `onTopicClick`。**2026-09-21 再更新：chip 视觉回归描边档**——金色药丸实机反馈太抢眼，点击保留（同一描边视觉叠加 `Modifier.clickable`）；详见 docs/android_timeline_topics.md §3.1。）

**2026-09-21 后续：改数据侧而非检索侧（A/B 实测通过）**。day_summary 的 system prompt 改为「**topic 的主题名必须从事件的摘要里面直接提取，不做任何修改和拼接，最多 5 个，可以没有**」，并把 few-shot 示例的 `topics` 从占位值（`topic1/topic2`）改为具体可提取短语（`启动耗时/性能优化`）——见 docs/day_summary.md §3.2。<br>（v3 措辞，2026-09-21 二次收紧：「不做任何修改和拼接」直接针对实测暴露的两种失败模式——拼接词（A股行情=A股+行情）与改写词（股市不在文中）；「可以没有」允许该日无合适提取词时返回空数组。）实测（3 天真实风格事件 × 3 种 prompt，生产同款链路 qwen3.7-flash + `json_object`；topic 判定 = 字面出现在事件文本 + 生产同款 FTS（bigram 短语）命中）：

| prompt | 提取率（topic 字面出现在事件里） | FTS 可搜 | 典型问题 topic |
|---|---|---|---|
| 旧规则 + 占位示例 | 5/9（56%） | 提取者 1–2 命中；自造词 **0 命中** | 工作协调 / 影视娱乐 / 信号处理 / 饮食 / 休闲娱乐 |
| 新规则 + 占位示例 | 7/10（70%） | 同上 | 娱乐休闲 / 新闻阅读 / 运动 |
| **新规则 + 具体示例（采用）** | **11/12（92%）** | 全部 ≥1 命中（含 2 命中样例） | 运动（羽毛球日的轻度抽象） |

结论：**「点 topic → 搜到内容」在新生成的日成立**，无需引入向量检索；未观察到照抄示例（12 个 topic 中 0 例外）。两个已知边界：① 存量 day_summary 仍为旧式抽象 topic（重生成受 `up_to_date` 护栏限制——需该日有新事件或显式 force），需一次全量重生成才能整体生效；② 新式 topic 更**具体**（「封神演义」而非「影视娱乐」），按"搜索入口"的用途更合适，但概念聚合度下降（同一主题跨天不再自动归并）——如需两者兼得，可后续让 LLM 同时输出"提取词 + 归类名"两档。

**2026-09-21 真实数据复测（重要修正：合成样本高估了模型）**。本机真实数据全量重生成 7 天（`/opt/lvdata`，旧 rows 备份 `/tmp/day_summary_backup.json`），同一把尺子（字面 + 生产同款 FTS，按当日全部事件文本）测量：

| topics 来源 | 精确可搜（bigram 短语命中） | 放宽检索（OR bigram，≥1 词） | 放宽 + ≥半数词覆盖 |
|---|---|---|---|
| 旧式（重生成前） | 2/24（8%） | 18/24（75%） | 5/24（21%） |
| v2 规则（"提取、不重新发明"） | 7/24（29%） | 19/24（79%） | 12/24（50%） |
| **v3 规则（"不做任何修改和拼接，可以没有"）** | **12/24（50%）** | — | **合计精确+放宽 21/24（88%）** |

- 改数据侧**有净收益但不是完整解**：精确可搜 8%→29%（3.6×），真实事件文本比合成样本"脏"得多；
- 残余失败机制（已逐条核实）：① **拼接词**（「A股行情」= A股+行情、「财经新闻」= 财经+新闻——组成部分在文中、连起来不在）；② **轻微改写**（「股市行情」里的"股市"在事件文本中根本不存在）；
- **搜索侧"放宽回退"是必要补充**：精确 0 命中时改用 bigram OR + 覆盖度/bm25 排序 → 可搜率升至 ~79%（两代 topics 都受益，存量数据无需重生成即修复）；
- 另：新 topics 命中"质量"更高（太乙真人→12 命中、海南发展→4、吃橘子→4；旧 topics 的 2 个命中更接近巧合）。
- **2026-09-21 再演进（已实施，§10.2）**：topics 改为**对象数组 `{topic, search_words}`**——「展示名」与「搜索词」解耦（搜索改用 `search_words`）；设计与实施清单见 docs/android_timeline_topics.md §10。搜索侧**零改动**（越界场景仍由 §10.1 放宽回退兜底）。

### 10.2 topics 对象化（`{topic, search_words}`）实施记录（2026-09-21 落地）

**动因**：v3 规则下 topic 名同时承担"展示名"与"搜索词"两个角色，二者目标冲突——展示要求可概括（「工作界面」）、搜索要求字面命中（「recording_data_process.md」）。对象化把两个角色拆成两个字段。

**改动清单（8 项，全部落地）**：

| # | 位置 | 改动 |
|---|---|---|
| T1 | `summary/day.py::DAY_SYSTEM_PROMPT` | schema/示例/规则句改为对象形态；新增"不要输出事件类型标签（chat/translate/agent_job/video frame）、不要照抄整句" |
| T2 | `summary/day.py::_normalize` | topics 归一为对象数组；legacy str → `{topic, search_words: None}`；缺 topic/空串/畸形条目丢弃；空白 `search_words` → null |
| T3 | `bff/common/topics.py` | 新增 `topic_items()` 返回 `[(name, search_words)]`（读侧兼容 str/dict+detail）；`topic_names()` 改为其投影（graph 等消费者不变） |
| T4 | `bff/common/schemas.py` / `timeline/service.py` | `DayTopicItem` 增 `search_words: str | None = None`（`detail` 保留字段、响应恒 null，S2）；`_day_topics` 改走 `topic_items` |
| T5 | Android `DayTopicDto` / `TimelineViewModel::onTopicSearch` | DTO 增 `search_words`；点击优先用 `search_words`，缺省/空白回退 topic 名（chip 显示名不变） |
| T6 | `configs/dev/lvdata_mcp.yaml` | summary model 由 qwen3.8-flash 回落 **qwen3.7-flash**（用户指定复测口径）；真实数据 7 天全量重生成 |
| T7 | 文档同步 | 本文 §10.2 + `docs/day_summary.md` §3.2 + `docs/android_timeline_topics.md` §10 |
| T8 | `apps/web/mylife/src/api/types.ts` | **不适用**：该 `TopicItem{topic, mentions}` 属 legacy `/sessions` 载荷（session_summary 已消亡、`mentions` 为死字段），与 day_summary topics 无数据关系 |

**真实数据复测（3.7 + 对象化 schema，本机 7 天全量重生成，度量 = 生产同款 FTS bigram 短语 × 当日全部事件文本）**：

| 口径 | 结果 |
|---|---|
| 形态合规（对象数组、可用字段） | 14/14（100%） |
| `search_words` **字面**出现在当日事件文本 | 14/14（100%） |
| `search_words` **精确可搜**（bigram 短语 ≥1 命中） | **13/14（93%）** |
| 放宽回退后可搜（唯一未精确命中项） | 14/14（100%） |

- 对比 v3 字符串 schema 的真实数据成绩（精确 50% / 合计 88%），对象化把"字面可搜"从 ~50% 抬到 **100%**：模型不再被迫在"可概括"与"可搜索"之间二选一；
- 唯一非精确命中项：`{topic: "工作界面", search_words: "recording_data_process.md"}` ——ASCII 文件名**含 `.`** → `build_fts_query` 原样透传 → FTS5 `syntax error near "."` → `count_fts` 吞错返回 0 → **放宽回退接管并返回 2 条命中**（行为正确，代价是一次失败查询 + 一条 warning 日志）。边界与处置见下；
- 提取质量样例：`{取消复盘任务, 取消这个上面的那个那个那个任务吧}`、`{海南发展, 海南发展}`、`{股市行情, 今日收盘三大指数集体走强}`——search_words 是"能从事件原文直接命中的最短连续片段"，topic 名保持可读。

**ASCII 标点边界 → 已修复（2026-09-21，见 §10.3）**。原实现对非 CJK 词原样透传，故 `.` `@` `?` `:` `*` 等 ASCII 标点与大写 `AND/OR/NOT` 会被 FTS5 当语法解析并抛错，靠 §10.1 放宽回退兜底（用户可见正常，代价是精确路径空转一次 + warning）。修复后该边界消失。

### 10.3 查询串安全修复：`build_fts_query` 一律加引号（2026-09-21 实施）

**问题**：`build_fts_query` 对**非 CJK 词原样透传**（仅 CJK 连续串加引号），于是纯 ASCII 词里的 FTS5 语法字符会触发 `sqlite3.OperationalError`。逐字符实测（真实 FTS5 表）：

| 类别 | 触发字符 | 报错形态 |
|---|---|---|
| ASCII 标点（28 个） | `! # $ % & ( ) , . / : ; < = > ? @ [ \ ] ^ `` ` `` { \| } ~ + *` | `syntax error near "<c>"` |
| 位置敏感 | `*` 前导（`*abc`）/ `^` 非前导（`abc^`）/ `+` 尾随（`C++`）/ `a**` | `unknown special query` / `syntax error` |
| 列过滤语法 | `:` `{` `}`（`a:b` `a{b` `a}b`） | `no such column: <c>` |
| 大写关键字 | `AND` / `OR` / `NOT` 单独或位置非法（`OR abc`、`abc OR`） | `syntax error near "AND"` |
| **安全** | `_`（合法 bareword 字符）；`"` `'` `-`（查询切词集内，进 FTS 前已被吃掉） | — |
| 不报错但异常 | 非 ASCII 标点（`“”` `～` `％` `ＡＢＣ`）：透传后被 unicode61 当分隔符 → 等效切词；全角字母数字**静默 0 命中** | — |

**免疫面**：词里含任一 CJK 字符即走 bigram 分支并被引号包裹 → `记录v1.2.3`、`test.测试`、`3.5寸`、`市值100.5%` 全部安全。**风险面仅纯 ASCII 词**，而 3.7 的 `search_words` 恰恰会照抄原文里的文件名/URL/英文句（实测 `recording_data_process.md`、`What's your name?`）。

**修复**：`build_fts_query` 改为**词一律双引号包裹**（`"abc" "世界"`；`cjk_bigram` 对非 CJK 词原样返回，故分支消失）。全标点词加引号后是**空短语**（`"..."` → 0 命中，实测不报错）。

- **两侧同改**（契约测试 `test_fts_helpers_match_lvdata_mcp` 逐样本守护）：查询侧 `services/bff/search/fts.py:57`（App `/search`）+ 工具侧 `tools/lvdata-mcp/src/lvdata_mcp/index.py:58`（MCP `search()` 工具、`lvdata-mcp eval`）。**不需要 reindex**——`build_fts_query` 不参与索引写入（写库只用 `cjk_bigram`），存量索引照旧可用。
- **代价（已接受）**：放弃 FTS5 原生通配与布尔语义（`abc*` 前缀查询、`a AND b` 组合）——App 搜索框与 topic `search_words` 都是自然文本，无此需求；`abc*` 加引号后退化为按 token 精确匹配。
- **测试**：新增 `test_build_fts_query_always_valid_fts5`（19 个报错样本 + 正常样本，**非空** MATCH 串不得抛错；措辞修正——"任意输入"不成立，空输出单列）、`test_build_fts_query_empty_output_contract`（纯分隔符 → `""`，且 `MATCH ''` 必抛——调用侧守卫存在的理由）、`test_search_query_syntax_chars_no_store_error`（端到端断言不落 `query failed` 告警——修复前必落，这是能真正区分修复前后的断言）、契约样本表补 7 个标点/关键字样本 + 3 个纯分隔符样本（两侧同判空）；`test_index.py::test_build_fts_query` 期望值同步。
- **线上验证**（HTTPS 8080 实服务）：`recording_data_process.md` 2 命中（改前靠放宽、改后走精确，结果一致且无 warning）、`What's your name?` 3 命中（改前放宽 =11，精确后更收敛）、`C++` / `v1.2.3` 不报错（前者 3 命中为单字母 token 的 FTS 固有噪声）。
- **模糊测试**：随机串 2 万条（ASCII 标点 + CJK + 全角标点混合字母表）过真实 FTS5 → 新实现 0 报错（仅 5 例纯分隔符串产出空 MATCH，调用侧已前置守卫），旧实现 **93% 报错**（18592/20000）。
- **连带修复（同批）**：MCP `search()` 工具缺 bff 同款的前置守卫——纯分隔符查询（`——`/`- -`）产出空 MATCH → 走到 broad `except` 被误报为 **`index_not_built`「请运行 reindex」**（索引其实好好的）。已在 `tools.py` 补 `if not fts: return invalid_param`，用例 `test_search_separator_only_query`。**另两处入口同批补齐（2026-09-21 review 扫尾）**：`semantic_search` 同款守卫（该工具无兜底 `except`，空 MATCH 会以未捕获的 `sqlite3.OperationalError` 返回）、`lvdata-mcp eval`（`main.py`）跳过空 MATCH 的 golden query 并记 warning——`build_fts_query` 的三个调用点至此全部带空守卫。

### 10.1 放宽回退：精确 0 命中 → bigram OR（**已实施 2026-09-21**）

**目标**：把"拼接词/轻微改写"类 topic 的可搜率从精确的 29%（新）/8%（旧）抬到 ~79%，且**不动精确路径**、不动 wire schema、客户端零改动。

**触发条件**：仅当精确查询（`build_fts_query` 的 bigram 短语）`total == 0` 时启用。副产品：topic 只有 1 个检索词时放宽查询与精确查询等价 → 天然 no-op（回退只对多词/拼接场景生效）。

**① 检索词构造（`fts.relaxed_terms`，与索引侧同源）**：复用 `cjk_bigram` 拆 topic——CJK 连续串 → 重叠双字；ASCII → 整词；**丢弃 len<2 的 token**（实测例：`A股行情` → 完整拆分 `A/股行/行情`，丢 `A` 后 = `股行`、`行情`；保留 `A` 会命中一切含 A 的文本）。**已知边界**：由"单字 CJK + 数字/单字母"构成的 topic（如「第1讲」→ 第/1/讲 全被丢）词集为空 → 不回退、维持空结果——刻意如此，保留单字 token 会引入更大噪声。

**② 查询**：`build_relaxed_query(terms)` → `"股行" OR "行情"`（FTS5 原生 OR，零新依赖）。空词集 → 直接空结果。

**③ 排序 = 纯 bm25（SQL 原生）+ `ts_ms DESC, event_ref` tie-break**
- **不引入"覆盖度"重排**（review 修正的严重缺陷）：覆盖度只能在 Python 里算，而这会与 `OFFSET` 分页冲突——重排结果不是"已排序集合前缀的延伸"，候选集随 offset 变化时**跨页会重复/漏项**；若改为全量候选又在超大语料下不可控。故排序/分页/COUNT 全在 SQL 层完成（与现有路径同构），**无候选池、无上限参数**。
- 覆盖度的收益由 bm25 **天然近似**：多词命中的文档获得多份词分数贡献、IDF 高的词贡献更大 → 命中词更多/更关键的文档本就靠前。实测例：`A股行情` 的候选中全部是 1 词命中，两种排序无差异；**召回率不受排序影响**——§10 实测的 `OR≥1` 75%（旧）/79%（新）即本方案的预期可搜率。
- 保留证据（为何不做词数门槛）：`≥2 词覆盖` 硬过滤只剩 29%/38%，且会杀掉完全正确的 `A股行情`（1/2 词命中股票事件）——只放宽、不设阈值。

**④ snippet（review 修正）**：放宽命中时原文不含整串 topic，`_snippet` 需按 **`relaxed_terms(q)` 中首个在该文本出现的词** 定位（"整串优先 → bigram 词退化"）。**不得只用分隔符切词**——CJK 拼接词（`A股行情`）切不开，会退化成"取文首 120 字、看不到命中处"。客户端整串高亮在放宽命中时自然缺省（既有兜底 ✓）。

**⑤ 契约与影响**：`kind/ts_ms/snippet`、cursor 格式、`total` 语义（= 放宽查询 COUNT）全不变；**客户端零改动**。

**⑥ 适用范围（记录）**：回退对**全部 0 命中查询**生效（typed 与 topic 来源无区分手段）——typed 多词查询在 0 命中路径上语义由 AND 放宽为 OR，属可预期行为，一并记录于此。

**⑦ 改动清单（待实施）**

| # | 文件 | 改动 |
|---|---|---|
| R-B1 | `services/bff/search/fts.py` | 新增 `relaxed_terms(raw)`（bigram/整词 + 丢 len<2）与 `build_relaxed_query(terms)`（词一律加引号 OR；空 → `""`——加引号顺带免疫 `AND/OR/NOT` 大写关键字语法错误） |
| R-B2 | `services/bff/search/service.py` | ① `build_search`：精确 `total==0` 时走回退分支（`count_fts(放宽)` / `search_fts(放宽, limit, start)`，与现有路径同构、可正常分页）；② `_snippet` 增加"按 `relaxed_terms` 定位"的退化分支 |
| R-B3 | `services/tests/unit/bff/test_search_api.py` | 新增：拼接词命中（`A股行情` 型）、单字符 token 不噪声（含 `A` 的无关事件不被 1 字符 token 拉进来）、精确优先不触发回退、放宽分页跨页不重不漏（复用现有分页用例形态）、`total`=放宽计数、放宽仍 0 → 空结果、snippet 指向命中的 bigram 词 |

**⑧ 测试与验收**：单测覆盖上表；真实数据验收 = 用 §10 的度量脚本复测 7 天 topics（目标：`OR≥1` 命中率 新 ≥79% / 旧 ≥75%），且精确路径行为完全不变（现有 27 例全绿）。

**⑨ 定案（T1–T2，2026-09-21 按推荐）**：**T1 = 不标注"放宽命中"**（schema 零改动；高亮缺省即隐含；备选是响应加 `match_mode` 供客户端显"模糊结果"，需双侧改动）；**T2 = 不引入覆盖度**（纯 bm25 排序 → 分页一致、零 Python 重排、无候选上限；bm25 已近似覆盖度）。

**⑩ 实施记录（2026-09-21，已落地）**：`fts.py` 新增 `relaxed_terms`/`build_relaxed_query`；`service.py` 在精确 `total==0` 时切放宽 MATCH（排序/分页/COUNT 仍 SQL 层），`_snippet`/`_hit` 增 `terms` 参数（放宽命中按 bigram 词定位）；`test_search_api.py` +6 例（词集形状、拼接词命中、单字符不噪声、精确优先不回退、放宽分页、放宽仍空），**33/33 通过**；bff 全量 **124 passed**（2 条 `test_gzip.py` 为分支既有失败）。

**真实数据验收（用已实现的 `build_search` 对 `/opt/lvdata` 复测 7 天 topics）**：
| topics | 精确（实施前） | 放宽回退（实施后） |
|---|---|---|
| 旧式（重生成前） | 2/24（8%） | **23/24（96%）** |
| 新规则（重生成后） | 7/24（29%） | **21/24（88%）** |

- 注：验收口径为**全语料**命中（用户真实检索行为）；§10 离线表的 75%/79% 是"限当日事件"的更严口径——两者都成立，前者即产品实际可搜率。
- 命中质量抽样：`海马汽车→5 条`（首条即"海马汽车为什么今天大跌"✓）、`股市行情→9 条`（首条为大盘讨论 ✓）；**噪声如实记录**：抽象旧 topic 的泛化词会拉到无关内容（如「休闲活动」→ 手机参数截图）——由"仅 0 命中启用 + bm25 排序"收敛，仍属放宽路径的固有代价。

**⑪ 风险**：放宽的噪声由「精确优先 + 仅 0 命中启用 + bm25 排序」三重收敛；OR 查询的 COUNT 在超大语料下是唯一性能注意点（仅 0 命中时才走）。

---

*Created: 2026-09-21 | 状态: **已实施（2026-09-21，实施记录见 §9）** | 关联: docs/android_timeline_topics.md（§3.5 kind 集合与 Q3/Q4 缺口）、docs/mylife/timeline-android-v2.md（§5.3 搜索现状）、tools/lvdata-mcp/src/lvdata_mcp/index.py（search_docs 契约单点）、services/bff/search/service.py（被替换实现）*
