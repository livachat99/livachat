# qwen-audio-3.1-asr-flash-streaming 切换 —— 调研、实测与变更记录

> 状态：**已实施**（2026-10-10。功能性变更仅 1 处：`configs/dev/server.yaml` model 串
> 3.0→3.1；全量单测 1532 passed 绿；运行中 server 日志确证模型穿透，真机会话在跑 3.1）。
> 范围：`configs/dev/server.yaml` + 两个探针脚本 `DEFAULT_MODEL` + 新增对比探针
> `services/scripts/probe_asr_31_vs_30_real.py` + 本档。**零代码改动**（provider /
> orchestrator / 协议层 / 客户端均未动——协议实测与 3.0 零差异）。
> 结论先行：**3.1 与 3.0 同一套 DashScope duplex 协议，可配置直切**；计费由"音频时长"
> 改为"token"（整句实测省 ~34%，碎句反超）；保守档（仅换模型串，far+mss600）行为与
> 现网等价（同场 1351→1389ms）；`vad_model=near_meeting_16k` 可再提速 ~514ms，但 700ms
> 自然停顿必切句，另立 P2-1 验证。

## 1. 背景与目标

- 2026-09-23 云栖大会发布 Qwen-Audio-3.1 系列（ASR/TTS/Realtime），官网实时场景选型页
  已改推 `qwen-audio-3.1-asr-flash-streaming`；ASR 全线降价（媒体口径"最高 95%"，见 §3.4
  实测订正）。
- 现网自 2026-09-13 起用 `qwen-audio-3.0-asr-flash-streaming`（迁移背景与调参见
  `docs/asr_bailian_improvements.md`）。
- 目标：核对 3.1 的功能/性能/协议/价格差异，给切换决策提供实测依据。

## 2. 调研（官网文档，2026-10-10）

### 2.1 功能差异

| 维度 | 3.0 | 3.1 |
|---|---|---|
| 语种 | ~28 语种；普通话口音覆盖广（中原/西南/东北/粤语/吴语/闽南/客家等 20+ 地区） | ~30 语种；方言为指定方言点：上海/南昌/宁波/客家/杭州/温州/湖南/福建/粤语/苏州 |
| VAD 配置 | `max_sentence_silence` | **新增 `vad_model`**：`near_meeting_16k`（近场）/ `far_field_meeting_16k`（远场，默认）；`max_sentence_silence` 仍生效（§3 实测） |
| 方言输出 | 无 | **新增 `keep_dialect`**：false（默认）=方言转写为普通话；true=保留方言表达 |
| 共有 | 热词（vocabulary/vocabulary_id）、Prompt 上下文（input.context ≤5 轮/400 字）、句级+字级时间戳、标点预测、文本规范化、中英自由切换、音频时长不限 | 同左 |
| 上下文限制 | 未标注 | 8192 in / 1024 out token |

### 2.2 通信协议

- 与 3.0 **完全同一套 duplex WS 协议**：`run-task → task-started → result-generated`
  （`payload.output.sentence`：begin/end_time、text、sentence_begin/end、words[]）→
  `task-finished`；同一 endpoint（workspace 专属域名）+ `X-DashScope-WorkSpace` 头。
- 同一 run-task 报文；`heartbeat` / `language_hints` 3.1 照常接受。
- **usage 字段差异**：3.0 返回 `{duration}`；3.1 返回
  `{duration, input_tokens, output_tokens, total_tokens}`（计费口径=token）。

### 2.3 价格与限流（北京地域）

| | 3.0 | 3.1 |
|---|---|---|
| 计费 | 音频时长 ¥0.00033/秒 | 输入 ¥6/百万 token + 输出 ¥4.5/百万 token |
| RPM | 1200 | 600 |

RPM 减半：单副本部署（`infra/kubernetes/deployment.yaml` replicas=1）× 单会话 1 task
的产品形态下裕量仍极大，无影响。新加坡价格：3.1 为 ¥6.781/¥5.104 每百万 token。

## 3. 真机实测（2026-10-10，workspace 域名直连）

### 3.1 方法

- 新增探针 `services/scripts/probe_asr_31_vs_30_real.py`：裸 WebSocket（绕过 provider
  以捕获全部服务端事件含 usage 计费字段），实时节奏（20ms/50Hz）喂帧，测
  「最后一帧语音发出 → final 到达」；每配置 2–4 次重复；产物 `/tmp/asr_probe/`
  （`raw_*.jsonl` 全事件流可审计）。
- 素材：macOS TTS 合成 3 条——普通话 4.66s（"帮我查一下明天北京的天气，然后提醒我下午
  三点开会。"）、同句+700ms 句中停顿（与 3.0 mss 调参同口径）、粤语 6.5s（Sinji）。
- 基线注意：绝对时延跨天漂移（3.0+mss600 今日直连 1351ms，9 月文档为 1085ms），
  故一律**同场相对比较**；合成音为受控样本，真人语音需 App smoke 兜底（§5）。

### 3.2 收尾时延矩阵（普通话样本，单位 ms）

| 配置 | 收尾 mean（min–max） | finals/次 | 输入/输出 tokens |
|---|---|---|---|
| 3.0 far+mss600（现网兼容档） | 1351（1348–1355） | 1（完整句） | —（duration=5） |
| 3.0 far+默认 | 1546（1541–1554） | 1 | — |
| 3.1 far+mss600（本次切换档） | 1389（1347–1424） | 1 | 170/15 |
| 3.1 far+默认 | 1645（1588–1751） | 1 | 170/15 |
| 3.1 near+默认 | 1044（1004–1076） | 1 | 164/15 |
| 3.1 near+mss600 | **837（779–917）** | 1 | 164/15 |
| 3.1 near+mss800 | 1021（1004–1054） | 1 | 164/15 |
| 3.1 near+mss400 | 621（577–672） | **2（逗号处切句）** | 317/15 |

核心结论：

1. **`max_sentence_silence` 在 3.1 上线性生效**：near 下 400/600/800 → 621/837/1021，
   拟合约 `收尾 ≈ mss + 220ms`（far 档同场 ≈ mss + 790ms）——无需为 3.1 放弃现网 mss=600 调参。
2. `vad_model=near` 比 far 快 ~600ms；near+mss600 同场比现网 3.0 快 **~514ms（-38%）**，
   是 P2-1 的主要候选。
3. 碎句（near+mss400 在逗号处切句）：`_start_thinking` 抢答风险与现网 mss 调参时期的
   既有分析一致（见 asr_bailian_improvements.md §6.4），本次未下探。

### 3.3 切句行为（700ms 句中停顿样本）

| 配置 | 收尾 mean | finals |
|---|---|---|
| 3.0 far+mss600 | 1435 | 1（不切） |
| 3.1 far+mss600 | 1417 | 1（不切） |
| 3.1 near+默认 | 1013 | 2（切句） |
| 3.1 near+mss600 | 818 | 2（切句） |
| 3.1 near+mss800 | 1015 | 2（切句） |

**切句边界由 VAD 模型决定（near 明显更激进），mss 只调节 final 到达时延**——near 档
在 700ms 停顿上必切（far+mss600 与 3.0 同样不切），这正是 P2-1 需真人验证的取舍点。

### 3.4 计费实测

- 3.0：按 billed `duration`（本样本=5s）→ ¥0.00165/轮。
- 3.1：整句 ≈170 in + 15 out → **¥0.00109/轮，省 ~34%**（约 25–34 输入 token/音频秒）；
  粤语样本省 ~46%。
- **token 计费对切句敏感**：碎成两句后输入 token ≈ 翻倍（317）→ ¥0.00197/轮，**反超 3.0 约 19%**。
- 媒体口径"ASR 降价 95%"与 3.0→3.1 直接对比不符（应对照更早一代定价）；以 3.0 现价
  为基线，实测整句省 ~34%、不碎句是收益前提。

### 3.5 粤语与 keep_dialect

3.0 与 3.1 均完整转写粤语原字（输出基本一致，仅标点差异）；**`keep_dialect` true/false
在合成粤语样本上未观察到差异**（均为粤语原字、无转普通话）——真人方言样本验证列入
P2-4。keep_dialect 开启时 tokens 略增（174 vs 170），无行为差异故未启用。

### 3.6 协议事件核对

同一段音频下 3.0/3.1 事件序列逐条对照一致（task-started / result-generated×12 /
task-finished；interim 节奏相同、词级时间戳两边都有、无独立 heartbeat 包出现）；
usage 出现位置与语义同 §2.2。

## 4. 切换内容（已实施）

### 4.1 功能配置 `configs/dev/server.yaml`

```yaml
    alibaba_bailian:
      model: qwen-audio-3.1-asr-flash-streaming   # 3.0 → 3.1
      params:                                     # 保持不变
        heartbeat: true
        language_hints: ["zh", "en"]
        max_sentence_silence: 600
```

- **决策：保守档**（仅换模型串）。不显式配 `vad_model`（默认 far_field_meeting_16k 即
  保守档）、不启用 `keep_dialect`——二者默认值即现行为，避免引入无实测背书的参数。
- params 注释更新为 3.1 实测口径（mss 线性、far≈mss+790ms、near≈mss+220ms）。

### 4.2 探针脚本默认值

`services/scripts/measure_asr_final_latency.py` 与 `test_bailian_asr_real.py` 的
`DEFAULT_MODEL` 3.0→3.1（`--matrix` 内 3.0 对照项保留）。

### 4.3 明确不做

- 不动 provider / orchestrator / 协议层 / 客户端（零代码）
- 不启用 near VAD / keep_dialect（P2-1 / P2-4）
- 不同步 `infra/kubernetes/configmap.yaml`（其 asr 段仍为更旧的 deepgram/paraformer 配置
  + 死键 `asr.language`，系 3.0 迁移遗留待办，独立处理见 P2-2）
- 不加 usage token 日志（P2-3）

## 5. 验证

- **单测**：`cd services && pytest -q`（venv PATH 前置）→ **1532 passed, 1 skipped,
  97 deselected**（119s，2026-10-10）。测试无模型串断言，零改动。
- **配置穿透**：运行中 server（uvicorn --reload）日志两次确证
  `bailian asr open model=qwen-audio-3.1-asr-flash-streaming workspace=llm-1tmsqifo51cwfadr`
  （10:21:19 / 10:27:53）；reload 后真机设备自动重连、会话恢复正常。
- **未跑模拟设备 e2e**（避免向真实 chat store 写入假设备会话）；穿透证据以运行日志为准。
- **待办**：App 真机 smoke（一轮完整对话：final 上屏/无重复/无残影/收尾体感/长静默保活）。

## 6. 风险与回滚

| 风险 | 评估 |
|---|---|
| 3.1 真人语音上标点/切句微差 | 保守档同场与 3.0 行为一致（§3.2/3.3）；模型本体换代，靠 §5 真机 smoke 兜底 |
| token 计费对碎句敏感 | 保守档切句行为同现网档；若真机碎句率异常上升，先回滚再启用 P2-3 观察 |
| RPM 600 | 单副本、单会话 1 task 形态下无影响（§2.3） |

**回滚**：`model` 串改回 3.0，触发 reload/重启即可（无状态迁移、无客户端配套）。

## 7. 后续独立项

- **P2-1 收尾提速 -514ms**：`3.1+near+mss600`（837ms）。前置：真人语音验证 700ms 级停顿
  切句率/抢答率；可结合 asr_bailian_improvements.md §6.4 残留的"轮次驱动 finish-task"方案评估。
- **P2-2 部署侧配置同步**：k8s configmap asr 段（含死键 `asr.language` 清理）。
- **P2-3 usage token 日志**：provider 目前丢弃 usage 字段，加日志使计费可观测。
- **P2-4 keep_dialect 真人方言验证**：合成样本无差异，真人粤语/吴语等再评。

## 8. 探针与产物（复现）

```bash
cd services
# 对比矩阵（两模型 × 参数档 × 3 次）
.venv/bin/python scripts/probe_asr_31_vs_30_real.py /tmp/asr_probe/speech_16k.wav --repeats 3
# 单档位
.venv/bin/python scripts/probe_asr_31_vs_30_real.py /tmp/asr_probe/speech_16k.wav \
  --only 3.1-near-mss600 --repeats 3
```

素材生成（macOS）：`say -v "Eddy (Chinese (China mainland))" -o x.aiff "..." &&
afconvert -f WAVE -d LEI16@16000 -c 1 x.aiff x.wav`；700ms 停顿样本= 两段 say 输出 +
数字静音拼接。产物：`/tmp/asr_probe/`（`raw_<case>_N.jsonl` 全服务端事件 + summary）。
