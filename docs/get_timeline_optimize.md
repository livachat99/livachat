# get_timeline / bff timeline 查询优化：ORDER BY 走 TEMP B-TREE

> 来源：2026-09-28 review（`docs/issue_list.md` I-9 的**诊断修正**——原条目把问题记成
> "COUNT 全表扫描"，实测证伪）。
> 状态：**已实施（2026-09-28，A 方案，MCP + BFF 双侧）**——实施记录见 §8.6；B/C/D/E 未采用。
> 范围：MCP `store.get_timeline`（`tools/lvdata-mcp/src/lvdata_mcp/store.py`）+
> BFF 的 timeline 查询副本（`services/bff/common/store.py`）。**只读查询优化，无 schema 变更。**

## 1. 问题（代码事实）

两处都是同一条 SQL 形状：

```sql
SELECT * FROM events [WHERE …] ORDER BY ts_ms, {PRIORITY_SQL}, event_id [LIMIT ?]
```

`PRIORITY_SQL` 是 **CASE 表达式**（`consts.py` 由 `EVENT_PRIORITY` 生成，"同 ms 文本类靠前"的
契约）。后果：**没有任何索引能覆盖完整排序键**；planner 又因子句里 `type IN (…)` / `rsession_id=?`
先选中该列索引，然后对命中行做 `TEMP B-TREE FOR ORDER BY`——`LIMIT` 因此**不能提前停**。

实测执行计划（200k 行合成库，本机 SSD）：

```
SELECT * FROM events WHERE type IN (7 类) ORDER BY ts_ms, CASE…, event_id LIMIT 201
  → SEARCH events USING INDEX idx_events_type (type=?)
    USE TEMP B-TREE FOR ORDER BY           ← 瓶颈：对全部命中行排序
```

受影响的调用点：

| # | 位置 | 说明 |
|---|------|------|
| 1 | `tools/lvdata-mcp/src/lvdata_mcp/store.py` `get_timeline` | MCP 时间线（含日期窗，**推荐用法也中招**） |
| 2 | `services/bff/common/store.py:217`（`_events_where` + 可选 LIMIT/OFFSET） | **App `/timeline` 主路径**（流量高于 MCP） |
| 3 | `services/bff/common/store.py:169`（`rsession_id = ?`，无 LIMIT） | 单会话事件查询——有 `idx_events_rsession_ts(rsession_id, ts_ms)`，排序范围仅该会话行，**不是问题**（见 §4 反作用） |

**COUNT 不是瓶颈**（推翻 I-9 原诊断）：`COUNT(*) FROM events WHERE type IN (…)` 走
`idx_events_type` 的 **covering index 扫描**，200k 行仅 **6.9 ms**——比翻页查询便宜 16 倍。

## 2. 实测数据（p50，本机 SSD）

### 2.1 规模对照

| 查询 | dev 库（1094 行） | 合成 200k 行 |
|------|------------------|-------------|
| `COUNT(*)`（type IN 7 类） | 0.05 ms | **6.9 ms** |
| 翻页查询（现状，无日期窗） | 3.68 ms | **113.0 ms** |
| 翻页查询（现状，带 30 天窗） | 3.68 ms | **88.2 ms** |

→ 当前 dev 规模不痛（服务端 `get_timeline` 整体 p50 5.3 ms 里它占大头），**痛点在万级行之后**。

### 2.2 变体对照（200k 行）

| 变体 | 耗时 | 计划 |
|------|------|------|
| 现状 · 无日期窗 | 113.0 ms | `idx_events_type` + `TEMP B-TREE FOR ORDER BY` |
| 现状 · 带 30 天窗 | 88.2 ms | 同上 |
| **`INDEXED BY idx_events_ts` · 无窗** | **0.6 ms** | ts 索引 + `TEMP B-TREE FOR **LAST 2 TERMS**`（只排同 ms 小段，LIMIT 可提前停） |
| **`INDEXED BY idx_events_ts` · 带窗** | **0.1 ms** | `SEARCH … USING INDEX idx_events_ts (ts_ms>?)` |
| COUNT（对照） | 6.9 ms | covering index |

### 2.3 反作用（决定"不能无脑加提示"）

强制 ts 索引在**稀有类型 + 无日期窗**时会退化成扫全索引：

| 场景 | 现状（planner 自选） | 强制 `idx_events_ts` |
|------|--------------------|---------------------|
| `type='agent_job'`（10 / 200k）无窗 | **0.08 ms** | 20.9 ms ✗ |
| `type='chat'`（199990 / 200k）无窗 | 34.7 ms | **0.62 ms** ✓ |

即：**收益取决于"该类型的行数占比 × 是否带日期窗"**；planner 在稀有类型时是对的选择。

## 3. 候选方案

| # | 方案 | 实测/评估 | 取向 |
|---|------|----------|------|
| **A** | **带日期窗时加 `INDEXED BY idx_events_ts`**；无窗保持 planner 自选 | 带窗 88.2 → **0.1 ms**；无窗保持现状（保护稀有类型） | **推荐**（风险可控：提示只作用于"窗口已收窄"的场景，收益单调） |
| B | 跑 `ANALYZE`（让 planner 有 `sqlite_stat1` 依据） | 合成库无统计信息、planner 判断可能失真；真实库效果需实测 | 建议**先试**（零代码风险），不行再上 A |
| C | 改 `ORDER BY`（去掉 `PRIORITY_SQL`） | 排序不再需要 b-tree，但**同 ms 优先级语义是契约**（`consts.py` 注释 + 分页游标依赖） | **否决**（语义变更） |
| D | 新增物化 `priority` 列 + 复合索引（如 `(ts_ms, priority, event_id)`） | 排序可由索引覆盖，收益与 A 同量级但**需 schema 变更 + 写侧/迁移/双端同步** | 搁置（A 无效时再议） |
| E | 无过滤时跳过 COUNT、`total` 置 null（I-9 原改法） | COUNT 仅 6.9 ms @200k | **降级为可选**（收益远小于 ORDER BY 那步） |

**注意**：`INDEXED BY` 是硬提示——若表上将来删了 `idx_events_ts` 会直接报错（fail loud，非静默退化），
可接受但需在代码注释里写明依赖。

## 4. 影响面（若实施 A/B）

| 类别 | 项 |
|------|-----|
| 代码 | `tools/lvdata-mcp/src/lvdata_mcp/store.py` `get_timeline`（按 §3 结论加提示/加 ANALYZE 后的行为）；`services/bff/common/store.py:217` 同款（**BFF 有独立副本，须同步改**——仓库既有约定：双副本改动需双侧注释互指）；`:169`（会话内查询）**不动** |
| 其它索引使用者 | `idx_events_ts` 现已服务 `idx_events_ts` 的其它查询（recent_events 的 BETWEEN、count）——加提示不改索引本身，无连带 |
| 迁移/统计 | 若选 B：`ANALYZE events`（SQLite 统计表；可在启动或 reindex 后跑一次；需评估写库时机——**查询平面只读**，应放在 watcher/reindex 侧） |
| 测试 | 现`test_get_timeline_same_ts_pagination`（同 ms 不重不漏）与 `test_get_timeline_filters_and_pagination` 必须全绿——**排序语义不变**是硬约束；新增：合成 20 万行的**性能护栏用例**（断言 EXPLAIN 计划里不出现整段 `TEMP B-TREE FOR ORDER BY`，只允许 `LAST N TERMS`）——比计时断言稳定 |
| 文档 | 本文档；`docs/issue_list.md` I-9 指向本文；`docs/bff_search_fts.md`（BFF 查询面）若同步改动需加注 |

### 不变量（实施时不可破坏）

1. **同 ms 排序**：`(ts_ms, EVENT_PRIORITY, event_id)` 三级全序——游标 keyset 依赖它；
2. **游标语义**：`next_cursor` 跨页不重不漏（现有用例守护）；
3. **窗口/类型/分页参数语义**不变（`INDEXED BY` 只影响执行计划，不影响结果集）。

## 5. 决策点（待拍板）

| # | 决策 | 备选 |
|---|------|------|
| D1 | 采用 A（带窗加提示）还是先 B（`ANALYZE`，零代码风险） | **倾向：先 B 实测 → 无效再 A**（B 若有效则连续性与最简） |
| D2 | 若选 A：提示的施加条件 | **倾向"仅当带日期窗"**（`start_ms`/`end_ms` 任一存在）；备选"总是加"（会牺牲稀有类型场景，见 §2.3） |
| D3 | BFF 侧是否同批（`:217`） | **倾向同批**（App 流量更大；但需双副本同步 + BFF 测试）；备选先 MCP、BFF 另立项 |
| D4 | 是否顺带落地"无过滤时跳过 COUNT"（E） | **倾向不做**（6.9 ms，收益小、`total: null` 还要改契约） |
| D5 | 性能护栏形式 | **倾向"EXPLAIN 计划断言"**（稳定）；备选"计时断言"（CI 机器性能波动会 flaky） |

## 6. 验收（实施时）

1. 200k 行合成库、带 30 天窗的翻页查询 p50 **< 5 ms**（现状 88 ms）；无窗查询不劣化（稀有类型
   场景保持 ≤ 现状）；
2. `EXPLAIN QUERY PLAN` 不再出现整段 `TEMP B-TREE FOR ORDER BY`（允许 `LAST N TERMS`）；
3. 同 ms 排序/游标语义不变：`test_get_timeline_same_ts_pagination` 等既有用例全绿；
4. 真实库（1094 行）`get_timeline` 行为与响应逐字不变；
5. `tools/lvdata-mcp/tests` 全量 pytest 全绿（BFF 侧若同批，`services` 侧相关用例同步）。

## 7. 复现口径（本次实测脚本）

- **真实库**：`/opt/lvdata/lvdata_inventory.db` 只读副本（`sqlite3.Connection.backup`），
  `EXPLAIN QUERY PLAN` + 5 次取 p50；
- **合成库**：临时目录按 `ddl.py` 的 `EVENTS_SQL` 建表，插 200k 行（type 按 7 类均分；
  反作用实验用 `agent_job` 10 行 + `chat` 其余），3 次取 p50；
- 关键 SQL：`SELECT * FROM events [INDEXED BY idx_events_ts] WHERE type IN (…) [AND ts_ms >= ?]
  ORDER BY ts_ms, {PRIORITY_SQL}, event_id LIMIT 201`。

---

## 8. 修改计划：A 方案（带日期窗时强制 `idx_events_ts`）——已实施（MCP + BFF）

**范围**：`tools/lvdata-mcp/src/lvdata_mcp/store.py` 的 `get_timeline` **翻页查询**一处；
**COUNT 不动**（covering index 已最优，加提示反而更差）。

### 8.1 具体改动（一处 SQL + 一个可测 helper）

现在（`store.py:157-160`）：

```python
            rows = conn.execute(
                f"SELECT * FROM events WHERE {' AND '.join(page_where)} "
                f"ORDER BY ts_ms, {PRIORITY_SQL}, event_id LIMIT ?",
                page_params + [limit + 1]).fetchall()
```

改为（抽一个**模块级** helper，便于测试直接对同一条 SQL 跑 `EXPLAIN`——避免测试里复制 SQL 造成漂移）：

```python
def _timeline_page_sql(where_sql: str, windowed: bool) -> str:
    """get_timeline 的翻页 SQL（§A 优化：**带日期窗时**强制 idx_events_ts）。

    原因：ORDER BY 第二键是 PRIORITY_SQL（CASE 表达式），任何索引都无法覆盖完整排序键；
    planner 默认选 `idx_events_type` 后会整段 `TEMP B-TREE FOR ORDER BY`（LIMIT 失效）——
    带窗时强制 ts 索引只剩"同 ms 小段"排序（`LAST 2 TERMS`），LIMIT 可提前停。
    无窗**不加**提示：类型占比低时（如 agent_job）类型索引更优（见 §2.3 反作用表）。
    ⚠️ 硬提示依赖 `idx_events_ts` 存在（`ddl.py` 建表保证）；索引被删会直接报错（fail loud）。
    """
    hint = "INDEXED BY idx_events_ts " if windowed else ""
    return (f"SELECT * FROM events {hint}WHERE {where_sql} "
            f"ORDER BY ts_ms, {PRIORITY_SQL}, event_id LIMIT ?")
```

调用点：

```python
        windowed = start_ms is not None or end_ms is not None
        rows = conn.execute(_timeline_page_sql(" AND ".join(page_where), windowed),
                            page_params + [limit + 1]).fetchall()
```

**不改动**：`where`/游标谓词/参数顺序、`total` COUNT、分组与轻量投影、返回结构。

### 8.2 实测验证（本次已跑，200k 合成库）

| 页面 | 现状 | 加提示后 | 计划（加提示） |
|------|------|---------|---------------|
| 首页（带 30 天窗） | 89.30 ms | **0.06 ms** | `SEARCH … USING INDEX idx_events_ts (ts_ms>? AND ts_ms<?)` + `LAST 2 TERMS` |
| **游标页**（三元组谓词，带窗） | 89.28 ms | **0.07 ms** | 同上（游标谓词未挡掉索引区间扫描） |
| 无窗 | 113 ms（不动） | — | 保持 planner 自选（保护稀有类型，§2.3） |

即：**收益不只在首页**——游标页同样从 ~89 ms 降到 ~0.07 ms，不存在"把问题挪到第二页"。

### 8.3 测试

| 用例 | 断言 |
|------|------|
| 新增 `test_timeline_page_sql_uses_ts_index_when_windowed` | `_timeline_page_sql(…, windowed=True)` 产出含 `INDEXED BY idx_events_ts`；`windowed=False` 不含 |
| 新增 `test_timeline_page_plan_has_no_full_sort`（对 temp 库跑 `EXPLAIN QUERY PLAN`） | 带窗 SQL 的计划含 `idx_events_ts` 且**只允许** `TEMP B-TREE FOR LAST N TERMS`（禁止整段 `TEMP B-TREE FOR ORDER BY`）；游标谓词形状同样断言 |
| 既有 | `test_get_timeline` / `test_get_timeline_filters_and_pagination` / `test_get_timeline_same_ts_pagination`（同 ms 不重不漏）/ `test_get_timeline_timezone` 必须全绿（语义不变量） |
| 真机 | 重启后 `get_timeline`（带窗/无窗各一次）响应与之前**逐字一致**（只有计划变了） |

**不做计时断言**（CI 机器性能波动会 flaky，见 §5 D5）。

### 8.4 验收

1. `EXPLAIN QUERY PLAN`：带窗（含游标页）不再出现整段 `TEMP B-TREE FOR ORDER BY`；
2. 既有 timeline 用例全绿 + 新增 2 例计划/构造断言；
3. 真机响应逐字不变；合成 200k 行带窗查询 p50 < 5 ms（本次实测 0.06–0.07 ms）；
4. 全量 `tools/lvdata-mcp/tests` pytest 全绿。

### 8.5 范围（已定：两处同批，2026-09-28 用户拍板）

BFF 侧同批修改（`services/bff/common/store.py` 的 `events_in_range`）。


### 8.6 实施记录（2026-09-28）

| 侧 | 改动 |
|----|------|
| MCP | `tools/lvdata-mcp/src/lvdata_mcp/store.py`：新增模块级 `_timeline_page_sql(where_sql, windowed)`（带窗插 `INDEXED BY idx_events_ts`），`get_timeline` 调用点改用它；注释含"为什么只在带窗时加 / ORDER BY 为何用不上索引 / 硬提示 fail loud / 🔁 BFF 双副本同步" |
| BFF | `services/bff/common/store.py`：新增同形 `_page_sql(where_clause, windowed)`，`events_in_range` 调用点改用它（`count_events`/会话内查询 `:169` **不动**）；注释含 🔁 指回 MCP 副本 |
| 测试（MCP） | 新增 `test_timeline_page_sql_uses_ts_index_when_windowed`（构造断言：带窗含提示、无窗不含、`ORDER BY … LIMIT` 形状不变）与 `test_timeline_page_plan_has_no_full_sort`（`EXPLAIN QUERY PLAN`：首页与**游标页**形状均须含 `idx_events_ts` 且**禁止**整段 `TEMP B-TREE FOR ORDER BY`） |
| 测试（BFF 夹具） | `services/tests/unit/bff/conftest.py` 的 events 建表补三条索引（`idx_events_ts`/`idx_events_type`/`idx_events_rsession_ts`）——**夹具原本漏了索引，与生产 schema 不一致**；硬提示因此在测试里 fail loud 暴露（这正是"硬提示"的预期代价：不一致即报错，而非静默退化）。新增索引对齐生产（`ddl.py`） |
| 验证 | MCP 全量 **283 passed / 0 failed**（281 → +2）；`services/tests/unit` 全量 **1115 passed / 0 failed**；真机重启后 `get_timeline`（带窗/无窗）响应与改动前逐字一致 |

**注**：两个测试树存在同名文件（`test_tools.py`/`test_vec.py`/…），**同一 pytest 进程内会模块名冲突**
（仓库既有布局）——须分开跑：`pytest tools/lvdata-mcp/tests/` 与 `pytest services/tests/unit/`。
