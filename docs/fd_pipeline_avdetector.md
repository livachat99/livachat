# Session AVDetector（音视频主动监测）— 分析与设计

> **状态**：**已实施（2026-10-03）**——C1–C12 全部落地（实施记录见 §10）；服务端 `tests/unit`
> **1304 passed / 0 failed / 1 skipped**（avdetector 相关 132 例：unit 130 + 装配 2；已实施含
> **十轮 review 修复（第十轮 [P6.6]–[P6.12] 见 §10）**）；[R1]–[R22] 拍板记录见 §9；
> 交付默认 `avdetector.enabled: false`（真机验收期置 true——2026-10-03 晚已在跑）。
> **修订（2026-10-03 review 后）**：修复三处严重问题（§4 D2/D5 标 ★）——①检测流 feed 改**无阻塞入队**，
> 不得在 dispatch 热路径 await 检测流 IO（背压会阻塞主循环）；②新增**窗口纪律**：周期静默 commit 给未提交
> 音频划界，修复长静默下窗口无界 + 说话后否决误伤（静默 commit 已探针验证，变体 H，§3.2）；
> ③视频改为**仅检查时上送当帧**，弃用 1fps 连续上行（省 video token 成本）。
> **增补（2026-10-03，已拍板）**：④检测能力电池（B/F 变体）结果并入 §3.5——真实感样本分类可用性结论修订；
> ⑤**通道优先级**：有图像 → 图像为主、声音补充；无图像 → 仅检测声音；⑥带 `payload.text` 的帧**不做检测**、
> 交给既有 v3→LLM 链路（处理方式同现状）——⑤⑥ 落 §4 D2（[R13]/[R14]）；⑦**D6 图像判定三条标准**：
> 清晰非过渡 / 与之前画面较大区别 / 危险行为（立即报告，event 固定"危险行为"）——[R15] **已拍板：
> 危险事件突破主动冷却**；视频检查节流 30s→**10s**（[R17]）；
> ⑧**帧缓存**：avdetector 本地缓存最近 10 帧非 text 视频帧（text 帧不入缓存）；检查上行默认单帧、
> 多帧同窗语义不稳暂不用（§3.5 U 定向）——[R16]。
> **修订（2026-10-06，已实施）**：[P6.13] 转写否决/竞态复检阈值 **≥2 → ≥5 种字符**——方案 A
> （门②/门③共享单点 `is_speech_like_text` 同改）；2–4 种字符的短转写放行事件/不再拦竞态——
> 边界矩阵、退化风险与实施清单见 §10。
> **修订（2026-10-06，已实施）**：[P6.14] 拒图停图改**限时**——服务端拒图后不再永久停图，
> 停 `image_disable_cooldown_s`（默认 **300s**）到期自动恢复附帧重试；计时为会话内状态
> （新建会话/重建 monitor 清零）；详见 §10 条目 26。
> **修订（2026-10-09，已实施）**：[R16] 帧缓存 **10 帧环形缓冲 → K=1 最新帧槽**——
> avdetector 本地只保留最新一帧非 text 视频帧（历史多帧从未被读，删除；多帧回溯一并放弃）；
> `frame_cache_size` 配置删除；检查仍取"处理时刻最新帧"（异步语义不变）。详见 §10 条目 27。
> **修订（2026-10-09，已实施）**：附帧超限防护——provider 预检 + `image_resize()`
> 上行副本重压（≤2 档：max_edge 960 → 640），仍超则跳过附帧（纯音频检查）；修复 DashScope
> 网关 262144B 帧限导致的 1009 崩流（真机实测 + 探针复现）。详见 §10 条目 28。
> **增补（2026-10-22，已实施）**：新增 **视觉回合抑制窗**——cam 首帧直起轮正常结束
> （回到 LISTENING）后 5s 内，AVDetector 的**普通画面事件**只在投递侧丢弃（危险事件照投、声音
> 事件与用户回合不受影响）。开窗时**强制补一次带图检查**建立视觉基线（否则 5s 窗短于 10s
> 视频检测节流，窗口内可能从未有检查，重复描述只会被推迟到窗口后）。**"cam 首帧"= `turn_id`
> 非 0 且与上次不同**：同 `turn_id` 的后续帧不算首帧；首次开相机、切换摄像头、重新打开摄像头等
> 任何 turn_id 变化的采集轮首帧都算首帧，其直起轮结束后各自开窗。设计与验证口径见 §4 D8。
> **Date**: 2026-10-03
> **需求要点**（用户原文归纳）：
> 1. **主动互动**：用户咳嗽/喷嚏等非语音声音 → AI 主动关心；摄像头画面清晰/有重大变化 → AI 主动描述；
>    不等用户提问。
> 2. **架构**：新增 **avdetector service**（使用 aliyun dashscope `qwen3.8-omni-flash-realtime` 做音视频监测）；
>    session 收到的 input audio **同时**喂 avdetector 与 ASR service——ASR 识别 → 走现有标准流程；
>    **ASR 未识别而 avdetector 识别**（audio: 描述 / video image: 描述）→ orchestrator 直接调用 LLM/TTS 主动说话。
> 3. **可关闭**：关闭时行为与现状**完全一致**；目前**仅 chat 模式**支持 avdetector。
> **Scope**：新增 `services/avdetector/**`（服务/provider）· `services/session/`（monitor 组件 + orchestrator 接线）·
> `services/app/application.py`、`services/gateway/endpoint.py`（装配）· `services/shared/config/models.py`、`configs/dev/server.yaml`
> （配置段）· `configs/dev/prompts/avdetector.txt`（检测器提示词）· `services/tests/**`
> **非目标**：客户端与 WS 协议**零改动**（§4 D7）；omni/lecture/translate/transcribe 模式不接入；
> iOS/Web；`fd_pipeline_omni_mode.md` §11-L2（omni 转 manual）不在此文解决（关系见 §9.1）。
> **上游文档**：`docs/fd_pipeline_omni_mode.md` §11（2026-10-02 主动互动探针与两期设计，机制先例）·
> `docs/qwen_omni_realtime.md`（qwen3.8-omni-flash-realtime 全部 provider 事实与价格）·
> `docs/server_design.md` §4.3（三段式编排基线）· `docs/asr_bailian_improvements.md`（ASR final 时延口径）

---

**结论先行**：

1. **需求可实现，且机制已被真机探针打通**（2026-10-02/10-03 两轮，§3）：manual（`turn_detection: null`）+
   单条 `response.create` 即一次"检测查询"；`text-only` modalities 被接受；安静→`NONE`、咳嗽→事件 JSON、
   灰图→`NONE`、机器人图→JSON，单次查询 0.27–0.55s；同会话多轮可重复；**lv-audiotool 真实感样本
   8 类 ×4 全部检出且分类正确**（§3.5）。
2. **关键风险已被探测并给出程序性对策**：真人说话会被模型**误报**为非语音事件（2026-10-02 探针 3/3；
   2026-10-03 电池复测 1/4——**随机波动、不能只靠提示词**）。但同一检测流的**内建转写**是可靠的否决信号：
   说话窗口转写= `'你好，这是一个ASR的测试识别测试…'`（完整文本，4/4），咳嗽窗口转写=**空串**。设计以
   「ASR 无识别」+「检测流转写为空」**双闸**约束主动触发（§4 D2）。
3. **orchestrator 接入 = 一个旁路 monitor + 一处音频 feed + 两处触发（音频 turn-end / 视频帧）**，全部在 chat 模式门控内；
   `avdetector.enabled=false`（或建流失败）时 `avdetector_service is None` → 代码路径全不激活，
   **与现状字节级一致**（§4 D4）。
4. **协议零变更**：主动发言复用 `ai_text/ai_audio/status` 既有下行；`status.reason` 复用 `asr_final`
   （Android SDK 的 `handleStatus` 只消费 `session_status`、不消费 reason，`SessionManager.kt:289-301`），
   无 schema 重生成（§4 D7）。
5. 成本可控（实测 token 计，§3.4）：一次"安静"查询 ≈144 tokens、一次"咳嗽"查询 ≈188 tokens、
   一次"图片"查询 ≈610 tokens（其中 640×640 图 400 video tokens）。按 §2.2 价目（音频入 ¥6/M、
   文/图/视频入 ¥1.5/M、文本出 ¥4.5/M）单次查询 ≈ **¥0.0005–0.001**。
6. **通道优先级（2026-10-03 拍板）**：有图像 → **图像为主、声音补充**（统一检测查询契约 + 提示词优先级
   条款，[R13]）；无图像 → **仅检测声音**；带 `payload.text` 的帧**不做检测**、交给既有 v3→LLM 链路
   （处理方式与现状完全一致，[R14]）——均落 §4 D2。

---

## 1. 背景与目标

现状（chat 模式）是"用户开口 → ASR → LLM → TTS"的被动三段式（`docs/server_design.md` §4.3）。
用户不说话时，服务端对麦克风/摄像头的内容没有任何"反应通道"：非语音声音（咳嗽/喷嚏）与画面内容
完全被忽略（ASR 只输出文字，video 帧只是下一轮 LLM 的多模态上下文）。

目标：为 **chat 模式**增加一条**旁路主动通道**——

- 输入的每一帧音频在喂 ASR 的同时喂给 avdetector（独立 WS 流，qwen3.8-omni-flash-realtime，
  **manual 模式、text-only 输出**）；
- 回合结束时若 ASR 没有产生有意义文本（"没识别"），且该窗口确有"声音活动"，向 avdetector 发一次
  **检测查询**（`response.create`）；
- avdetector 返回事件描述（咳嗽/喷嚏/异响）时，orchestrator 以该描述为**事件注记**走既有
  `_start_thinking`（LLM 流式 + TTS）主动说一句话；
- 视频帧在节流下做同款检测查询，画面有值得描述的内容（清晰/变化）时，AI 主动描述；
- 开关（配置级）关闭时，全部行为与现状一致。

### 1.1 目标

- [ ] 新 service：`services/avdetector/`（base/service/factory/providers），provider 走 DashScope realtime
  WS（与 omni 同族协议），manual 模式 + text-only。
- [ ] orchestrator：音频 feed（视频不做连续上行）、monitor 帧槽（**K=1** 最新帧、非 text，[R16]〔2026-10-09 修订〕）、
  音/视频触发门控（**通道优先级**：有图图像为主、无图仅声音；带 `payload.text` 帧不检测）、
  主动回合执行、生命周期（session 边界、模式切换）。
- [ ] 配置与装配：`avdetector` 配置段（enabled/providers/prompt/节流参数），endpoint/application 接线。
- [ ] 测试：单测（monitor 门控/否决/冷却、provider 假 WS）+ 集成（主动回合 s2c 序列、关闭零回归）。

### 1.2 非目标（Out of Scope）

- **不改协议与客户端**：不新增 C2S/S2C 消息与字段；不做客户端开关 UI（"可关闭"= 服务端配置级，
  §4 D4；后续如需用户级开关另立需求）。
- **不改 omni/lecture/translate/transcribe 路径**；avdetector 仅 chat 模式生效。
- 不做唤醒词/命令词；不做声纹/身份识别（隐私面见 §7）。
- 不解决 `fd_pipeline_omni_mode.md` §10 TODO-1 / §11-L2（omni 模式自身的主动化）——本设计是
  **chat 模式的平行方案**，与 L2 的关系见 §9.1。

## 2. 现状关键代码锚点（逐条核实，2026-10-03）

### 2.1 服务端（接入面）

| 位置 | 内容 |
|---|---|
| `services/session/orchestrator.py:397-411` | `_dispatch`：`session.start/end/change/audio/interrupt/video` 六类 |
| `orchestrator.py:965-1180` | `_handle_audio`（chat 路径）：omni/lecture 分流（`:971-998`）→ 状态门控（`:1000-1019`，仅 LISTENING/SPEAKING 处理帧）→ opus 解码（`:1021`）→ `TurnDetector.on_frame`（`:1028`）→ **喂 ASR `_feed_asr_with_recovery(pcm)`（`:1037-1038`）** ← avdetector 并列喂入点 |
| `orchestrator.py:1040-1179` | turn-end 块：`final_text = asr_stream.final_text`（`:1049`）→ `meaningful = ...>=2`（`:1059`）→ 落库/快照（`:1063-1117`）→ `_start_thinking` 起后台任务，条件 `meaningful or had_asr_final`（`:1157-1165`）**← "ASR 没识别"的判定点：两个皆假时当前什么都不做（avdetector 触发钩子）** |
| `orchestrator.py:1186-1558` | `_start_thinking`：THINKING 状态 + `status(thinking,"asr_final")`（`:1194-1195`）→ **chat 分支 messages 仅由 system_prompt + history 组成，`user_text` 不参与**（`:1224-1239`）→ LLM 流式（`:1297-1318`）→ final ai_text（可带图，`:1430-1431`）→ `will_speak` 提前迁 SPEAKING（`:1440-1445`）→ TTS feed/flush/drain（`:1465-1533`）→ LISTENING + `status(listening,"tts_finish")`（`:1535-1539`） |
| `orchestrator.py:1595-1622` / `:1624-1682` | `_handle_interrupt` / `_do_interrupt`：epoch 递增、取消 pipeline/ASR poll、清 per-turn 态、`status(listening,"interrupted")`——主动回合天然可被同一机制打断 |
| `orchestrator.py:1688-1842` | `_handle_video`：帧入 buffer（`:1711-1728`）→ v3 文本回合门控（`:1746-1748`，排除 `translate/transcribe/asr_only/omni`）→ omni feed_image 先例（`:1817-1829`）→ `ai_video_ack`（`:1831-1842`）← avdetector 视频喂入/触发点 |
| `orchestrator.py:1998-2167` | `_poll_asr_results`：interim 上屏；**有文本 final** 才 `force_turn_end + _asr_final_sent`（`:2053-2063`）——ASR "没识别"= 此路径从未置位 |
| `orchestrator.py:416-562` | `_handle_session_start`：复位 `_pipeline_mode="full"`（`:418`）、关旧 lecture/omni 引擎（`:437-452`）、**ASR/TTS eager open 先例**（`:521-562`）← monitor 生命周期挂点 |
| `orchestrator.py:564-586` / `:381-391` | `_handle_session_end` / `run()` 收尾：并发关闭 asr/tts/lecture/omni ← avdetector 加入关闭清单 |
| `orchestrator.py:626-750` | `_handle_session_change`：白名单 `chat/translate/transcribe/omni`；chat → `_pipeline_mode="full"`（`:681-686`）；omni/lecture 进出引擎互斥 ← avdetector 的"仅 chat"门控挂点 |
| `services/session/turn_detector.py:9-30` | `TurnDetector`：连续静音 low/medium/high=45/30/20 帧（900/600/400ms）；**静音回合在每个"语音→静音"周期只触发一次**（`_turn_ended` 置位），会话开始后的纯静音也会触发一次 |
| `services/session/omni_mode.py:88-199` | 引擎形态基线：`open()`/懒重开（`_ensure_stream`，5s 冷却，`:147-177`）/feed 失败降级 |
| `services/omni/base.py:9-53` | `OmniEventType` / `OmniStream` 协议（**无文本输入、无 response.create**——avdetector provider 不能直接复用） |
| `services/omni/providers/alibaba_omni.py:468-488` | `session.update` 构造：**semantic_vad 硬编码**（`:484`）、nested audio 16k、modalities 参数化（`:469`）← avdetector 需要 manual + text-only 的**新 provider** |
| `alibaba_omni.py:339-351` | 图片上行 `input_image_buffer.append`（base64 JPEG、非 data URL） |
| `alibaba_omni.py:268-270` | `Invalid image/video` → `_image_disabled` 会话级熔断（avdetector 可复用同款纪律） |
| `services/app/application.py:44-98` | 服务创建基线：三段式无条件 + omni 额外创建（`cfg.omni.enabled`）；`app.state.*` 暴露（`:255-269`） |
| `services/gateway/endpoint.py:106-131` | `DialogOrchestrator(...)` 全部 kwarg（omni 先例：`omni_service/omni_system_prompt/omni_generation_opts/pipeline_sample_rate`）← avdetector 参数同款透传 |
| `services/shared/config/models.py:156-176` | `OmniProviderConfig/OmniConfig` 形态（avdetector 配置照此） |
| `configs/dev/server.yaml:43-77` | asr 段（active `alibaba_bailian`，`qwen-audio-3.0-asr-flash-streaming`） |
| `configs/dev/server.yaml:150-181` | omni 段（`qwen3.8-omni-flash-realtime`、voice Tina、workspace 专属域名+头）← avdetector provider 取值同源 |
| `services/session/state_machine.py` | `SessionState`（IDLE/LISTENING/THINKING/SPEAKING）；`transition_or_reject` 静默吞非法迁移（主动回合复用既有迁移序） |
| `packages/api-schema/common.json:128-140` | `StatusReasonCode` 枚举：`connected/asr_final/tts_start/tts_finish/interrupted/idle_timeout/mode_changed` |
| `apps/android/LivaChatSDK/.../SessionManager.kt:289-301` | Android `handleStatus` 只消费 `session_status` 映射状态机，**reason 不参与任何逻辑**（复用 reason 的客户端依据） |

### 2.2 模型/协议事实（沿用 `docs/qwen_omni_realtime.md`，勿重复调研）

- 模型 `qwen3.8-omni-flash-realtime` 必须走 workspace 专属 WS 域名 + `X-DashScope-WorkSpace` 头
  （`server.yaml:160-168`）；事件名两代一致；nested `audio.*` 16 kHz 生效；图片须 ≤1080p、
  base64 ≤256KB（**网关按 WS 单帧 262144B 硬限执行：超限直接 close 1009，见 §10 条目 28**）、
  **append 前该窗口须已 append 过音频**（§4.5、2026-10-03 探针再次复现）；单会话上限 120 分钟；
  音频历史 100 轮/600s。
- **空闲回收（2026-10-09 真机实测）**：会话 **300s 无输入** → 服务端以 **1007** 关闭，
  文案 `Your session was closed because no user input was received for 300 seconds.`
  （接收循环以 `receive loop crashed` warning 呈现——**属服务端空闲回收，非连接
  故障**；monitor 现有降级/懒重开自愈，恢复含 5s 冷却；判读崩溃日志先看 close 文案）。
- **semantic_vad 对非语音声不起回合**（2026-10-02 探针 A）→ 检测必须走 **manual**。
- manual 下：单条 `response.create` 即起一轮（无需 commit、无需文本入口）；模型对"最近听觉/视觉上下文"
  自发表达（2026-10-02 探针 C/C2/D/E）。
- 价格（北京）：音频入 ¥6/M、文/图/视频入 ¥1.5/M、文本出 ¥4.5/M（`qwen_omni_realtime.md` §2.2）。

## 3. 真机探针证据（2026-10-03，增量探针）

### 3.1 方法

- 脚本：`services/scripts/probe_avdetector_check.py`（[R11] 已入库；裸 WS 直连，与 provider 同协议；
  只读、不改产品配置）。
- 会话配置：`modalities: ["text"]`、`turn_detection: null`（manual）、nested 16k、instructions=检测器提示词
  （§4 D6 草案文案）；查询 = 裸 `response.create`（P2 另测 `conversation.item.create(input_text)` 文本入口）。
- 样本：合成咳嗽 = 双噪声爆发（**非真人**，2026-10-02 同款）；真实语音 = `~/projects/asr_test1.wav`
  （9.7s，仓库既有 ASR 测试样本，内容"你好，这是一个ASR的测试识别测试…"）；灰图=纯色 640×640；机器人图=
  `/tmp/lv3d_robot/robot_preview.png` 640×640。
- 复现：`cd services && set -a && . ../.env && set +a && ./.venv/bin/python scripts/probe_avdetector_check.py --variants N1,P1,P2,M,S,H`（产物落 `/tmp/lvdet_probe/`）

### 3.2 结果总表（2026-10-03 实测；`report.json` 原始记录在同目录）

| 变体 | 场景 | 回复 | 时延 |
|---|---|---|---|
| **N1** | 安静（0.8s 环境噪声） | `NONE` | 0.30s |
| **P1** | 环境噪声 + 合成咳嗽 | `{"event":"物体撞击","desc":"听到两声清脆的拍打或撞击声"}` | 0.43s |
| **P2** | 同 P1，但先 `conversation.item.create` 显式查询文本 | `{"event":"异响","desc":"音频中传来两声沉闷的拍打或撞击声"}`（item.create **格式被接受**） | 0.47s |
| **M-0** | 多轮会话·安静 | `NONE` | 0.27s |
| **M-1** | 多轮会话·咳嗽 | `{"event":"物体撞击","desc":"听到两声清脆的物体碰撞或敲击声"}` | 0.47s |
| **M-2** | 多轮会话·纯灰图 | `NONE` | 0.33s |
| **M-3** | 多轮会话·机器人图（该窗口先发音频） | `{"event":"画面内容","desc":"画面中出现一个白色的卡通机器人"}` | 0.44s |
| **S（×3）** | **真人说话**（9.7s 真实语音） | ✘ 3/3 **误报**：`物体掉落`×2、`静电噪音`×1（10-03 电池复测 1/4——误报随机波动，详 §3.5） | 0.41-0.48s |
| **H** | 咳嗽 → **静默 commit**（无 response.create）→ 安静 → 查询 | 无 error；事件 `committed → item.created → transcription.completed('')`；随后查询正常（0.43s） | — |

**衍生事实**：

1. **text-only 被接受**：`session.updated` 正常回显 `modalities:["text"]`——检测器无需音频输出
   （省 ¥12/M 音频出 + 免生成延迟）；响应事件为 `response.text.delta/.done`，`response.done` 内带 `usage`。
2. **"检测契约"成立**：安静→`NONE`、灰图→`NONE`（负样本）；咳嗽→JSON、机器人图→JSON（正样本）。
   合成咳嗽被归为"物体撞击/掉落"（2026-10-02 探针 D 同款现象）——**分类可用性取决于样本真实性**：
   噪声合成样本不可用、lv-audiotool 真实感样本可用（§3.5，结论修订）；**描述文本两轮均可用**（§4 D6）。
3. **说话否决器成立（关键）**：同轮内检测流的
   `conversation.item.input_audio_transcription.completed` —— 真人说话 → `'你好，这是一个ASR的测试识别测试。……'`
   （完整转写）；咳嗽 → **空串**。即"说话窗口"可用检测流自身的转写做**程序性否决**，弥补提示词判别不可靠。
4. **查询/提交会归档音频窗口**：事件序列含 `input_audio_buffer.committed → conversation.item.created →
   transcription.completed → response.created → …`（manual 下由 `response.create` 触发提交）——每次提交把
   "上次提交至今"的音频归档为一个会话条目。**窗口需要一个主动的界**：长静默下若无提交，未提交音频会无限
   累积（服务器缓冲行为不可预期、检查窗口失焦）——对策 = 周期静默提交（§4 D5 ★ 窗口纪律，变体 H 已验证）；
   会话条目随提交次数累积（限额对策同见 D5）。
5. **图片约束复现**：两次 image append 之间若无新音频 → `Error append image before append audio.`；
   补 0.8s 音频后同帧成功（该约束不影响实际链路——音频帧持续喂入）。
6. **usage（实计）**：安静查询 144 tokens（in 142=text 135+audio 7；out 2）；咳嗽查询 188
   （audio 21）；图片查询 ≈610（video 400 + audio 28 + text 180）。
7. **静默提交可行（窗口纪律依据，变体 H，2026-10-03）**：单发 `input_audio_buffer.commit`（不触发回复）
   不报错、流保持可用，且**每个提交都会下发该窗口的 `transcription.completed`**（咳嗽窗口为空串）——
   据此可把"转写否决"精确绑定到"上一次提交以来"的窗口。

### 3.3 与 2026-10-02 探针（`fd_pipeline_omni_mode.md` §11.2）的关系

- 10-02 证明"**机制**"：manual + `response.create` 起一轮、模型自发点评声音/图片（C/C2/D/E）；
- 10-03 证明"**检测器契约**"：text-only、NONE/JSON 判别、多轮重复、说话否决信号、usage/时延/成本——
  本节即 avdetector 设计的直接依据。两轮探针互为补充，均**只读**、产物落 `/tmp`。

### 3.4 成本估算（按实测 token × `qwen_omni_realtime.md` §2.2 价目）

- 安静/无事件查询 ≈ ¥0.0002–0.0003（音频 7 tok + 文本 ~140 tok in + 2 tok out）；
- 声音事件查询 ≈ ¥0.0005（音频 21 tok + 文本 148 in + 19 out）；
- 图片查询 ≈ ¥0.001（video 400 tok = ¥0.0006 为主）。
- 量级直觉：每 10s 一次图片检测 ≈ **¥0.36/小时/会话**（[R17] 拍板节流 10s；不含声音检测）；声音检测
  按"回合空识别"事件触发，频次远低于轮流对话的回合数。详尽预算见 §7 风险 1。

### 3.5 检测能力电池（2026-10-03；`--variants B,F`，可复跑）

**B 电池（合成信号 + 真人语音，n=4/类）**

| 样本 | 判定 | 标签 |
|---|---|---|
| 安静 | `NONE` 4/4 ✓ | — |
| 合成咳嗽 | JSON 4/4 | 物体掉落 / 敲击声 / 物体撞击×2 |
| 合成咳嗽 @25% 音量 | JSON 4/4 | 物体掉落×4（灵敏度好） |
| 敲门（合成） | JSON 4/4 | 异响×2 / 物体碰撞 / 物体掉落 |
| 口哨（合成） | JSON 4/4 | 异响×2 / 口哨声 / 电子音效 |
| 真人说话 | `NONE` 3/4、误报 1/4 | — |
| 纯灰图 | `NONE` 4/4 ✓ | — |
| 机器人图（新会话单图） | `NONE` **8/8** | —（见"图像定向"） |

**F 电池（lv-audiotool 生成的真实感样本，n=4/类）**

生成方式：`.venv/bin/lv-audiotool generate -p "…" --format wav --sample-rate 16000 --channels 1`
（人声非语言声音，1.2–3.5s，≈¥0.006/条；样本 `/tmp/lvdet_probe/samples/`；
`--variants F --samples-dir <dir>` 复跑；lv-audiotool 已迁至 videogo 仓库，2026-10-04）。

| 样本 | 判定 | 标签（4 次） |
|---|---|---|
| 咳嗽 a/b | JSON **8/8** | `咳嗽`×6、`cough`×2 —— **分类正确** |
| 喷嚏 a/b | JSON **8/8** | `喷嚏`/`打喷嚏`×6、`Sneeze`、`咳嗽` —— 7/8 正确 |
| 清嗓子 | JSON 4/4 | `清嗓子`×3、`清嗓` |
| 哈欠 | JSON 4/4 | `哈欠`×2、`打哈欠`×2 |
| 笑声 | JSON 4/4 | `笑声`×3、`laughter` |
| 吸鼻 | JSON 4/4 | `吸鼻涕`/`擤鼻涕`/`喷嚏`×2（同语义域内波动） |

响应时间（F 电池）：`response.created` 中位 0.277s、`done` 中位 **0.414s**（0.334–0.553s）——与 B 电池同档。

**结论（修订 §3.2 衍生事实 2）**：此前"分类不可依赖"是**噪声合成样本的伪影**——对真实感人声事件，
模型分类可用（咳嗽/喷嚏/清嗓子/哈欠/笑声均给出正确标签），标签 + 描述文本可共同驱动 LLM。
两个实现注意：①偶发**英文标签**（`cough`/`Sneeze`/`laughter`，32 次中 4 次）→ 解析需容忍中英；
②个别 run 无 `transcription.completed` 事件（"窗口无转写"= 不否决，实现按缺省空处理）。

**图像定向（2026-10-03）**：机器人图**新会话单图 8/8 `NONE`**（图确被摄入：usage `video_tokens=400`）；
但**多轮上下文中、灰度图之后出现**→ 描述（"画面中出现一个白色的卡通机器人"）；提示词加一句
"可辨认主体即算值得描述"→ **4/4 描述**。即图像主动受**提示词阈值**支配、"变化触发"天然成立。
**图像判定标准已按拍板修订**（清晰非过渡 + 较大变化 / 危险行为，§4 D6）；"可辨认即报告"的放宽句
**不采用**（单图冷启动、无变化、无危险 → 默认不报告）。

**多帧同窗定向（U1–U4，2026-10-03，为"帧缓存/变化对照"验证）**：manual、每张图前补 0.6s 音频、
同窗连续两图后一次查询：

| 序列 | 回复 |
|---|---|
| 灰图 → 机器人图（变化） | `NONE`（变化未触发） |
| 机器人 → 机器人（无变化） | `NONE` ✓ |
| 机器人 → 灰图（变化） | `{"event":"画面变化","desc":"画面中有一个白色卡通机器人…"}`（报"变化"但描述的是**前一帧**内容） |
| 灰图 → 咳嗽 → 机器人 | `{"event":"impact","desc":"画面中白色机器人出现，伴随两声沉闷的撞击声"}`（变化 + 声音补充） |

结论：**多图同窗协议可行**（usage `video_tokens=800`，两帧均被摄入、400 tok/帧），但"之前 vs 最新"的
变化判定**不稳定**（U1/U3 互相矛盾）——**检查上传默认单帧（最新）**，缓存仅作本地帧源与未来回溯素材
（§4 D2 [R16]）。**〔2026-10-09 修订：回溯素材一并放弃——[R16] 改为 K=1 帧槽（历史多帧从未被读），
见 §10 条目 27〕**

## 4. 设计

### D1 avdetector service 形态（新包 `services/avdetector/`）

```
services/avdetector/
  __init__.py
  base.py        # AvCheckResult / AvDetectorStream(Protocol) / AvDetectorProvider(Protocol)
  service.py     # AvDetectorService.open() -> (stream, provider_name)；active+fallback（照抄 omni/service.py）
  factory.py     # create_avdetector(cfg)（照抄 omni/factory.py 的 provider 白名单形态）
  providers/alibaba_avdetector.py   # DashScope realtime WS、manual + text-only + response.create
```

- **provider 独立实现、不扩展现有 `alibaba_omni.py`**：差异面（manual、`response.create`、text-only、
  检测结果收集）与 omni 消费语义（对话流）完全不同，硬塞会污染 omni 路径（omni 的 `semantic_vad` 是
  跑通验收的既有行为）；仓库先例 = 同族 provider 各自独立（asr/tts/livetranslate/omni 四份 ws 胶水各自维护）。
  代价是 ~250 行协议胶水重复（[R1] 拍板：接受，新写、不改 omni）。
- 上行事件：`input_audio_buffer.append` / `input_image_buffer.append` / `response.create` / `session.finish`；
  下行映射：`response.text.delta|done`、`response.done(usage)`、`conversation.item.input_audio_transcription.*`、
  `input_audio_buffer.committed`、`error`、`session.finished`。
- **接口形态（供 monitor 消费，非事件泵）**：一次查询 = 一次 await（内部收满本轮后返回），
  比 omni 的全量事件流更贴合"查询-应答"语义：

```python
@dataclass
class AvCheckResult:
    text: str                 # 响应全文（"NONE" 或一行 JSON）
    transcripts: list[str]    # 自上次查询以来的输入转写（说话否决信号）
    usage: dict | None

class AvDetectorStream(Protocol):
    async def feed_audio(self, pcm: bytes) -> None: ...              # 持续喂入
    async def feed_image(self, jpeg: bytes, *, width: int, height: int) -> None: ...  # 查询前喂
    async def check(self, *, timeout_s: float) -> AvCheckResult: ... # response.create + 收满一轮
    async def reopen(self) -> None: ...   # 周期重建（历史限额，D5）
    async def close(self) -> None: ...
```

- 建流参数照抄 omni 的采样率同源纪律（`pipeline_sample_rate`，16 kHz）；`generation_opts` 定为
  `temperature 0.2 / max_tokens 128`（[R6] 拍板；检测回复极短，约束成本与随机性）。
- `session.update` 差异（相对 `alibaba_omni.py:468-488`）：`turn_detection: null`（manual）、
  `modalities: ["text"]`、`instructions` = 检测器提示词（§4 D6）、不需要 voice。

### D2 orchestrator 接入：旁路 monitor + 音频/视频触发

新增 `services/session/av_detector.py`（`AvDetectorMonitor`，引擎形态基线 `omni_mode.py`）：

- 生命周期：`open()`（懒开：chat 模式下首帧音频或首个视频帧触发，镜像 ASR/TTS 懒重开）；
  `close()`（session.end / run() 收尾 / 切出 chat / 出错熔断）。
- 职责：流与重连（失败**静默降级**——不向客户端发任何 error，与 omni 的 1007 语义不同，检测器是
  best-effort 增强）、★**无阻塞 feed**（见下"喂入点"）、★**单管道串行**（音频/图片 append、commit、
  response.create、重建走同一串行发送任务——探针实测的"先音频后图"约束与窗口顺序由管道序天然保证；
  查询单飞）、
  结果解析（`NONE` / 一行 JSON `{"event","desc"}` / 不可解析→丢弃）、**转写否决**（窗口转写含意义文本 →
  该次结果作废）、★**窗口纪律**（周期静默提交，D5）、★**帧槽（2026-10-03 拍板 [R16]；
  2026-10-09 修订为 K=1）**：只保留**最新一帧**非 text 视频帧（本地单槽覆写，**零网络 IO**——
  与"不做连续帧上行"不冲突：上传只发生在检查时刻；原 10 帧环形缓存 `frame_cache_size` 因
  历史多帧从未被读而删除）、周期重建（D5）。
- 回调：结果经 `on_event(kind, desc, label)` 回调 orchestrator（label = 检测器 JSON 的 `event` 字段；
  **`is_danger_label(label)`**（[P6.10] 中英/变体归一——`'危险行为'`/`'Danger'`/`'Dangerous
  behavior'` 等同判）触发 [R15] 冷却豁免与 D3 提醒语气文案；由 orchestrator 再做最终门控与主动回合执行）。

**喂入点**（★ 2026-10-03 review 修订：热路径只做**同步投递**，网络 IO 全部在 monitor 自己的任务里做）：

| 位置 | 改动 |
|---|---|
| `_handle_audio:1037-1038` | 在 `_feed_asr_with_recovery(pcm)` 相邻处 `monitor.feed_audio(pcm)`——**同步入队**（monitor 内部有界队列 + 自有发送任务；队列满丢最旧并计数）。**不得**在此 await 检测流的 WS 写/懒开握手：检测流背压会阻塞 dispatch 主循环，连带拖慢 ASR/中断/视频消息处理（ASR 的 `await feed` 是既有基线，检测器不复制该模式） |
| `_handle_video` 帧入 buffer 后（`:1711-1728` 段） | ① **非 text 帧同步入 monitor 帧槽**（`monitor.feed_frame(...)`，纯本地 K=1 单槽覆写、零网络 IO；**任何状态都入**——`_handle_video` 本就全状态收帧）；**带 `payload.text` 的帧不入槽、不触发检测**（[R14] 延伸：text 帧归 v3 链路）。② 视频触发点：节流+门控通过时 `monitor.request_video_check()`（同步投递，同上；上行帧取**槽内最新帧**，与音频触发同源）。**不做连续帧上行**（review 修订弃用 1fps 网络版 `feed_frame`：徒增 video token 成本）；帧源为本消息的 `jpeg_bytes` 局部变量（入槽），**不得**经 `_snapshot_turn_images()` 取图（那是"取走+清空"语义、为 AF/落盘链路独占） |

喂入门控：`_pipeline_mode == "full"` 且 monitor 存在且未熔断；沿用既有状态门控（THINKING/IDLE 帧本就被
`_handle_audio:1018` 丢弃——即 AI 说话/思考时检测器"听不到"，天然减少自声污染与多余检查，记录为已知行为）。
懒开（首个待喂帧触发）同样在 monitor 后台任务完成：未就绪期间的帧直接丢弃（检测器无历史回放需求，首个
检查窗口短一点可接受），**绝不在 dispatch 热路径 await 建流**。

**触发点（核心设计，"ASR 没识别 → avdetector 识别"）**：

- **音频**：在 `_handle_audio` 的 turn-end 块内（`:1040` 起），新条件分支——
  `turn_ended` **且** `not meaningful` **且** `not had_asr_final` **且** 状态 LISTENING **且**
  ASR 健康（`_asr_stream is not None`，即不在 1001 冷却中；[R4] 拍板：ASR 故障期暂停主动，
  避免"真说话被当事件"的最坏组合）**且**
  本窗口有过"声音活动"（`_last_speech_mono` 距触发 < ~2s，即客户端 VAD 认为是 speech）→
  发起**一次检测查询**（后台任务，不阻塞 dispatch）。查询完成后按**三重门**决定是否主动：
  1. 回复非 `NONE` 且可解析为 JSON → 候选事件；
  2. **转写否决**：该查询窗口 `transcripts` 中出现"在说话"的转写则作废——判定为**去标点/空白后
     不同字符数 ≥2**（[P6.1] 拍板，2026-10-03：真咳嗽/喷嚏等发声类非语音事件会被两路 ASR
     转写成单字重复拟声 `'咳咳。'`/`'咳。'`，同一字符重复**不**否决、事件放行；正常词句
     `'好的'`/`'你好…'` 仍否决——说话误报（3/3~1/4 随机）的双闸兜底保留，§3.5）。
     **[P6.13] 修订（2026-10-06，方案 A，已实施）**：阈值 ≥2 → **≥5**——仅**成句级**
     （≥5 种字符）才算"在说话"，`'好的'`/`'OK'`/`'哈喽'` 等 2–4 种字符的短转写**放行事件**；
     说话误报兜底退化至投递侧（状态/在飞门 + 30s 冷却 + barge-in 链，边界与风险见 §10）；
  3. **竞态复检**："在说话"只认**文字证据**（[P6.2]/[P6.3] 两轮订正）——送达时拦截仅当：
     ① 触发后迟到 meaningful final（`_asr_final_sent`，触发门已保证其触发时为假）；
     ② 触发后**新出现且文本像话**的 interim（`_asr_interim_pending` 在触发后新转真 **且**
     最近 interim 文本 `is_speech_like_text()` ≥2 种字符——与转写否决同一判定）。
     **[P6.13] 同改 ≥5 种字符**（方案 A 共享单点；`'你好'` 等 2–4 种字符的短 interim 不再拦）。
     **语音帧快照不再单独拦**（[P6.3]：连续咳嗽的后续 VAD 帧 / 拟声 interim `'咳'` 不算
     "在说话"，否则事件自杀式拦截）；用户真开口的兜底 = 既有 barge-in 链（SPEAKING 中
     `user_voice` 打断）+ ASR 回合送达即 cancel。pipeline 未在飞、状态仍 LISTENING 同前。
  三重门负责"少触发"；**错触发的兜底是既有机制**：真实 ASR 回合随后到来时，`:1141` 的 `_cancel_pipeline()`
  会取消在途主动回合、按标准流程重开——主动发言不抢答。
- **视频**：`_handle_video` 帧入 buffer 后，非 text 帧先**同步入帧槽（K=1）**；节流条件（距上次视频查询
  ≥ `video_check_interval_s`，默认 **10s**（[R17] 拍板））、状态/竞态门（同音频，除"声音活动"项）**且该帧无 `payload.text`**
  （[R14]：带 text 的帧不做检测，交给既有 v3 文本回合链路——处理方式与现状完全一致）→
  `monitor.request_video_check()`（同步投递；monitor 后台完成"上送**槽内最新帧** → response.create"）；
  回复非 `NONE` → 主动描述（附该帧作 final ai_text 回图，与 v3 同款载荷；帧经回调以 `ChatImageFrame`
  传入，**不消费** `latest_video_frames`）。
  **视觉回合抑制窗（D8）不拦本触发**：开窗时另**强制补一次带图检查**（绕过 10s 节流）建立
  视觉基线；窗口内其它帧仍按本触发链路/节流正常检查，只在投递侧丢弃普通画面事件。
  **上传策略（2026-10-03 拍板，[R16]；2026-10-09 修订）**：**单帧（槽内最新）**；同窗多帧
  （"之前 vs 最新"对照）协议可行（探针实测 2 图同窗被摄入、计费 400 tok/帧）但**语义不稳定**
  （§3.5 U 定向：灰→机器人 NONE、机器人→灰反而描述机器人、灰→机器人+咳嗽才报"机器人出现"）
  ——多帧回溯（覆盖两次检查之间的画面）**一并放弃**（原预留从未启用；如重启需重新加缓存 + 探针复验）。
- **画面事件投递语义（[P6] 拍板，2026-10-03，取代此前"等待/排队"设想）**：
  **豁免主动发言冷却**（满足判定即发言；防打扰节制仅剩 10s 检测节流 + "AI 说话中丢弃"）；
  **LLM task 在飞 → 直接丢弃 + log**（实时性优先：旧画面过期无意义，等下一轮检测——不等待、不排队）；
  状态/竞态门同音频（丢弃 + log）。
  **视觉回合抑制窗（2026-10-22 增补，§4 D8，已实施）**：cam 首帧直起轮结束后 5s 内，
  **普通画面事件**（`kind == "画面"` 且非危险）在投递侧丢弃 + log，**不起主动回合**；
  危险事件（中英/变体标签）照常投递与抢占，声音事件不受影响。开窗时**强制补一次带图检查**
  （`request_video_check(force=True)`，绕过 10s 节流）建立视觉基线，其普通画面结果同样被丢弃；
  不能只依赖窗口内帧触发——5s 窗短于 10s 节流时可能一次检查都没有，重复描述只会被推迟。

- **附帧静音自愈（[P6] 拍板，provider 层）**：`feed_image` 在本窗口**无音频**时先 append
  **20ms 静音**再上图；**有音频则不补**。真机探针（G1–G6，§10[P6]）：约束为逐窗口
  （无注入时服务端报 `Error append image before append audio`，G1/G5 复现），20ms 静音注入
  即可满足（G2/G6 均被接受）。monitor 侧**已解除** `_pipe_window_has_audio` 附帧拦截、
  provider 防御性跳过**已移除**（约束由自愈承担）——**静默期（客户端 VAD 门控不发帧）画面检测可用**。
  〔2026-10-09（条目 28）：附帧另有**帧长硬限防护**——序列化帧超 262144B 先在 provider 层
  `image_resize` 重压（max_edge 960→640 两档），仍超则跳过附帧走纯音频检查〕
- **通道优先级（2026-10-03 拍板，[R13]）**：有图像时**图像为主、声音补充**；无图像时**仅检测声音**：
  - **查询契约（[P6.9] 修订）**：**视频检查**的窗口 = 自上次提交的音频 + 最新帧槽内帧；
    **音频补充检查纯声音**（不再附帧）——原"统一附帧"在音频通道双输：付 +400 画面
    token、按提示词"有图画面为主"大概率得画面描述，投递却按声音语义（不豁免冷却、
    final ai_text 不回带图）、日志 `kind="画面"` 与投递矛盾。画面变化归 10s 视频主通道。
    **提示词要求"有画面时以画面为主、声音只作补充；画面与上次相同不重复描述"**（§4 D6，
    原文保留——视频检查仍音画同查）——优先级的第一层是**输出语义**；
  - **触发调度**：视频帧触发为**主通道**（独立节拍 `_last_video_check_at` + 节流
    `video_check_interval_s`，**不受音频检查挤压**——[第四轮修复]）；音频 turn-end 触发为
    **补充通道**（距上次**任意**检查 ≥ `check_min_interval_s`=10s → 自然让位于视频节拍）；
    两通道共享主动发言冷却 `proactive_cooldown_s`（**画面/危险事件豁免**，[P6]/[R15]）；
  - **无帧（未开摄像头/帧槽为空）**：仅音频检测（不受 `video_check_interval_s` 限制）；
  - 代价记录：有图时咳嗽等事件最多被最小间隔（10s）延迟到下一窗口——"图像优先、声音补充"的既有取舍；
    **危险行为**检出延迟 ≤ 一个检测节流窗（`video_check_interval_s`，[R17] 拍板 **10s**）；危险事件的
    **主动冷却豁免已拍板**（[R15]：突破 `proactive_cooldown_s`）；〔2026-10-09：原"10 帧 ≈ 10s@1fps
    与节流对齐"论证随 [R16] 修订失效——K=1 帧槽只保证"检查时刻有最近一帧可用"，不做窗口覆盖〕。
- **主动发言冷却（投递侧门控）**：距上次主动回合 ≥ `proactive_cooldown_s`（[R3] 拍板 30s），
  在 `_on_av_event` 生效；SPEAKING/THINKING 中不主动（**危险抢占例外**——[P6]，见下条门控顺序）。
  **检测触发不受冷却冻结**
  （[P3] review 修复：触发层不再检查冷却——检测照跑，冷却窗内的危险行为才有 [R15] 豁免的机会）。
  **豁免**（[R15]/[P6] 拍板）：**`is_danger_label(label)`（[P6.10] 中英/变体归一）**
  **或带帧的画面事件**均跳过该冷却检查（危险=安全优先；画面=满足即发言）。
- **危险画面抢占（[P6] 拍板）**：危险事件到达时若 LLM task 在飞——
  在飞回合**不是**危险回合 → **抢占**：`_do_interrupt("av_danger")` 中断之并立即报危险
  （**含用户标准回合在飞**——安全优先；代价=当前用户回合被打断、ASR 流复位，接受）；
  在飞**也是**危险回合 → 丢弃 + log、等下一轮检测；
  危险 + 用户正在说话但尚无 pipeline（竞态复检场景）→ 丢弃 + log（不插话）。
  **抢占窗口补刀（[P6.6]，2026-10-03 review）**：`_do_interrupt` 返回后立即再 `await
  _cancel_pipeline()` 一次（幂等）——interrupt 内含多个 await，dispatch 循环可能在此窗口
  受理用户 turn-end / v3 文本回合并建了新 pipeline；不补刀则紧随其后的引用覆写使其成为
  无人取消的孤儿（双 LLM/TTS 并发）。补刀点到 `create_task` 之间零 await，单线程事件循环
  下原子（细则见 §10 第 18 条）。
  **三处创建点统一换装原语（[P6.8]，2026-10-03 review）**：反向插队同样存在——用户
  turn-end / v3 文本回合自己的 `_do_interrupt` 窗口内，av 危险事件装了 pipeline，会被
  随后的 `create_task` 覆写为孤儿。现 `_on_av_event` / turn-end / v3 文本三处创建统一走
  `_spawn_pipeline(coro, danger=…)`：覆写引用前**同步** `cancel()` 掉引用上的在飞任务，
  函数体零 await（写入原子），任何方向的插队者必被取消（细则见 §10 第 20 条）。
  **门控顺序（实现规格——防与既有门控打架，[P6] review 补）**：`mode==chat` 是唯一前置硬门；
  随后**先按"LLM task 在飞与否"分流**：
  - 在飞（state 必为 THINKING/SPEAKING，**不受"SPEAKING/THINKING 中不主动"与
    "状态仍 LISTENING"竞态门限制**）→ 按 `_pipeline_danger` 抢占/丢弃二分；
    **危险路径不经过竞态复检的在飞丢弃**（用户标准回合在飞时 `_asr_interim_pending` 可能为真，
    不得据此拦抢占）；
  - 无在飞 → 再走竞态复检（说话中→丢弃 + log）→ **视觉回合抑制窗**（`kind == "画面"` 且
    非危险 → 丢弃 + log，D8）→ [R15] 冷却豁免放行。
  **`_pipeline_danger` 标记维护**：`_on_av_event` 起轮时按 `is_danger_label(label)` 置 True/False；
  标准回合（turn-end）与 v3 视频文本回合的起轮处置 False；session.start 复位 False。
  所有丢弃/抢占路径均写 log（busy 丢弃 / preempt / 门控放弃按原因成行）。
- **纯静音不查**：静音窗口（无 VAD speech）不触发音频查询（会话开始后的首个"静音 turn-end"因此被跳过）；
  视频检测按帧到达独立节流。
- **已知边界（VAD 依赖）**：音频检查的触发挂在 turn-end 上——客户端 VAD 未标记 speech 的低能量异响不产生
  新 turn-end，因而不会触发音频检查（周期慢扫为未来可选兜底，本期不做、[R3] 范围外）。

**竞态与陈旧防护**（伪码，实现时逐条落测试）：

```python
# turn-end 空识别分支（_handle_audio 内）
if (turn_ended and not meaningful and not had_asr_final
        and self._ctx.state == SessionState.LISTENING
        and self._asr_stream is not None          # [R4] ASR 健康（非冷却期）
        and self._monitor is not None and self._monitor.ready()
        and (time.monotonic() - self._last_speech_mono) < 2.0):
    self._monitor.request_audio_check()      # 后台任务；结果经 on_event 回调
    # 注：主动冷却只在投递侧（on_event）检查——检测照跑，冷却窗内的危险行为
    # 才有 [R15] 豁免的机会（[P3] review 修复，原触发层冷却门已移除）

# 视频触发（_handle_video 内）：帧无 payload.text 先入 K=1 帧槽；节流 + 状态/竞态门通过
#   → self._monitor.request_video_check()        # 检查取槽内最新帧；[R14] text 帧不检测

# on_event 回调（orchestrator 侧，最终门控；label = 检测器 JSON 的 event 字段）
async def _on_av_event(kind, desc, label="", image_frames=None):
    if self._pipeline_mode != "full" or self._ctx.state != SessionState.LISTENING:
        return
    if self._pipeline_task is not None and not self._pipeline_task.done():
        return
    if (kind == "画面" and not is_danger_label(label)
            and time.monotonic() < self._av_visual_suppress_until):  # D8 视觉回合抑制窗
        return
    if not is_danger_label(label) and not self._av_proactive_cooldown_ok():  # [R15] 危险突破冷却
        return
    self._last_av_proactive = time.monotonic()
    self._pipeline_task = asyncio.create_task(
        self._start_thinking("", image_frames=image_frames, turn_token="",
                              event_note=self._build_event_note(kind, desc)))
```

（`event_note` 为 `_start_thinking` 新增参数，见 D3；`turn_token` 空串 = 不参与 4xx 回滚守卫，既有约定。）

### D3 主动回合执行：复用 `_start_thinking` + 事件注记

- **不新增 LLM/TTS 链路**：主动回合 = 现有 `_start_thinking` 后台任务（打断、epoch、状态迁移、
  `will_speak`、TTS drain、final ai_text 全部复用；客户端可正常打断）。
- **`_start_thinking` 新增 `event_note: str = ""`**（仅 chat 分支生效）：
  - chat 分支 messages 组装处（`:1412` 一带）把注记**并入 system 内容**
    （`event_note` 非空 → `system = system_prompt + "\n\n" + event_note`），不新增 message 条目、
    不依赖 provider 对多 system message 的支持；
  - `event_note` 文案模板（orchestrator 生成；[R7] 拍板，[P5] 收紧为强制回应口径）：

    ```
    【环境事件·系统注入】上一条用户消息来自环境监测（非用户本人发言）：{desc}。
    请立即用一句自然、简短的话回应上一条事件（主动关心或自然点评）；
    不要复述、不要延续之前的对话内容；不要提及"检测/系统/传感器"等字样。
    若事件为危险行为（event="危险行为"）→ 用紧迫语气提醒注意安全（如"小心，别摔着了"），
    优先于一切。
    （[P1] 不写死"来源/点评画面"；[P5] 强制回应 + 禁复述——防模型续写 assistant 尾条）
    ```

- **事件落 user 行（[P5]，2026-10-03 拍板）**：事件以 **user 消息**写入 `history/chat_store/recording`
  三件一体（kind="asr" + `ref_id="avd-…"`，沿用 v3 文本回合的 provenance 约定；视频事件附帧图像），
  内容 `【环境事件】{desc}`。**目的**：LLM 请求以 **user 消息结尾**（不再以 assistant 尾条结尾触发
  模型"续写/复读"，真机事故根治）；chatlog 读起来自然不突兀；4xx 回滚令牌 = ref_id（与 v3 同语义）。
  **门控顺序**：先门控（竞态/冷却/状态）后落库——被拦截的事件零落库。
- **状态与 reason**：`thinking→speaking→listening` 迁移照旧；`reason` 复用 `asr_final`（[R8] 拍板：
  零协议变更；依据 Android 不消费 reason，`SessionManager.kt:289-301`；新增 `proactive` 备选不做）。
- **落库归因**：`ctx.llm_provider` 无需变更（LLM/TTS 均为三段式服务）；detector 自身用量**二期**落
  ProviderUsage（[R9] 拍板）。

### D4 装配、配置与开关语义（关闭 == 现状）

- `shared/config/models.py` 新增：

```python
class AvDetectorProviderConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    workspace_id: str | None = None

class AvDetectorConfig(BaseModel):
    enabled: bool = False                 # 总开关；false → 服务不创建、路径全不激活
    active: str = ""
    fallback: list[str] = []
    system_prompt_path: str = "configs/dev/prompts/avdetector.txt"
    generation: LlmGenerationConfig = LlmGenerationConfig(temperature=0.2, max_tokens=128)
    check_timeout_s: float = 8.0          # 单次查询超时
    audio_commit_interval_s: float = 15.0 # 静默提交间隔（窗口纪律，D5 ★）
    check_min_interval_s: float = 10.0    # 全局最小检测间隔（[R13] 通道调度）
    # 〔2026-10-09 修订：[R16] 帧缓存 → K=1 帧槽，frame_cache_size 删除（§10 条目 27）〕
    video_check_interval_s: float = 10.0  # 视频检测节流（[R17] 拍板 30s→10s）
    image_disable_cooldown_s: float = 300.0  # 拒图停图时限（[P6.14] 拍板：到期恢复）
    proactive_cooldown_s: float = 30.0    # 两次主动发言最小间隔
    visual_turn_suppress_s: float = 5.0   # cam 首帧直起轮结束后的普通画面事件抑制窗（D8；危险豁免）
    reopen_interval_s: float = 600.0      # 检测流周期重建（历史限额）
    providers: dict[str, AvDetectorProviderConfig] = {}
```

- `configs/dev/server.yaml` 新增 `avdetector:` 段（provider 取值抄 omni 段同款域名/头/模型；
  **默认 `enabled: false`**，真机验收期临时置 true——[R2] 拍板，同 `omni.enabled` 交付纪律）。
- `application.py`：`cfg.avdetector.enabled` 时创建 `avdetector_service` + 载入 `avdetector_system_prompt`
  （失败仅告警 → 服务为 None）；`app.state.avdetector_service / avdetector_system_prompt`（`:255-269` 同款）。
- `endpoint.py`：`DialogOrchestrator(...)` 增传 `avdetector_service` / `avdetector_system_prompt` /
  `avdetector_cfg`（节流参数）/ 复用 `pipeline_sample_rate`。
- **关闭语义**：`enabled=false` → `avdetector_service is None` → orchestrator 不建 monitor、
  `_handle_audio/_handle_video` 的 feed 与触发分支因 `self._av_monitor is None` 全部短路——
  行为与现状一致（测试用"关闭态回归"用例锁定，§6）。
- **模式门控**：`_pipeline_mode != "full"`（含 omni/lecture/translate/transcribe）→ 不喂、不触发；
  `session.change` 切出 chat → `monitor.close()`；切回 chat → 下帧懒开。session.start 复位、
  session.end/run() 收尾关闭（`:437-452`、`:564-586`、`:381-391` 三处清单加 avdetector）。
- **关闭顺序不变式（[P6.5]，2026-10-03 review 修订）**：四类生命周期收口（`run()` 收尾 /
  `session.start` / `session.end` / `session.change` 切出）一律**先 close monitor、再做其余
  teardown 长 await**——否则复位/仍为 `"full"` 的窗口期内存活事件源会起 stale 主动回合
  （细则与四窗口分析见 §10 第 17 条）；切 omni 失败（1009 回滚）时恢复已关事件源。
  **`session.start` 另须取消在飞 pipeline（[P6.12]）**：同连接重发 start 时旧任务对
  `self._ctx/_history` 是实时引用，残余（TTS 尾音/final 落库/status）会泄进新会话
  （epoch 兜底不完全——新旧 ctx epoch 常同为 0，见 §10 第 24 条）。

### D5 生命周期与故障策略

| 场景 | 行为 |
|---|---|
| 建流/喂入失败 | 静默降级：warning 日志 + 冷却（照 `omni_mode.py:147-177` 的 5s 冷却），**不发客户端 error**；连续失败 **3 次**进入会话级熔断（本会话不再尝试；[R10] 拍板 N=3）。**[P6.11] 修订**：计数 = "自上次**成功开流/成功检查**以来的连续失败"——成功开流即清零（连接已恢复的证据；防瞬时故障跨开流累计误熔断）。已知边界：恒开流成功但恒使用失败的病态服务不再熔断（重连受 5s 冷却节制；服务不可达类故障仍靠开流失败累计熔断） |
| ★窗口纪律（2026-10-03 review 修订） | LISTENING 且窗口有未提交音频时，每 `audio_commit_interval_s`（默认 15s）发一次**静默 commit**（无 response.create）。修复两点：①长静默下未提交音频无界累积（服务器缓冲/限额行为不可预期）；②"说话后未提交的音频"混入后续检查窗口 → 转写否决误伤 + 模型误报。每个提交的 `transcription.completed`（变体 H 实测：必下发，含空串）结算旧窗口并清零窗口转写；检查否决只看"上次提交至今"。静默提交计入会话条目（15s 节奏 ≈40 条/10min，与 `reopen_interval_s` 配套，不触 100 轮限额）。**[P6.7] 补**：静默提交**发送失败 → provider 重连一次**（陈旧窗口随旧连接销毁；重连失败抛错由 monitor 降级）——否则连接未死时问题②会从该路径复活（§10 第 19 条） |
| 查询超时（默认 8s） | 该次结果**丢弃、不计熔断失败**（超时 ≠ 连接故障，[P2] review 修复）；**连续 2 次超时**视为流僵死 → 降级（关流 + 冷却）；连接错误/服务 error 帧仍直接降级计失败。超时即 best-effort `response.cancel`（[P4]：掐掉在途生成，防其残响串入下一轮窗口） |
| 查询进行中用户又说话 | 结果送达时被竞态复检/转写否决丢弃（§4 D2）；查询任务不取消（零成本） |
| 流被服务端终结 / 限额错误 / 周期到期 | `reopen()`：新 WS + 重试；周期重建默认 10min（检测器无对话状态需保留；重建后首次图片检测失去"与上次对比"基线，退化为"直接描述"，记录为已知行为） |
| 检测器返回不可解析文本 | 丢弃 + debug 日志（不猜测语义） |
| 会话切模式/结束 | close()（发 `session.finish` + 关 WS），照 `omni_mode.py:131-143` |

### D6 提示词（检测器 instructions，拍板随文生效）

`configs/dev/prompts/avdetector.txt`（**检测器角色独立提示词**，与 `companion.txt`/`omni_companion.txt`
互不复用）：

```
你是 LivaChat 的环境监测器，只做检测、不参与对话、不寒暄。
每次收到响应请求时，检查你最近收到的音频与图像，按以下判定输出：

【画面判定】（有图像时以画面为主，声音只作补充）
报告画面必须**画面清晰**（不是摄像头切换、模糊、剧烈晃动中的过渡帧），并满足其一：
  a) 最新的画面与之前的画面有较大区别、画面变换了 → 描述新画面；
  b) 画面中出现危险行为（如有人摔倒、小孩子的行为有潜在危险等）→ **立即报告**
     （危险优先于一切，此类不要求与上一帧有明显区别）。
画面不满足 a/b（无变化、不清晰、过渡帧）→ 不描述画面；此时若同窗口听到值得关心的
非语音声音事件（咳嗽、喷嚏、哈欠、异响、物体掉落等），改为报告该声音事件。

【声音判定】（没有图像时只判断声音）
有值得关心的非语音声音事件 → 报告；否则只输出：NONE。

输出格式：一行 JSON：{"event":"<事件类型>","desc":"<不超过40字的客观描述>"}；
危险行为的 event 固定为"危险行为"。都没有值得关注的内容 → 只输出：NONE。
不要输出任何其它文字。描述要客观、克制，不脑补画面外的信息。
```
（[R13] 优先级契约即本提示词的核心条款：有图 → 画面为主、声音补充；无图 → 仅声音；
图像判定三条 = ①清晰非过渡 ②与之前画面较大区别 ③危险行为——2026-10-03 拍板。）

**已实测的边界（诚实声明）**：① 说话误报**随机波动**（2026-10-02 探针 3/3；10-03 电池 1/4，§3.5）——
不能只靠提示词，**转写否决 + ASR 竞态复检**双闸必留（§4 D2）；② 分类可用性取决于样本真实性：
噪声合成样本不可用（"撞击/掉落"）、lv-audiotool 真实感样本可用（咳嗽/喷嚏/清嗓子/哈欠/笑声，§3.5）——
**描述文本两轮均可用**；偶发英文标签（`cough`/`Sneeze`/`laughter`）需解析容忍；
③ 图像判定标准已按拍板修订（清晰非过渡 + 较大变化 / 危险行为，见上）——"可辨认主体即报告"的放宽句
**不采用**，单图冷启动（无变化基线、无危险）默认不报告；④ 真人录音样本验收仍待做（§6.2，[R5]）。

### D7 协议与客户端影响：零变更

> **v1 修订（2026-11-06）**：本节“协议零变更”仅适用于 AVDetector v1。会话级开关增强新增
> `session.start.config.avdetector`、`session.change(mode="config")`、`session.changed(mode="config")`
> 与错误码 `1011 SESSION_CONFIG_FAILED`，详见 `docs/avdetector_improvements.md`。

- 下行为既有 `ai_text`（可带 image）/`ai_audio`/`status`——Android SDK 对无前置 transcription 的
  `ai_text/ai_audio` 的播放路径与普通回复一致（消息由 `message_id/index/is_final` 组织，主动回合
  只是"没有用户那句话"，播放器不依赖用户消息存在）。
- `status.reason` 复用 `asr_final`（依据：`SessionManager.kt:289-301` 只消费 `session_status`）。
- 客户端**不需要**知道/展示"这是主动发言"；不新增 UI 状态。[R8] 备选"新增 reason 让客户端可标注"
  代价 = `common.json` enum + Python/Kotlin DTO 重生成 + 各端逻辑确认，默认不做。

### D8 视觉回合抑制窗（2026-10-22 增补，已实施）

**问题（真机日志，2026-10-22 12:22，UTC+8）**：cam 首帧直起轮在第一帧触发用户回合并完成回复；
答复结束回到 LISTENING 后，后续普通视频帧触发本会话**第一次带图检测**（此前只有音频检查，检测器
没有视觉基线），检测器把同一画面判为"画面变化"→ 又起一条主动回合。用户视角即"一个 video message
得到两条关于同一场景的回复"：

```
12:22:31.805  C→S video（turn_id 变化、无 text，首帧）→ cam 首帧直起轮（[相机画面]，ref=cam-*）
12:22:34.675  S→C 回复 #1（针对该帧）→ TTS 播报
12:22:35.65   回复 #1 结束 → LISTENING
12:22:36.42   C→S 下一帧（无 text）→ 视频检查：节流/状态/在飞门全部通过（会话首次带图检查）
12:22:37.31   检测器返回 画面变化（同一桌面）→ _on_av_event（带帧 → 画面事件豁免冷却）
12:22:39.76   S→C 回复 #2（同一场景的主动描述）
```

**根因**：[R14] 只排除**带 `payload.text` 的帧**；cam 首帧直起轮用的是无 text 帧，其后的相机流帧
在 pipeline 结束后重新满足视频触发门；画面事件按 [P6] 豁免 30s 主动冷却；现有门控没有
"用户刚看过这张画面"的抑制窗。

**行为规格**：

1. **开窗（覆盖所有 cam 采集轮首帧）**：触发判定沿用现有 cam 分支——`payload.turn_id` 非 0
   且与 `_last_video_turn_id` 不同即**首帧**；**同一 `turn_id` 的后续帧不算首帧**（不起轮，
   也不开窗）。首次开相机、切换前后摄像头、停止后重新打开等任何使 `turn_id` 变化的采集轮，
   其首帧直起轮在**未被中断的收尾**（含 LLM 失败但仍走到 LISTENING 的收尾）时各自开启 5s
   窗口；每个新 `turn_id` 独立一轮/一窗（切镜头/重开后同样抑制）。ASR 回合、v3 视频文本回合、
   AVDetector 自身主动回合均不开窗。被打断/被换装的 cam turn 不经过该收尾，不开窗
   （回复未完成或被新回合接管）。
2. **窗口长度**：`visual_turn_suppress_s`，默认 **5.0s**；`<= 0` 关闭该特性。窗口内即使发生
   真实的普通画面变化也不主动描述（最长 5s，危险行为例外）——这是换取"不重复描述刚看过的
   画面"的已知取舍。
3. **投递侧门控**（`_on_av_event`，位于竞态复检之后、主动冷却之前）：
   **`kind == "画面"` 且 `not is_danger_label(label)` 且窗口未过期 → 丢弃 + log**。
   门控在落库之前，被丢事件零落库，也不更新 `_last_av_proactive`。
4. **危险豁免**：复用 `is_danger_label`（中英/变体归一），危险事件照常投递；在飞抢占逻辑不变。
5. **声音通道事件不受影响**：`kind == "声音"`（无帧检查）不进该门；带图检查即使返回声音标签，
   `kind` 仍为 `画面`，窗口内会被丢弃（≤5s，见已知边界）。
6. **用户回合不受抑制窗影响**：门控只在 `_on_av_event`，不触碰 ASR/v3/cam 回合链路；
   危险事件的既有抢占行为不变（安全优先，可中断用户回合）。
7. **开窗强制补一次检查（保证基线更新）**：开窗同时调用
   `request_video_check(force=True)`——绕过 `video_check_interval_s`（仍要求 monitor ready +
   帧槽非空），让当前画面立即进入检测器上下文；该检查的普通画面结果由门控丢弃，危险结果照投。
   不能只依赖"窗口内下一帧触发检查"：5s 窗短于 10s 视频节流，若回合结束距上次检查 <5s，
   窗口内可能没有任何检查，基线不会更新，窗口结束后第一次检查仍会把同一画面当"新画面"报告。
   强制检查也保证危险检测时效不被窗口拖慢（危险命中延迟 ≤ 一个检测窗，不额外等 5s）。
   窗口内其它帧仍走既有触发/节流，检测触发本身不关闭。`visual_turn_suppress_s <= 0` 时不开窗、
   也不强制检查。成本：每个 cam 首帧 turn **最多**额外 1 次带图检查（窗口内本会触发常规检查时
   由强制检查替代；频率 = 开相机/切镜头次数，远低于 10s 常规视频节流；单次成本见 §3.4）。
8. **复位**：`session.start` 时 `_av_visual_suppress_until = 0.0`。

**实现锚点**：

| 位置 | 改动 |
|---|---|
| `services/shared/config/models.py` `AvDetectorConfig` | 新增 `visual_turn_suppress_s: float = 5.0` |
| `configs/dev/server.yaml`、`configs/test/server.yaml` `avdetector:` 段 | 显式写 `visual_turn_suppress_s: 5.0` |
| `services/session/orchestrator.py`（`_last_av_proactive` 旁） | 新增 `_av_visual_suppress_until: float = 0.0`；session.start 复位 |
| `services/session/av_detector.py` `request_video_check` | 新增 `force: bool = False`；force 绕过 `video_check_interval_s`（ready + 帧槽非空仍必需），照常更新 `_last_video_check_at` / `_last_check_at` |
| `orchestrator.py` `_start_thinking` | 新增 kwarg `visual_turn: bool = False`；未中断的 LISTENING 迁移处按配置开窗，并**强制补一次视频检查** |
| `orchestrator.py` 开窗 helper | `suppress_s > 0` 时**先设 deadline**（monitor 缺失也照设，窗口无副作用）；随后调用 `request_video_check(force=True)`，monitor 未就绪/帧槽为空时仅跳过强制检查 + log |
| `orchestrator.py` cam 首帧分支 | `_start_thinking(..., visual_turn=True)` |
| `orchestrator.py` `_on_av_event` | 冷却判断前新增普通画面事件抑制门 |

**已知边界**：

- 带图检查偶发返回**声音**标签时，`kind` 仍由"有没有帧"决定为 `画面`，窗口内会被一并丢弃（≤5s）；
  标签非封闭集合，不做 label 级细分。
- 开窗时 monitor 未就绪 / 帧槽为空 → 强制检查被跳过（log），该轮无基线更新；窗口结束后首个
  常规检查仍可能重复描述。
- 拒图停图期（`image_disable_cooldown_s`）内，强制检查按既有语义退化为纯音频、不建立视觉
  基线；该期间本身不会产生普通画面事件，到期后常规检查重新建立基线。
- 相机切换瞬间若开窗时帧槽仍是过渡/模糊帧，视觉基线质量差，后续稳定画面可能再次被判为变化；
  prompt 已要求不报告过渡帧，接受该边界（真机验收覆盖切镜头场景）。
- 检测成功耗时 >5s 时结果可能落在窗口外（正常约 1s；超时结果本就丢弃）。
- v3 视频文本回合存在同形风险（text 帧不入检测器帧槽，后续无 text 帧会触发视频检查）——本轮
  **不覆盖**（拍板：按需求只覆盖 cam 首帧；后续如需要，在该 spawn 处同样传 `visual_turn=True`）。
- ASR 回合若经 AF 快照携带相机帧（用户语音提问画面）也存在同形风险——本轮**不覆盖**（拍板只
  覆盖 cam 首帧）；后续如需要，在该 turn-end spawn 处同样传 `visual_turn=True`。

**拍板（2026-10-22）**：① **只在投递侧抑制**（不改变危险/声音/用户回合链路）；开窗时**强制补
一次视频检查**更新视觉基线，窗口内常规触发不关闭（保危险检测时效）；② **只覆盖 cam 首帧
turn**——同一 `turn_id` 的后续帧不算首帧；切换摄像头/重新打开摄像头等新 `turn_id` 首帧各自
开窗；v3 视频文本回合本轮不纳入；③ 窗口默认 **5.0s**，走 `visual_turn_suppress_s` 配置，
`<= 0` 关闭（同时不强制检查）。

## 5. 消息流

### 5.1 音频主动（咳嗽场景）

```
C→S audio(vad=silence/speech) ──► 既有链路（解码一次）
                                   ├─► ASR（照旧）
                                   └─► monitor.feed_audio（同步入队 → 后台 sender 上行）；
                                       monitor 每 15s 静默 commit 划窗
C→S audio(vad=silence≥600ms)
S:  TurnDetector turn-end → final_text 空 → meaningful=False
S:  [avdetector 触发] response.create（检测查询；提交"上次提交至今"的窗口）
S←  detector: transcription.completed=''（非说话窗口）→ response='{"event":"…","desc":"…"}'
S:  三重门通过 → 事件落 user 行（【环境事件】…，ref_id=avd-*）→ 主动回合（_start_thinking(event_note=…)）
S→C status{thinking,asr_final} → ai_text(delta…) → status{speaking,tts_start} → ai_audio(Opus…)
S→C ai_text(final) → status{listening,tts_finish}
（期间用户开口 → 既有 interrupt/barge-in 链路照常打断）
```

### 5.2 视频主动（画面描述场景）

```
C→S video(JPEG, 无 text) → buffer + ack（照旧；帧不连续上行检测器）
C→S video(JPEG, 带 text) → 直接走既有 v3 文本回合链路（LLM 处理；avdetector 不参与，[R14]）
S:  [无 text 帧] 距上次视频查询 ≥10s（[R17]）且状态/竞态门通过 → monitor 上送槽内最新帧 + response.create
    [P6] 上送前窗口无音频 → 先补 20ms 静音（自愈）；事件到达时 LLM 在飞 → 普通画面丢弃 + log、
    危险抢占（在飞亦为危险 → 丢弃 + log）；命中即发言（画面事件豁免主动冷却）
S←  detector: '{"event":"画面内容","desc":"…"}'（或 NONE → 丢弃，等下一个节流窗；
    回复以画面为主，声音事件只作补充——统一契约 [R13]）
S:  主动回合（event_note 含画面描述；final ai_text 回带该帧 image，与 v3 同款载荷）
```

### 5.3 cam 首帧直起轮后的视觉回合抑制窗（D8）

```
C→S video(turn_id 非 0 且与上次不同, 无 text) → cam 首帧直起轮 → 回复（用户已看过该画面）
    （首次开相机 / 切换摄像头 / 重新打开摄像头等每个新 turn_id 都各自触发一轮）
S:  回复结束 → LISTENING → 开启 visual_turn_suppress_s（默认 5s）抑制窗
S:  同时强制 request_video_check(force=True)（绕过 10s 节流）→ 当前帧写入检测器视觉基线
C→S video(同一 turn_id, 无 text) → 不触发首帧直起轮，也不重复开窗
C→S video(无 text, 窗口内) → 视频检查按既有节流照跑（基线已由强制检查建立）
S←  detector 普通画面事件 → 投递侧丢弃 + log（不起主动回合；危险事件例外）
S←  detector 危险事件     → 照常投递/抢占（不等窗口）
S←  detector 声音事件     → 照常投递（抑制窗只针对普通画面）
S:  窗口过期 → 后续普通画面事件恢复既有门控（画面事件仍豁免 30s 主动冷却）
```

## 6. 测试计划

### 6.1 服务端单测（`services/tests/unit/`）

- `avdetector/`（新）：provider 假 WS——握手（manual + text-only payload 断言）、`check()` 收集
  （text-only delta / NONE / JSON / 超时 / error）、转写事件映射、图片 append 前置音频约束的调用序断言、
  reopen/冷却。
- `session/test_av_monitor.py`（新）：monitor 门控矩阵——NONE 丢弃、不可解析丢弃、转写否决
  （'像在说话'判定，[P6.1]/[P6.13]）、
  查询串行化、冷却、熔断、周期重建触发；★feed 无阻塞（有界队列、满则丢最旧并计数、不 await 网络）；
  ★窗口纪律（静默 commit 周期触发、提交结算"旧窗口"转写、检查否决只看上一提交以来）；全局最小检测间隔
  `check_min_interval_s`（[R13] 补充通道节流）；★帧槽（**K=1**、text 帧不入、全状态入槽、
  **视频检查**上行取槽内最新帧——[P6.9]：音频补充检查不附帧；〔2026-10-09 修订：原 10 帧上限
  用例随缓存删除，新增"槽空不触发"改写〕）；★强制视频检查（`force=True` 绕过
  `video_check_interval_s`，但仍要求 ready + 帧槽非空，且照常更新两个检查时间戳）。
- `session/test_orchestrator_avdetector.py`（新，orchestrator 级，fake monitor/service）：
  - 触发条件：`turn_ended && !meaningful && !had_asr_final && speech 窗口` 才发起查询；纯静音不查；
  - 竞态：触发后 ASR 迟到 meaningful final → 主动丢弃、标准流程照常（断言无主动 ai_text）；
    错触发兜底：在途主动回合被随后真实 ASR 回合经 `_cancel_pipeline` 取消（断言无抢答）；
  - 视频：查询上送当帧字节且**不消费** `ctx.latest_video_frames`（fake monitor 断言收帧、v3/AF 取图不受影响）；
    带 `payload.text` 的帧**不触发检测**（[R14]：fake monitor 零调用，v3 文本回合行为不回归）；
    非 text 帧**无论会话状态**均同步入 monitor 帧槽（fake 断言；THINKING/SPEAKING 期间照常入槽）；
  - 优先级（[R13]）：无帧 → 音频触发不受 `video_check_interval_s` 限制；有帧 → 音频补充触发受
    `check_min_interval_s` 约束；两通道共享 `proactive_cooldown_s`（断言间隔下限；危险事件除外）；
  - 危险行为：`{"event":"危险行为",…}` → 走主动回合、event_note 为提醒语气、**突破主动冷却**
    （[R15] 已拍板：断言"冷却窗口内危险事件仍触发、常规事件被压制"两者差异）；
  - 视觉回合抑制窗（D8）：cam 首帧直起轮（`turn_id` 非 0 且与上次不同）未中断收尾后开窗；
    同一 `turn_id` 的后续帧不起轮/不重复开窗；不同 `turn_id`（切镜头/重开）各自起轮并各自开窗；
    开窗同时断言 `request_video_check(force=True)` 被调用（即使距上次检查 <10s）；
    monitor 未就绪/帧槽空时跳过强制检查并 log；窗口内普通画面事件丢弃且零落库、不起主动回合；
    窗口内帧的视频触发不被抑制（常规 `request_video_check` 仍调用）；危险事件（含英文变体）
    照常投递；无帧声音检查不受影响（带图检查返回声音标签时按 `kind=画面` 丢弃）；窗口过期后
    恢复；`visual_turn_suppress_s <= 0` 时不开窗也不强制检查；session.start 复位窗口；
  - 模式门控：translate/transcribe/omni/lecture 下不喂不触发；切出 chat 关 monitor、切回懒开；
  - 主动回合 s2c 序列：status(thinking) → ai_text → status(speaking) → ai_audio → listening；
    事件注记进入 LLM system 内容（fake llm 捕获 messages 断言）；history/chat_store **无 user 行**、
    assistant 行照常；可被 interrupt 打断；
  - **关闭态回归**：`avdetector_service=None` 时全量既有用例（chat 主路径）不回归——新增用例断言
    `_handle_audio/_handle_video` 无额外调用、下行字节序列与基线一致。
- `tests/integration/`：application 装配断言（enabled=true → `avdetector_service` 非 None；
  false → None；prompt 载入自新文件）；cam 首帧 turn 完整结束后窗口被置为未来时间，
  普通 ASR 回合结束后不置窗（D8）。

### 6.2 真机验收（服务端，`avdetector.enabled=true`）

1. **声音正样本**：真人咳嗽/喷嚏/哈欠各 ≥3 例 → 达标线（[R5] 拍板）：每类命中 ≥2/3；
   记录检测回复原文与分类。
2. **说话无扰**：正常对话 20 轮 → **零**主动插入（转写否决生效）；插话打断主动回合正常。
3. **画面主动**：开摄像头对静态场景（不触发）→ 移入新物体/换场景（触发描述）；灰墙/遮挡（不触发）。
4. **频率与冷却**：连续咳嗽/连续画面变化 → 主动间隔 ≥ cooldown；不自问自答刷屏。
5. **回声**：外放音量下 AI 说话期间无自触发（帧在 SPEAKING 丢弃 + 冷却）；TTS 尾音不引发误检测。
6. **降级**：断网/无效 key → 无主动行为、无客户端错误帧、chat 主链路无感（对比关闭态日志）。
7. **长会话**：>30min 连续使用 → 周期重建生效、无流终结哑掉、内存/连接无泄漏。
8. **视觉回合抑制窗（D8）**：开摄像头首帧触发一次回复后，同一场景持续 10s+ **只出现一条回复**
   （抑制窗内普通画面事件被丢）；切换摄像头/重新打开摄像头（新 `turn_id`）各自触发回复后，
   同样只出现一条、不叠加同场景主动描述；换到差异明显的场景后仍能主动描述；危险事件在窗口内
   照常即时提示。

### 6.3 探针入库（已完成）

`services/scripts/probe_avdetector_check.py`（[R11] 拍板落地，2026-10-03；原 `/tmp/lvdet_probe/probe2.py`）。
- **B 变体（能力矩阵）**：`--variants B --repeat N`（合成信号 + 真人语音 + 图像正负样本）。
- **F 变体（真实感文件电池）**：`--variants F --samples-dir <dir>`——样本用 lv-audiotool 直出
  16k/mono wav（生成命令见 §3.5；lv-audiotool 已迁至 videogo 仓库，2026-10-04）。
真人样本验收（§6.2-1）时以真人录音替换生成样本复跑。

## 7. 风险与回滚

| # | 风险 | 等级 | 说明与对策 |
|---|---|---|---|
| 1 | 成本失控（持续音频+周期图片检测） | 中 | 实测单次查询 ¥0.0005–0.001（§3.4）；图片查询默认 **10s** 节流 ≈ **¥0.36/h/会话**（[R17]）；音频查询仅事件触发。可调参数全在配置（interval/cooldown/max_tokens）；用量观测按 [R9] 拍板放二期 |
| 2 | **说话误报**（模型把说话当异响） | 高 | 实测 3/3（S 变体）——**不能只靠提示词**。对策：ASR 竞态复检 + 检测流转写否决双闸（§4 D2）；ASR 故障期暂停主动（[R4] 拍板，已并入触发门控） |
| 3 | 真人咳嗽识别质量未知 | 中 | 合成咳嗽仅证明机制（且被归为"撞击"）；验收用真人样本（§6.2-1）。分类不准不致命——LLM 按描述泛化关心，但需体感评审 |
| 4 | 自声/回声触发 | 中 | SPEAKING/THINKING 帧本就丢弃（`_handle_audio:1018`）+ 主动冷却；外放场景依赖设备 AEC，验收专项（§6.2-5） |
| 5 | 检测历史限额（100 轮/600s、196k tokens） | 中 | 每次查询提交一个音频条目、图片为 video tokens——周期重建（默认 10min）兜底；限额错误按 reopen 处理 |
| 6 | 主动发言打扰/冒犯 | 中 | 冷却 + 三闸门控 + prompt 克制条款；主动文案自然度验收；样式可全配置回滚 |
| 7 | 隐私（画面/声音持续上云） | 中 | 与 omni/v3 链路同等性质（已在上云）；本文档明示；客户端承诺文案不在本次范围（另立需求） |
| 8 | 探测引入 orchestrator 回归 | 中 | 全部门控 `service is None → 短路`；关闭态回归用例锁字节序列（§6.1） |
| 9 | ★检测流背压阻塞主链路 | 高（若不修） | review 修订已内建对策：热路径仅**同步入队**（有界队列丢最旧），网络 IO 全在 monitor 任务；用例锁"网络慢/挂起时 dispatch 不受影响"（§6.1） |
| 10 | 回滚 | — | **配置级**：`avdetector.enabled=false`（即现状）；代码级 revert 部署；检测器独立包/独立 WS，不与三段式、omni 共享状态 |
| 11 | cam 首帧直起轮后重复主动描述同一场景（2026-10-22 真机） | 中 | D8：回合结束开 5s 视觉抑制窗，普通画面事件投递侧丢弃；危险豁免、声音/用户回合不受影响；开窗强制补一次带图检查建立视觉基线；窗口值可配置为 0 关闭 |

## 8. 改动清单（预估）

| # | 文件 | 改动 |
|---|---|---|
| C1 | `configs/dev/prompts/avdetector.txt`（新） | 检测器提示词（§4 D6） |
| C2 | `services/shared/config/models.py` | `AvDetectorProviderConfig`/`AvDetectorConfig` + `AppConfig.avdetector` |
| C3 | `configs/dev/server.yaml` | `avdetector:` 段（默认 `enabled: false`，provider 抄 omni 域名/头/模型） |
| C4 | `services/avdetector/{__init__,base,service,factory}.py`（新） | 服务骨架（照抄 omni 四件套形态）+ `AvCheckResult`/协议 |
| C5 | `services/avdetector/providers/alibaba_avdetector.py`（新） | DashScope realtime WS：manual + text-only + `response.create` + `check()` 收集 + 转写事件 + reopen/close |
| C6 | `services/session/av_detector.py`（新） | `AvDetectorMonitor`：懒开（后台）/★无阻塞 feed（有界队列+发送任务）/★静默 commit 窗口纪律/★帧缓存（10 帧非 text，[R16]）/查询串行/全局最小检测间隔（[R13]）/解析/转写否决/冷却/熔断/周期重建/`on_event` 回调 |
| C7 | `services/session/orchestrator.py` | 新 kwarg（`avdetector_service`/`avdetector_system_prompt`/`avdetector_cfg`）；`_handle_audio:1037` 旁路 feed（同步投递）+ turn-end 空识别触发分支（`:1040` 块内）；`_handle_video` 非 text 帧同步入 monitor 帧缓存（本地，[R16]）+ 视频触发点（上行取缓存尾、不消费 buffer、**带 `payload.text` 帧不入缓存不触发**（[R14]）；**无连续帧上行**）；通道优先级调度（[R13]）；`_start_thinking` 增 `event_note`；主动冷却/竞态门控；三处生命周期清单（start/end/run teardown + session.change 切出 chat）；monitor 懒开 |
| C8 | `services/app/application.py` | `cfg.avdetector.enabled` 时创建 service + prompt；`app.state` 暴露 |
| C9 | `services/gateway/endpoint.py` | orchestrator kwarg 透传 |
| C10 | `services/tests/**` | §6.1 全部用例 |
| C11 | 文档 | 本文档状态；`fd_pipeline_omni_mode.md` §11 增补"chat 平行方案已立项"交叉引用（实施后） |
| C12 | `services/scripts/probe_avdetector_check.py`（新） | 探针入库（[R11]，**已随本文档落地**） |
| C13（D8） | `services/session/orchestrator.py`、`services/session/av_detector.py`、`services/shared/config/models.py`、`configs/{dev,test}/server.yaml`、`services/tests/**` | 视觉回合抑制窗：cam 首帧 turn 未中断收尾后开窗 5s；开窗强制补一次带图检查（`force` 绕过 10s 节流）建立视觉基线；`_on_av_event` 投递侧丢弃普通画面事件（危险/声音/用户回合不受影响） |

〔2026-10-09 修订：本表 C6/C7 所述"帧缓存（10 帧非 text）/入 monitor 帧缓存/上行取缓存尾"随
[R16] 修订改为 **K=1 帧槽**（`feed_frame` / `request_video_check` 接口签名不变）；C2/C3 同步
删除 `frame_cache_size`。见 §10 条目 27；本表其余行不变。〕

## 9. 拍板记录（2026-10-03，全部按本文推荐执行）

| # | 拍板结果 | 落点 |
|---|---|---|
| R1 | **新写** `alibaba_avdetector.py`；不改 `alibaba_omni.py` | §4 D1 |
| R2 | dev 默认 `enabled: false`；真机验收期临时置 true | §4 D4 |
| R3 | 视频检测 30s（后由 [R17] 修订为 **10s**）/ 主动冷却 30s / 查询超时 8s / 周期重建 10min（均配置项） | §4 D4 |
| R4 | ASR 故障期**暂停主动**（触发门控加 `_asr_stream is not None`） | §4 D2 |
| R5 | 真人样本达标线：咳嗽/喷嚏/哈欠各 ≥3 例、每类命中 ≥2/3；20 轮对话零主动插入 | §6.2 |
| R6 | 检测 generation_opts：`temperature 0.2 / max_tokens 128` | §4 D1 |
| R7 | 事件注记按 §4 D3 模板原文 | §4 D3 |
| R8 | status reason **复用 `asr_final`**（零协议变更；不新增 `proactive`） | §4 D7 |
| R9 | 检测器用量归因放**二期**（先靠日志观测） | §4 D3 |
| R10 | 会话级熔断阈值 **N=3**（**[P6.11] 修订**：失败计数在成功开流/成功检查时清零——= "自上次成功以来的连续失败"） | §4 D5 |
| R11 | 探针入库 `services/scripts/probe_avdetector_check.py`（**已落地**） | §6.3 / 附录 A |
| R12 | 代码命名统一 `avdetector`；文档文件名 `session_avdetector.md`（**已落地**）。**〔2026-10-09：文档改名 `fd_pipeline_avdetector.md`，对齐 `fd_pipeline_*` 文档家族；代码命名不变〕** | 文首状态行 |
| R13 | **通道优先级**：有图 → 图像为主、声音补充（音频补充触发受 `check_min_interval_s`=10s 约束）；无图 → 仅声音。**[P6.9] 修订**：查询契约按通道拆分——视频检查 = 音频 + 最新帧；**音频补充检查纯声音不再附帧**（画面变化归视频主通道） | §4 D2 |
| R14 | 带 `payload.text` 的视频帧**不做检测**，走既有 v3→LLM 链路（处理方式同现状） | §4 D2 |
| R15 | 危险事件调度**已拍板：突破主动发言冷却**（安全优先，`is_danger_label(label)` 豁免——[P6.10] 中英/变体归一；仍受检测节流）；event_note 提醒语气已写入 D3 模板 | §4 D2/D3 |
| R16 | avdetector **本地帧缓存**：最近 **10** 帧非 text 视频帧（text 帧不入缓存）；检查上传默认单帧（缓存尾）；多帧同窗协议可行但语义不稳，暂不上行。**〔2026-10-09 修订：10 帧环形缓存 → K=1 最新帧槽——历史多帧从未被读、删除；`frame_cache_size` 配置删除；多帧回溯放弃。见 §10 条目 27〕** | §4 D2 |
| R17 | 视频检查节流 `video_check_interval_s` 30s → **10s**（成本 ≈¥0.36/h/会话；与 10 帧缓存窗口对齐）。**〔2026-10-09：原"与 10 帧缓存对齐"理由随 [R16] 修订失效；10s 节流本身不变〕** | §4 D2/D4 |
| R18 | **画面事件投递语义**：豁免主动冷却；LLM 在飞 → **丢弃 + log**（实时性优先，不等待/不排队）；**危险画面可抢占**非危险在飞回合（复用 `_do_interrupt`，**含用户标准回合在飞**）；在飞亦为危险 → 丢弃 + log 等下一轮；危险 + 说话中无 pipeline → 丢弃 + log | §4 D2 |
| R19 | **附帧静音自愈**（provider 层）：窗口无音频先补 **20ms 静音**、有则不补；monitor 取消 `_pipe_window_has_audio` 附帧拦截；`video_check_interval_s` 10s **保留**（"无冷却" = 发言冷却豁免，非每秒查询） | §4 D2 |
| R20 | **转写否决判定细化（[P6.1]）**：去标点/空白后**不同字符数 ≥2** 才算"在说话"——单字重复拟声（`咳咳。`）放行事件、正常词句仍否决。**[P6.13] 修订（2026-10-06，已实施）**：阈值 **≥2 → ≥5**（方案 A：与竞态门共享单点同改）——2–4 种字符的短转写放行事件/不再拦竞态，仅**成句级**（≥5 种字符）否决 | §4 D2 |
| R21 | **竞态复检去帧化（[P6.3]）**："在说话"只认文字证据（迟到 meaningful final / 触发后像话的 interim）；**语音帧快照不再单独拦**——连续咳嗽不再自杀式拦截，防抢话由既有 barge-in 链兜底 | §4 D2 |
| R22 | **两字拟声豁免（[P6.4]）**：`is_speech_like_text` 对常见非语音拟声（`啊嚏`/`哈哈`/`嗯嗯` 等，整串精确匹配）豁免——喷嚏不再被 veto/竞态门误拦；混合词句不豁免 | §4 D2 |
| R23 | **拒图停图限时（[P6.14] 拍板，2026-10-06，已实施）**：服务端拒图后的停图从"会话级永久"改为 **300s**（`image_disable_cooldown_s`，可配置）——到期后下一个视频检查自动恢复附帧；计时为会话内状态（重建清零）；固定间隔重试、无退避 | §4 D4 / §10 |

### 9.1 与 `fd_pipeline_omni_mode.md` §11-L2 的关系（本设计验收后另拍）

§11-L2（omni 转 manual 实现"无开口主动"）与本设计是**两个不同模式下的两条路**：
本设计=**chat 模式**的平行检测器（不动三段式、不动 omni 架构，风险面小）；
§11-L2 仍是 **omni 模式**自身的主动化议题（manual 全迁，需专项验收）。
两者共享同一批探针结论（`response.create` 起一轮、manual 可用），实施时互不阻塞。
若本设计落地且体验达标，可评估 §11-L2 是否降级为"omni 模式不做主动"（改由 chat 承担主动能力）——
**待本设计验收后单独拍板，不在本文实施范围**。

## 10. 实施记录（2026-10-03）

| # | 落地内容 |
|---|---|
| C1 | `configs/dev/prompts/avdetector.txt`（D6 定稿提示词：图像三标准 + 危险上报 + 优先级契约） |
| C2 | `shared/config/models.py`：`AvDetectorProviderConfig` / `AvDetectorConfig`（[R3]/[R6]/[R13]/[R16]/[R17] 拍板默认值）+ `AppConfig.avdetector`；`shared/utils/errors.py` 增 `AVDETECTOR_UNAVAILABLE=1010`（内部用，不下发客户端） |
| C3 | `configs/dev/server.yaml`：`avdetector:` 段（默认 `enabled: false`；provider 抄 omni 域名/头/模型） |
| C4/C5 | `services/avdetector/**`：`AvCheckResult`/协议（base）+ `AvDetectorService`（active+fallback）+ factory + `providers/alibaba_avdetector.py`（manual + text-only + `response.create`；`commit_audio()` 静默提交并等待窗口转写结算；`check()` 收满一轮返回 text/transcripts/usage；feed 失败重连重试；`reopen`/`close`） |
| C6 | `services/session/av_detector.py`：`AvDetectorMonitor`（★无阻塞 feed 有界队列丢最旧 / ★单管道串行 / ★静默 commit 窗口纪律 / 帧缓存 `deque(10)` 非 text / `check_min_interval`+`video_check_interval` 节流 / NONE/JSON/围栏解析 / 转写否决 / 静默降级 + 连续 3 次熔断 / 周期重建 / `on_event` 回调） |
| C7 | `orchestrator.py`：音频旁路 feed（`:1037` 相邻）；turn-end 空识别触发分支（[R4] ASR 健康 + VAD speech 窗口 + 冷却）；`_handle_video` 非 text 帧入缓存 + 视频触发（[R14] text 门控、LISTENING/无在飞门控、[R17] 10s 节流经 monitor）；`_on_av_event` 最终门控（[R15] `危险行为` 豁免冷却）+ `_build_av_event_note` 三种文案；`_start_thinking(event_note=)` 注记并入 system；生命周期（session.start 重建 / session.end / run() 收尾 / session.change 切出关、切回建） |
| C8/C9 | `application.py`（`cfg.avdetector.enabled` 门控创建 + 独立 prompt + `app.state.avdetector_service/cfg/system_prompt`）；`endpoint.py` kwarg 透传 |
| C10 | 测试：`tests/unit/avdetector/`（service/factory/provider 假 WS，20 例）；`tests/unit/session/test_av_monitor.py`（16 例：管道顺序/窗口纪律/**背压丢弃**/否决/冷却/熔断/重建）；`tests/unit/session/test_orchestrator_avdetector.py`（18 例：触发矩阵/text 帧零调用/事件注记/危险豁免/生命周期/**关闭态零差异**）；`tests/integration/agent_relay/test_application_wiring.py` +2（装配 + 关闭态） |
| C11 | 本文档状态与本节 |
| C12 | 探针 `services/scripts/probe_avdetector_check.py`（B/F/H 变体，此前已落地） |

**回归**：`tests/unit` **1228 passed / 0 failed / 1 skipped**（junitxml 计数，2026-10-03）；
`ruff check` 全部改动文件通过（`app/application.py:376` 的 I001 为改动前既有、未触碰）。
`pyproject.toml` per-file-ignores 按仓库"IO 边界吞错基线"惯例为 `avdetector/service.py`、
`avdetector/providers/alibaba_avdetector.py`、`session/av_detector.py` 登记 BLE001/S110。

**启用方式**：`configs/dev/server.yaml` `avdetector.enabled: true`（交付默认 false；同 omni 纪律）。

**review 修复（2026-10-03，实施后自查；均有测试锁定）**：

1. **竞态复检补齐**（§4 D2 三重门③此前漏实现）：触发受理时记录"最后语音帧"快照
   （`_av_trigger_speech_mono`）；`_on_av_event` 送达时若出现新语音帧、`_asr_interim_pending`
   或 `_asr_final_sent` → 丢弃主动回合（防检查往返期间用户开口被抢答）。
   （**帧快照条件已被 [P6.3] 移除**：连续咳嗽的后续 VAD 帧会自杀式拦截；现仅认文字证据，
   快照字段 `_av_trigger_speech_mono` 已删除。）
2. **"先音频后图"窗口竞态**：静默 commit / 重连产生空窗口时附图上送会撞服务端约束
   （`Error append image before append audio`）并把失败计进熔断——monitor 镜像窗口音频态
   （`_pipe_window_has_audio`，在 commit / reopen / 检查 / 降级处复位）决定是否附帧；
   provider `feed_image` 增加防御性跳过（重连等窗口态不一致的兜底，绝不产生 error 帧）。
   （**已被 [P6] 取代并移除**：约束改由 provider 静音自愈承担，mirror 附帧拦截与防御性跳过
   已在 [P6] 实施中删除。）
3. **坏图熔断防护**：服务端拒图（超 256KB/1080p 等）原会连续 3 次失败把整个检测器熔断
   （连带毁掉音频检测）→ 与 omni `_image_disabled` 同款**会话级停图**（`_images_disabled`），
   后续检查退化为纯音频、不熔断。**[P6.14] 修订（2026-10-06，已实施）**：停图改**限时
   300s**（`image_disable_cooldown_s`）——到期后下一个视频检查自动恢复附帧重试；会话内
   计时，重建清零。详见 §10 条目 26。
4. **上图失败不拖垮本轮**：feed_image 发送失败（已重连重试过）降级为纯音频检查，
   不中止本轮 check。

**待做（真机验收，§6.2）**：真人样本（[R5] 达标线）/ 说话无扰（转写否决）/ 画面主动（变化+危险）/
频率冷却 / 回声 / 降级（断网无感）/ 长会话。

**review 修复（第二轮，2026-10-03）**：

5. **[P1] 事件来源错位**：`kind`（"本次查询是否附帧"）被误当"回复主题"——有摄像头时咳嗽等
   声音事件的注记会写成"（来源：画面）……请点评画面"，与 desc 矛盾且音频检查不回带图。
   修复：注记不写死来源（"刚才检测到：{desc}。请自然地关心或回应"）；危险分支仍按
   `event=="危险行为"` 精确走紧迫提醒语气。
6. **[P2] 超时计熔断**：查询超时与连接错误原统一降级 + 计失败（3 次熔断），与 D5"超时只丢结果"
   矛盾（模型抖动会烧掉整会话检测）。修复：新增 `AvCheckTimeout`（provider 超时专用）——
   超时只丢结果、不计失败；**连续 2 次超时**才降级（防流僵死）；连接错误/error 帧维持直接降级。
7. **[P3] 冷却冻结检测触发**：触发层原带 `_av_proactive_cooldown_ok()`——主动发言后 30s 内
   完全不检测（含视频主通道），[R15] 危险豁免无从生效、"危险 ≤ 一个检测节流窗"不成立。
   修复：触发层去掉冷却门（检测照跑，成本可忽略）；冷却只在投递侧（`_on_av_event`）生效。

**review 修复（第三轮，2026-10-03）**：

8. **[P4] 超时残留响应串台**：P2 修复让"超时不再关流"，同时打开了新窗口——被放弃的上一轮
   response 若仍在生成，其 `response.text.delta` 会混入下一轮检查文本、其 `response.done`
   会**提前终止**收集并把陈旧结果当成新窗口结论（可能触发过期事件）。修复：①超时即
   best-effort `response.cancel` 掐掉在途生成；②`check()` 绑定本轮 `response.created` 的
   response_id，非本轮的 `response.*` 事件（delta/done）一律丢弃——乱序/迟到残响均不串台。

**review 修复（第四轮，2026-10-03 真机联调首日）**：

9. **图片上行可观测性**：`_send_feeding`（音频/图片 append）原无消息级日志——联调首日据
   "`send input_image_buffer.append` 计 0"**误判"图未上行"**（真实 provider 复刻探针证明链路
   正常）。修复：`feed_image` 补消息级日志；monitor"检查未带图"时输出原因 debug
   （video/frames/window_audio/images_disabled）。
10. **视频主通道独立节拍**：原实现两通道共享 `_last_check_at`——turn-end 音频检查每 ~10–15s
    把视频节拍重置，实测 90s 仅轮到 1 次视频检查，与 [R13]"有图时音频让位于视频节拍"**相反**。
    修复：`request_video_check` 改用独立 `_last_video_check_at`（只受自身节流）；受理时同时
    推高全局时间戳（音频补充自然让位）。

**真机联调首日实录（2026-10-03 晚，`avdetector.enabled=true`）**：主动回合 1 次
（13:17:03 触达 → 13:17:04.2 发起 → 13:17:06.6 首音频，端到端 ≈2.8s）；转写否决 1 次
（13:17:43，说话窗口正确拦截）；**竞态复检 1 次**（13:18:04，视频检查事件因用户开口被正确
丢弃——防抢答生效）；静默 commit 每 15s 节拍稳定；无超时/熔断/重连。

**review 修复（第五轮，2026-10-03 真机"咳嗽复读历史"事故）**：

11. **[P5] 主动回合以 assistant 尾条结尾 → 模型复读历史**：真机实证（两次 proactive 均复读——
    21:17 回复为历史尾条"美军汇报"的压缩复述；21:28 咳嗽后回复 = 10 分钟前 assistant 句的
    近乎逐字重复）——请求 messages 以 history 尾条（assistant）结尾，模型进入"续写上一条"
    模式，system 注记压不住。修复（拍板 1+2）：①事件**落 user 行三件一体**（`【环境事件】{desc}`，
    kind="asr" + `avd-` ref_id，视频事件带帧图；LLM 请求以 user 结尾 → 复读根治，且 chatlog
    自然不突兀、4xx 回滚句柄同 v3）；②注记收紧为强制回应口径（"请立即回应上一条事件；
    **不要复述、不要延续之前的对话内容**"）。另补日志可观测性：proactive / veto / race-recheck /
    冷却丢弃行均把 `label/desc` 写进**消息正文**（原 `extra=` 在本部署的日志渲染中不可见，
    导致事故复盘无法回看检测器实际报了什么）。**真机复测判据**：咳嗽 → 回复含咳嗽/关心语义
    且不复读历史。

**review 修复（第六轮，2026-10-03 拍板 · 待实施）**：

12. **[P6] 静默期附帧 + 画面/危险投递语义**：真机日志实证静默期（客户端 VAD 门控不发音频帧）
    视频检查全部无图运行（`check without frame … video=True frames=10 window_audio=False`），
    "安静时看画面变化主动开口"场景不可用。图片依赖探针（G1–G6，2026-10-03）：约束为**逐窗口**
    （"自上次提交须有 audio append"；纯图会话 G1 与提交后 G5 精确复现
    `Error append image before append audio`），**20ms 静音注入即可满足**（G2 零会话 / G6
    commit 后静默，均被接受；G3 对照正常）。**拍板语义**（R18/R19）：①provider 层**静音自愈**
    （有则补、有音频不补；monitor 取消附帧拦截与防御性跳过——静默期画面检测随之可用）；
    ②画面事件**豁免主动冷却**、"满足即发言"；③普通画面 LLM 在飞 → **丢弃 + log**（实时性优先，
    不等待、不排队，等下一轮检测）；④危险画面**可抢占**非危险在飞回合（`_do_interrupt` +
    立即报，含用户标准回合在飞），在飞亦为危险 → 丢弃 + log 等下一轮，危险 + 说话中无 pipeline
    → 丢弃 + log；⑤`video_check_interval_s`=10s **保留**（"无冷却"=发言冷却豁免；连续画面
    变化按 10s 节拍发言——防打扰节制仅剩检测节流 + "AI 说话中丢弃"）；⑥所有丢弃/抢占路径
    写 log（busy 丢弃 / preempt / 门控放弃按原因成行）。
    **状态：已实施（2026-10-03）**——provider 静音自愈 / monitor 解除附帧拦截 /
    orchestrator 投递语义（busy 丢弃+log、危险抢占、画面冷却豁免、`_pipeline_danger`
    标记及维护点）全部落地；静音自愈常数 640B/20ms；危险路径门控顺序见 §4 D2。
    **测试（已落地，+7 例）**：provider——静音自愈发送序列（无窗口音频 `[静音, 图]`、
    有音频 `[图]`，含"不重复补"断言）；monitor——无 `window_audio` 也附帧（旧拦截用例改写）；
    orchestrator——普通画面在飞→丢弃+零落库、冷却中仍发言（豁免断言）、危险**抢占**
    （`_do_interrupt("av_danger")` 被调 + 起轮）、危险在飞→丢弃、危险+说话中无在飞→丢弃、
    `_pipeline_danger` 置位/复位、声音通道回归。
    **真机复测判据（待验收）**：静默期画面变化 → `send input_image_buffer.append` 无错误且
    AI 点评；AI 说话中出现 `危险行为` 事件 → preempt 日志 + 危险提醒播报（可人工模拟）；
    连续画面变化按 10s 节拍发言、无 30s 冷却压制。
    **实施后 review 补（2026-10-03）**：①`feed_image` 重连重试改为**自带**且**重连后重新
    自愈**——原 `_send_feeding` 重试会在新会话（窗口已清零）上裸发图撞
    "append image before append audio"，error 帧 / 误降级路径被阻断（+1 测试锁定）；
    ②mode 门丢弃补 log（"门控放弃按原因成行"口径补齐）。

**review 修复（第七轮，2026-10-03 真机"咳嗽被拟声转写误杀"）**：

13. **[P6.1] 转写否决误杀真咳嗽**：真机实证（14:26:55）——咳嗽被检测器**正确识别**
    （`label=咳嗽声 desc='听到明显的咳嗽声'`），但检测流内建 ASR 将其转写为 `'咳咳。'`，
    命中"任何非空转写即否决"的粗规则 → 事件被误杀、AI 无反应（对照 13:38:53 同场景
    转写恰为空串 → 正常触发；表现为随机时好时坏）。附证：主 ASR 同样把咳嗽转成 `'咳。'`
    （1 个有效字符 < 2 阈值 → 不触发标准流程，正确；但非空 final 不走"空收尾"分支，
    `_asr_interim_pending` 需等下一空收尾才清——**竞态复检可能构成第二道拦截，待修复后
    真机验证**，若出现 `dropped by race recheck` 即证实）。修复（拍板"方案 2"）：veto 判定
    改为**去标点/空白后不同字符数 ≥2**——`咳咳。`（1 种）放行、`好的`/`你好…`（≥2 种）
    仍否决；纯函数参数化用例 9 组 + 交付链用例 1 组锁定。

    **第七轮 review 补（同日，[P6.2]）**：14. 两处纵深修复——①**竞态复检的 stale-flag 泄漏**：
    `_asr_interim_pending` 无时间戳，咳嗽自身的拟声 interim（触发**前**已挂起，主 ASR 非空
    final 不清它、要等下一空收尾）会在送达时被当作"触发以来的新活动"→ **确定性拦死**（veto
    修好也白修）。修复：触发受理时快照 `_av_trigger_interim_pending`，送达改为"**触发后新转真**
    才拦"（真实语音窗口由否决兜底）；②`_TRANSCRIPT_STRIP` 手写标点表不封闭（`'咳·'`/`'咳　'`
    等仍会被误否决）→ 标点剥离改用 `unicodedata` 按 Unicode 标点（P*）/空白类别。新增用例
    锁定（快照捕获 / stale 不拦 / 未见标点放行）。

**review 修复（第八轮，2026-10-03 真机"连续咳嗽自杀式拦截"；[P6.3] 已实施）**：

15. **[P6.3] 竞态复检去帧化**：真机实证（14:39:44）——咳嗽通过 veto 与 stale-interim 两闸后，
    仍被竞态复检丢弃（`dropped by race recheck label=咳嗽声 desc='听到有人连续咳嗽'`）。
    排除法逐项核对：窗口内无任何 ASR final 行、无新 interim 的 `send transcription` 行 ⇒
    唯一命中**"语音帧快照不一致"**——用户"连续咳嗽"的**下一声**（VAD=speech 帧）落进检查
    往返的 0.42s 窗口，被当成"用户开始说话"。这是三连（veto → stale interim → 帧快照）
    同一根因（咳嗽的声学痕迹被当说话）的最后一处。修复（拍板方案 1）："在说话"只认**文字
    证据**——① 触发后迟到 meaningful final；② 触发后**新出现且像话**的 interim（≥2 种字符，
    与 veto 共用 `is_speech_like_text`；新增跟踪 `_last_asr_interim_text`，poll 侧写入）。
    **帧快照条件与 `_av_trigger_speech_mono` 字段删除**；防抢话兜底 = 既有 barge-in 链
    （SPEAKING 中 `user_voice` 打断）+ ASR 回合送达即 cancel；race-drop 日志补
    `(final_sent=%s interim_speech=%s)` 原因位。新增/改写用例 5 个（快照仅 interim /
    像话 interim 拦 / 拟声 interim 不拦 / 帧独有不拦 / 危险竞态需文字证据）。

    **（同日 review 补，[P6.4]）**：16. **两字拟声豁免**——review 发现 `'啊嚏'`/`'阿嚏'`
    （喷嚏，产品目标场景之二）为 **2 种字符**、不被"单字重复"规则覆盖 → veto 与竞态门都会把
    喷嚏事件误拦（同根问题的残留变体）。修复：`is_speech_like_text` 增加常见非语音拟声
    **精确豁免白名单**（啊嚏/阿嚏/呵嚏/喷嚏/哈哈/呵呵/嘿嘿/嘻嘻/咳咳/嗯嗯/啊啊/呜呜；仅归一化
    整串精确匹配——混合真实词句如 `'啊嚏，不好意思'` 不豁免，说话误报兜底不退化）。参数化
    用例 +6（含 `'啊嚏，不好意思'`/`'哈喽'` 真词不豁免对照）。真机喷嚏复测待做。

**review 修复（第九轮，2026-10-03 全面自查：doc ↔ 实现逐条核对；[P6.5] 已实施）**：

17. **[P6.5] 生命周期"先关事件源"不变式**：四处收口（`run()` 收尾 / `session.start` /
    `session.end` / `session.change` 切出）原先把 `_close_av_monitor()` 放在 teardown 长
    await **之后**，逐个存在竞态窗口：①`session.start`——函数开头已复位 `_pipeline_mode`
    `"full"` + asr/state 门全开，`session_mgr.start` 等 await 期间旧 monitor 事件会起
    stale 主动回合（旧 ctx/历史混入新会话）；②`session.change(omni)`——`_enter_omni_mode`
    开流（秒级）期间 `_pipeline_mode` 仍 `"full"` 且状态被压回 LISTENING，事件会在切换
    窗口起 chat 回合与 omni 引擎并存；③`session.end` / `run()` 收尾——monitor 存活于
    `_cancel_pipeline` 之后的长 await（close gather 可秒级），事件会起一条无人取消的新
    pipeline。修复：四处一律**先 close monitor、再做其余 teardown**；切 omni 失败
    （1009 回滚、App 留在 chat）时恢复已关事件源（否则 chat 下检测静默死亡）。另清理
    [P6] 遗留死写：`_pipe_window_has_audio` 的唯一剩余写点（monitor `_run_check` 外层
    try/finally，字段已删、永不被读）删除。测试 +4（omni 关序 / omni 失败恢复 /
    session.end 关序 / session.start 关序）；`_wait_until` 预算 5s→10s（重负载 + uvicorn
    热重载并发下实测耗尽一次——测试等待，非语义）。**回归**：`tests/unit` **1277 passed /
    0 failed / 1 skipped**；`ruff check` 通过。

**review 修复（第十轮，2026-10-03 用户 review"高危 1"；[P6.6] 已实施）**：

18. **[P6.6] 危险抢占路径双 pipeline 竞态**：用户 review 指出——`_on_av_event` 的 busy
    分支抢占时 `await self._do_interrupt("av_danger")` 含**多个 await**（cancel pipeline、
    ASR/TTS reset、status 帧），整个窗口让出事件循环；dispatch 主循环若在此期间受理
    用户 turn-end（`:1400`，先 cancel 再建、自身无竞态）或 v3 文本回合（`:2054` 同款），
    会建一条新 pipeline。`_do_interrupt` 返回后代码直落 `self._pipeline_task =
    create_task(...)`（危险回合）**覆写引用**——插队回合成为**无人取消的孤儿**：双 LLM 流、
    共享 TTS 流交错、双 status(thinking) 下行，且两任务快照都合法（均在 interrupt 后抓取），
    should_yield 救不了；后续任何 interrupt/收口都无法再取消它。概率低（需危险事件 + 用户
    turn-end 落进 interrupt 的毫秒级窗口），后果严重（双重播报、引用泄漏）。**修复**（拍板
    方案 1"补刀取消"，弃"重查 busy 重走门控"——后者遇新用户回合会二次抢占循环）：`_do_interrupt`
    返回后、置位前补一次 `await self._cancel_pipeline()`（幂等、无在飞时零成本）——中断窗口内
    任何插队 pipeline 被取消；补刀点到 `create_task` 之间**零 await**（全 sync），单线程
    事件循环下引用写入原子。语义与 [P6] 一致：插队的用户标准回合视同"在飞非危险回合"被
    危险抢占（安全优先）。产出：**单测验证先失败后通过**（临时移除补刀复现失败）——
    模拟 interrupt 窗口内插队建轮（断言其 `cancelled()`、引用未被孤儿覆写、`_pipeline_danger`
    归危险回合）。**回归**：`tests/unit` **1278 passed / 0 failed / 1 skipped**；`ruff check`
    通过。

19. **[P6.7] 静默 commit 发送失败的 stale 窗口**（同批用户 review"高危 2"）：`commit_audio`
    原实现"先置 `_window_has_audio=False`，发送失败仅 warning + return"——注释声称
    "窗口音频随重连丢失是既有语义"，但该分支**根本没有重连**：连接未死（瞬时发送失败）时
    服务端仍持有未提交窗口，下一次 check 的 `response.create` 会把它一并提交 → 陈旧转写
    误触发否决 / 陈旧音频产生过期事件——正是 D5★ 问题②（"说话后未提交音频混入后续检查
    窗口"）的残留路径复活。概率低（send 失败通常意味着连接已死，下一帧 feed 会重连兜底；
    dead 情形由 `_dead` → check 抛错 → 降级自愈），但语义洞客观存在。**修复**（用户拍板
    方案 1，弃"仅标 `_dead`"——那让陈旧窗口在服务端存活到下次 check 才被动清理）：
    发送失败走一次 `_reconnect()`（与 `_send_feeding`/`feed_image` 同款）——重连 = 全新
    会话、陈旧窗口随旧连接销毁，"窗口音频丢失"语义成立；**不重发**（新会话窗口为空，
    commit 的划界目的已达成，空缓冲提交行为亦未验证）；重连失败 → 抛 `ProviderError`
    （`_dead` 已置）由 monitor `_pipeline` 兜底降级。产出：+2 单测（重连成功：旧连接已关、
    新会话零 commit、旧残留转写被 drain；重连失败：抛错 + `_dead`），**先失败后通过验证**
    （临时还原"仅告警"版两例均 FAILED）。**回归**：`tests/unit` **1280 passed / 0 failed /
    1 skipped**；`ruff check` 通过。

20. **[P6.8] 反向插队竞态（对称变体，用户拍板"要修"）**：[P6.6] 修的是"av 事件覆写
    dispatch 侧新轮"，反向同样存在——**dispatch 侧创建点**（用户 turn-end `:1400` 的
    barge-in `_do_interrupt("user_voice")`、v3 文本回合 `:2054` 的条件 `_do_interrupt
    ("user_text")`）各自的 interrupt 窗口内，av 危险/画面事件（monitor 任务）装入
    pipeline 并通过其门控（此时 `_pipeline_task` 已被 interrupt 置 None、状态已回
    LISTENING、`_asr_final_sent` 已复位）→ dispatch 侧恢复后 `create_task` 覆写引用
    → **av 回合成孤儿**（双 LLM/TTS、过后无人能取消；危险回合被吞掉更严重）。
    任何"await cancel/interrupt → sync create"的写法都留有这一窗口（补一刀 await 反而
    引入下一个同类窗口——乌龟递归）。**修复**：新增 `_spawn_pipeline(coro, *, danger)`
    换装原语——读引用 →（`old.cancel()` **同步**）→ 写 `_pipeline_danger` → `create_task`，
    **函数体零 await**＝写入原子；cancel 必于孤儿任务下一个 await 点生效（任务被装入后
    至多跑到第一个 await，无实质行为窗口），不 await 其清理（await 会重新引入窗口）。
    `_on_av_event` / turn-end / v3 文本三处创建点全部改走该原语（[P6.6] 的 awaited
    补刀保留：正常路径的"干净清理后再起轮"语义不变）。产出：+3 单测（原语直测 /
    turn-end barge-in 插队 / v3 文本条件中断插队；均断言插队任务 `cancelled()` 且引用
    未被孤儿覆写），**先失败后通过验证**（临时改回盲覆写三例均 FAILED）。**回归**：
    `tests/unit` **1283 passed / 0 failed / 1 skipped**；`ruff check` 通过。

21. **[P6.9] 音频通道附帧的投递语义缺口**（同批用户 review"中危 3"，拍板**方案 A**）：
    [R13] 统一查询契约下音频补充检查同样附最新帧（`_run_check`：`_frames[-1]` 不分通道），
    但回调传帧是 `frame if video else None`（旧 `:418`）——**中间态双输**：①成本——每次
    音频检查多付 ≈400 画面 token（≈610 vs ≈188，×3）；②回复——按提示词"有图画面为主"，
    咳嗽等声音事件的回复大概率变成画面描述；③投递——`frame=None` → `is_image=False` →
    不豁免 30s 冷却（咳嗽检查白送 token 还可能在冷却窗被吞）、final `ai_text` 不回带图；
    ④日志——`kind="画面"`（本地 frame 非 None 推导）与按声音投递自相矛盾。**拍板方案 A**
    （弃 B"音频画面事件按画面投递"——模糊两通道发言节制分工）：**音频补充检查纯声音、
    不再附帧**（回归 R13"音频补充"定位），画面变化完全归 10s 视频主通道（节流 + 检测不受
    冷却影响）；**提示词统一契约保留**（视频检查仍音画同查——D6 原文未动）。实现：
    `_run_check` 附帧条件加 `video` 门 + 回调简化为 `cb(kind, …, frame)`（frame 非 None
    现仅视频通道，`kind` 推导随之自洽；视频检查的静音自愈/拒图熔断逻辑不变）。产出：
    改写 1 单测（音频检查有缓存帧也**零上图**；原"音频检查附最新帧"用例随契约作废），
    **先失败后通过验证**（临时还原统一附帧 → FAILED）。**回归**：`tests/unit` **1283
    passed / 0 failed / 1 skipped**；`ruff check` 通过。

22. **[P6.10] 危险判定依赖中文精确匹配**（同批用户 review"中危 4"）：`is_danger = label
    == "危险行为"`（含 D3 语气文案同款比较）是 R15/P6 全部安全设计的**单点依赖**；而
    §3.5 F 电池实测检测器偶发英文/变体标签（英文率 12.5%——cough/Sneeze/laughter），
    解析层按"容忍中英"透传——危险判定一旦遇到 `Danger`/`Dangerous behavior` 变体，
    冷却豁免（[R15]）/危险抢占（[P6]）/紧迫语气（D3）**三重静默失效**，退化为普通画面
    事件被 30s 冷却吞掉。**修复**：判定收口为纯函数 `is_danger_label(label)`（`session/
    av_detector.py`，与 `is_speech_like_text` 同处）——`strip + lower + 核心词子串`
    （`"危险"`/`"danger"`），刻意取宽容口径（召回优先：假阳性=多一次紧迫提示）；
    orchestrator 两处（`is_danger` 判定 / `_build_av_event_note` 语气）共用此单一来源，
    解析层与判定层不再各写各的。产出：+18 单测（9 命中参数：`危险行为`/`危险`/`danger`/
    `Dangerous behavior`/`DANGEROUS BEHAVIOR DETECTED` 等；8 未命中参数：`画面变化`/
    `cough`/`Sneeze` 等；orchestrator 级英文变体语义锁：冷却豁免 + 紧迫语气 +
    `_pipeline_danger` 置位），**先失败后通过验证**（临时还原精确匹配 → 9 例 FAILED）。
    **回归**：`tests/unit` **1301 passed / 0 failed / 1 skipped**；`ruff check` 通过。

23. **[P6.11] 熔断计数跨成功开流累计**（同批用户 review"中危 5"）：`_ensure_stream` 开流
    成功只清 `_open_fail_time`/`_consecutive_timeouts`，**不清 `_consecutive_failures`**
    （只有成功 check 才清）——序列：open 成功 → feed 失败 → degrade(1) → 冷却 → open 成功
    （旧值保留）→ 再一次失败即逼近熔断。即"连续 3 次失败"（[R10]）被**跨成功开流累计**：
    长会话中三次互不相邻的瞬时故障（各自随后开流成功、但期间无成功查询——如摄像头关闭、
    ASR 正常的通话里检查不触发）→ 误熔断、检测对余下会话静默关闭。开流成功本身是
    "连接已恢复"的证据，语义上应清零。**修复**（用户方案）：`_ensure_stream` 成功路径补
    `_consecutive_failures = 0`——计数语义改为"自上次**成功开流/成功检查**以来的连续失败"
    （与 `_consecutive_timeouts` 开流清零的既有口径对齐）。**已知边界（接受并记录）**：
    恒开流成功但恒使用失败的病态服务不再熔断——重连受 5s 冷却节制；服务不可达类故障
    （开流失败）无成功可清零，仍按 3 次熔断。产出：+2 单测（成功开流清零直测；用户序列
    端到端：feed 失败→降级(1)→重开成功→再失败仍计 1 不累计），**先失败后通过验证**
    （临时移除清零行两例均 FAILED）。
    **同批附带修复**：`tests/unit/shared/test_chat_store.py` 两例时区日窗口测试的跨午夜
    缺陷（与 avdetector 无关、本日 00:02 实测暴露：原用 UTC 日推"上海今天"，UTC≥16:00
    （沪已跨日）后恒失败，每晚 CST 凌晨必红）——改为相对**各时区自己的**本地日 00:00
    ±1min 构造（+1min 属今天、-1min 属昨天），跨墙钟时刻稳定；断言改为无歧义成员断言。
    **回归**：`tests/unit` **1303 passed / 0 failed / 1 skipped**；`ruff check` 通过
    （test_chat_store 的 C408 两处为改动前既有、未触碰）。

24. **[P6.12] `session.start` 不取消在飞 pipeline——跨会话残余泄漏**（[P6.11] 后全面
    review 发现；与本批 [P6.5]/[P6.8] 同属"边界不干净"类，修复方式同款）：gateway
    明确允许同一连接重发 `session.start`（连接层 "Always accept session.start"），函数
    重置了全部 per-turn **状态**（`_asr_final_sent`/`_turn_image_frames`/回滚句柄……）
    却未清在飞**任务**。旧 `_start_thinking` 任务对 `self._ctx`/`self._history` 是**实时**
    引用——边界后其残余（TTS 尾音经 `_send_audio_chunk`、final ai_text 落库、
    `transition_or_reject`/status 帧）会直接写进**新会话**；epoch 兜底不完全：
    `should_yield` 比较 `self._ctx.epoch`（新 ctx 常为 0）与快照（旧 ctx 无中断时也为 0）
    ——同值判不出换代，任务继续跑。修复：`_handle_session_start` close monitor 后补
    `await self._cancel_pipeline()`（幂等、无在飞零成本；与同函数"新会话不得继承上一会话
    状态"的既有复位注释同款纪律；session.end/run() 收尾本就有 cancel）。产出 +1 单测
    （旧任务 `cancelled()` + 引用清空），**先失败后通过验证**（临时移除 cancel → FAILED）。
    **回归**：`tests/unit` **1304 passed / 0 failed / 1 skipped**；`ruff check` 通过。

**修订（第十一轮，2026-10-06 用户 review 拍板方案 A；[P6.13] 已实施）**：

25. **[P6.13] 转写否决/竞态复检阈值 ≥2 → ≥5**（调参拍板：阈值 2 对 2–4 种字符的短转写过严
    ——`'好的'`/`'OK'`/`'哈喽'` 及白名单外短拟声会压掉窗口内的真事件；本次按调参诉求直接
    拍板 5，无附带真机日志，真机验收判据见下）。方案 A：两闸共享的
    `_VETO_MIN_DISTINCT_CHARS` 2 → **5**（维持 [P6.3] "同一判定"纪律、单点维护）；
    `_NON_SPEECH_ONOMATOPOEIA` **保留**——阈值 5 下现有条目已冗余（均 ≤2 种字符），
    留作显式语义与阈值回降防护。
    边界矩阵（改后）：≥5 种字符 → 否决/拦（不变，`'今天天气不错'`=5）；**2–4 种 → 放行/
    不再拦（变化**：`'好的'`2、`'OK'`2、`'咳咳，你好'`3、`'hello'`4）；≤1 种/拟声白名单
    → 放行（不变）。
    **接受的退化与兜底**：①短句期间声音类事件投递增多——仍受 30s 主动冷却（[R3]）压制；
    ②**画面事件豁免冷却**（[R18]）→ 短句期间画面点评可投递（最可见变化）；③（A 下）[P6]
    "危险 + 说话中 → 丢弃"对 2–4 种字符的 interim 说话证据失效（迟到 final 路径不变）——
    危险会在用户短句期间投递（安全优先方向）；
    ④用户真开口最终兜底 = barge-in 链（SPEAKING `user_voice` 打断）+ ASR 回合送达即 cancel
    （[P6.3] 兜底不变）。
    实施（2026-10-06）：`av_detector.py` `_VETO_MIN_DISTINCT_CHARS` 2 → 5 + 注释同步三处
    （常量说明块/否决内联注释/白名单冗余注记）；`orchestrator.py` 竞态复检注释同步；
    测试——`test_av_monitor.py` 参数表 4 例翻转（`好的`/`咳咳，你好`/`OK`/`哈喽` True→False）
    + 边界对（`一二三四`False / `一二三四五`True）；`test_orchestrator_avdetector.py` 两处
    竞态范例换 `我今天有点困` + 新增"2–4 种字符 interim 不再拦"1 例。**先失败后通过验证**：
    改码前跑两测试文件恰 6 例 FAILED（4 翻转 + 边界 + 新不拦例），改码后 106/106 通过。
    **回归**：`tests/unit` **1307 passed / 0 failed / 1 skipped**（+3：1 新例 + 2 新参数）；
    `ruff check` 四个改动文件通过。
    **真机验收判据（待真机）**：短句（`好的`）窗口内的咳嗽/画面事件不再出现
    `vetoed by speech transcript` / `dropped by race recheck` 日志。

**修订（第十二轮，2026-10-06 用户 review 拍板；[P6.14] 已实施）**：

26. **[P6.14] 拒图停图限时（会话内 300s 到期恢复）**：动机——服务端拒图（超 256KB/1080p
    等，check 异常含 "image" 且本轮附帧触发）后的停图原为**会话级永久**（`_images_disabled`
    bool）：一次坏图让画面检测整个会话不可用——保音频的目标达成，但画面能力白白损失；
    用户拍板"不要一直停掉 image 检测"——停 300s 后自动恢复重试。
    方案：`_images_disabled`（bool）→ `_images_disabled_until: float`（monotonic 截止，
    0=未停图）；拒图时置 `now + image_disable_cooldown_s`（**每次拒图重新计时**）；
    **惰性恢复**（不引入定时器）——附帧选择处判定：未到期 → 纯音频检查（debug 日志带
    剩余秒数）；已到期 → 清零 + info 恢复日志 + 正常附帧。恢复时点 = 到期后**下一个**
    视频检查（不早于 300s，受 10s 节拍与帧到达约束）。
    配置：`AvDetectorConfig.image_disable_cooldown_s: float = 300.0`（§4 D4 模型块已列）+
    `configs/dev/server.yaml` 同步；monitor 按既有 `getattr(cfg, …, 300.0)` 风格读取
    （旧 yaml 不更新走默认，向后兼容）。
    **熔断安全性**（关键分析）：停图期内纯音频检查成功即清零失败计数；到期重试再拒 → +1
    ——计数只在 1↔0 间振荡，**永不累计到 3 → 不熔断**（[R10] 目标保持：坏图不熔断、音频
    不受连累）；与图无关的纯音频故障照旧熔断（纪律不变）。
    边界（本次拍板接受）：①到期重试附缓存尾=**最新**帧（视频检查由新帧到达触发，
    `feed_frame` 先于 `request_video_check`，不重发被拒旧图）——新帧仍被拒（如相机持续
    产出超限帧）→ 再拒再停 300s；
    ②chat↔translate 模式切换重建 monitor → 停图计时清零（沿用现有重建语义，不做跨重建
    持久）；③固定间隔重试、无指数退避；④触发启发式不变。
    **非目标**：omni/livetranslate 同款 `_image_disabled`（bool 永久）本次不改（如需另提）。
    实施（2026-10-06）：`av_detector.py` 状态字段改 `_images_disabled_until`（monotonic
    截止）+ 配置读取（getattr 默认 300）+ 拒图处置时限 + 附帧选择处惰性恢复（含剩余秒数
    debug / 恢复 info 日志）；`shared/config/models.py` + `configs/dev/server.yaml` 增
    `image_disable_cooldown_s`。测试——改写 `test_image_rejection_disables_images_not_fuse`
    （停图 0.2s：期内纯音频不上图 + 到期恢复附帧且时限清零）+ 新增
    `test_image_disable_retry_rejected_retimes_not_fuse`（到期重试再拒 → 重新计时、
    不熔断）。**先失败后通过验证**：改码前跑本文件恰 2 例 FAILED（改写 + 新增），
    改码后 63/63 通过。
    **回归**：`tests/unit` **1308 passed / 0 failed / 1 skipped**（+1 新例）；`ruff check`
    三个改动文件通过。
    **真机验收判据（待真机）**：真机触发拒图（可用超限大图）→ 日志 "disabled … 300s"；
    到期后视频检查恢复 `input_image_buffer.append` 且成功不复发。

**修订（第十三轮，2026-10-09 用户拍板；已实施）**：

27. **[R16] 修订：帧缓存 10 帧 → K=1 最新帧槽（多帧 history 删除）**：动机——核实
    `AvDetectorMonitor._frames` 的**全部读取点**只有 `has_frames`（真假性）与 `_frames[-1]`
    （最新帧；`:377/:393`），10 帧历史自落地起**从未被读**（多帧同窗在 §3.5 U 定向已判
    "语义不稳"、从未启用）；环形缓存的实际职能只有"最新帧持有器（异步解耦：
    `request_video_check` 投递空命令、检查处理时取帧）+ 存在性门"。
    方案：`_frames: deque(maxlen=frame_cache_size)` → **K=1 帧槽**（单槽覆写；
    `feed_frame` 写槽、`has_frames` 读槽非空、`_run_check` 取槽）——**异步语义不变**
    （检查仍取"处理时刻的最新帧"，非触发时刻帧）；`frame_cache_size` 配置删除
    （`shared/config/models.py` + `configs/dev/server.yaml`）。
    不变项：text 帧不入槽（[R14]）、任何状态都入槽、检查上行单帧、音频补充检查不附帧
    （[P6.9]）、拒图停图行为（[P6.14]）。
    放弃项：多帧回溯/对照（[R16] 原预留，从未启用——如重启需重新加缓存 + 探针复验）。
    收益：停帧后不再滞留 10 帧 jpeg（K=1 单帧），状态字段与配置各少一项。
    测试影响（实施时）：`test_av_monitor.py::test_frames_cache_maxlen` 删除；
    `test_video_check_requires_frames` 改写为"槽空不触发"；orchestrator 侧 `feed_frame`
    mock 断言不变（接口签名不变）。
    实施（2026-10-09）：`av_detector.py`——`_frames: deque` → `_latest_frame: tuple | None`
    单槽（`feed_frame` 覆写 / `has_frames` 读槽非空 / `request_video_check` 槽空短路 /
    `_run_check` 取槽；"无帧" debug 日志改 `(empty slot)`；删 `deque` import）；
    `shared/config/models.py` + `configs/dev/server.yaml` 删 `frame_cache_size`。测试——
    删 `test_frames_cache_maxlen`；`test_video_check_requires_frames` 改写为
    `test_video_check_requires_frame_slot`（槽空不触发 + 覆写后只上行最新一帧）；`_cfg`
    辅助删该字段。orchestrator 侧零改动（接口签名不变）。
    **回归**：services 全量（`tests/`）**1513 passed / 0 failed / 1 skipped**
    （97 deselected）；`ruff check` 三个改动文件通过。

**修订（第十四轮，2026-10-09 用户 review 拍板；已实施）**：

28. **附帧超限防护：`image_resize()` 上行副本重压（修复 1009 崩流）**：动机——真机
    2026-10-09 09:01:43Z 会话 `avdetector receive loop crashed`：对端 close
    `1009 Max frame length of 262144 has been exceeded.`（该文案不在本机 websockets
    17.1 中；本机协议层收到 close 后按 RFC **原样回显对端 close 帧**——`protocol.py`
    "Echo the original data"——故 rcvd/sent 同文案，实为对端生成）。该限为
    **DashScope realtime 网关的 WS 单帧上限 262144B（=256KB）**，与 §2.2 官方图片
    约束"base64 ≤256KB"同源；**超限帧不进模型**，网关在传输层直接关连接。失败经
    `check()` 以"连接丢失"（文案不含 `image`）形态返回 → **不触发 [P6.14] 停图冷却**
    （[P6.14] 只认含 "image" 的 check 异常）→ degrade 后重开；相机持续产出 >~191KB
    jpeg（1280×960 常见）时呈"崩→5s 冷却→重开→再崩"循环，画面检测实际失效
    （[P6.11] 成功开流清零计数避免了熔断，但检测仍被反复中断）。
    **探针实测（2026-10-09，一次性只读探针 `/tmp/probe_oversize.py`，产物
    `/tmp/lvdet_probe/oversize_probe.json`；四用例各自独立会话）**：

    | 用例 | WS 帧长 | 结果 |
    |---|---|---|
    | 有效小图（对照） | 9,436B | 正常，回复 NONE |
    | 限额内垃圾图 | 262,068B | 传输层**接受**（内容非法 → 应用层 error 帧 + 1007 close） |
    | 越限垃圾图 | 263,068B | **复现对端 1009**："Max frame length of 262144 has been exceeded." |
    | 远超限垃圾图 | 600,068B | 同上 1009 |

    → 边界为**序列化后整帧长度 262144B**（含 JSON 开销 ~70B 与 base64 膨胀 4/3；
    实测 262,068 接受 / 263,068 拒绝）。

    **方案（2026-10-09 用户拍板；同日 review 修订：函数契约收敛为"单档纯缩放"、
    两档阶梯归 provider——消除"阶梯在函数内 vs 调用方"与"解码失败/超预算同为
    `None`"两处歧义，后者混淆会使 review 要求的失败日志无法区分原因）**：
    - 新增公共函数 `services/media/jpeg.py::image_resize(jpeg, *, max_edge,
      max_bytes=None, quality=80) -> bytes | None`：**单档**——解码/编码失败 →
      **抛异常**（调用方处置）；最长边缩至 ≤max_edge（只缩不放、保持宽高比、
      LANCZOS，沿用 `to_data_url` 既有惯例）→ JPEG 重编码；`max_bytes` 非空且
      结果超预算 → `None`（"该档不可达"，非异常；`max_bytes=None` = 纯缩放语义）。
    - provider `alibaba_avdetector.feed_image`：payload 构造后测**序列化帧长**，
      超发送阈值 `_SEND_LIMIT = _MAX_WS_FRAME_BYTES − 1KB`（`_MAX_WS_FRAME_BYTES
      = 262144`，注释引探针出处）= **261,120B** → 按 **`_RESIZE_EDGE_LADDER =
      (960, 640)` 最多两档**依次 `await asyncio.to_thread(image_resize, ...)`
      （**不阻塞事件循环**；`max_bytes` = `_SEND_LIMIT` − 实际 JSON 开销，按
      base64 4/3 精确反推），首个达标者重建 payload 采用；**两档皆 `None` 或重压
      抛异常** → 抛 ProviderError（消息含尺寸与原因；**feed_image 异常路径不按
      消息内容分流**，monitor 直接 frame=None 降级纯音频检查）——**monitor 零
      改动**；重连重试复用已压 payload；重压成功属常态路径，**不触发 [P6.14]
      冷却**。
    - **可观测性（2026-10-09 用户 review 补：重压失败或仍超限必须留痕，供日后
      debug）**：provider 在重压路径落日志——重压成功 → `debug` 一行（输入像素
      尺寸 + 原/新字节数 + 档位 + 新帧长，样例 `avdetector image resized for
      upload: 1280x960 jpeg 1102685B -> 131651B, frame 175607B (edge 640)`）；
      **重压异常（解码失败等）或两档后仍超预算** → `warning`
      一行（含原因与相关尺寸，样例 `avdetector image resize unusable (decode
      failed): jpeg 210KB frame 280KB`、`avdetector image still over frame limit
      after 2 attempts: frame 271000B > 261120B`），随后抛 ProviderError（消息内
      含尺寸）；monitor 既有"上图失败 → 纯音频"warning 照旧。
    - **不变项**：回调 `frame`（final ai_text 回图）保持**原图**——客户端所见不变、
      模型见 720p 级画面（吻合官方"建议 480p/720p"）；未超限帧零重编码；音频通道 /
      [P6.9] / [P6.14] 行为不变。
    - **已知边界（接受）**：两档重压仍超 → 该轮纯音频检查（安全兜底，保证任何病理
      输入打不穿连接）；**同网关其他图片上行方**——① `alibaba_omni.py`：**已实施同款防护
      （2026-10-09）**——`feed_image` 前置两档重压，失败/仍超**跳过本帧不抛错**（引擎对
      `feed_image` 异常会 `_drop_stream` 关整个 omni 流）；修订记录见
      `fd_pipeline_omni_mode.md` 文首与 §4 D6；② `alibaba_livetranslate.py::_send_image_item`
      同款实现但**当前无调用方**（lecture 链路不投喂图像，dead path；未来接入须先套
      `image_resize`）。
    - **同日另一失败形态（2026-10-09 review 顺带核实，非本条目范围）**：
      `receive loop crashed` 另有 2 例（07:59/08:07）并非网络抖动——实为服务端
      **300s 无输入空闲回收**（1007，文案见 §2.2 增补）——非故障、由降级/懒重开
      自愈；判读崩溃日志先看 close 文案再归因。
    - **测试计划（实施时）**：`tests/unit/media/`（+`image_resize` 单档矩阵：缩放比/
      宽高比/只缩不放/超 `max_bytes` → None/解码失败 → 抛异常）；
      `tests/unit/avdetector/` provider（超限 → 发送帧 ≤`_SEND_LIMIT` 且可解码、
      长边 ≤960，含"需 640 档才达标"情形；未超限 → payload 字节不变零重编码；
      两档皆 None/重压异常 → 不发送不重连；均**先失败后通过**验证）。
    实施（2026-10-09）：`media/jpeg.py` 增 `image_resize`（单档，懒解码提前
    `img.load()` 使坏图按契约抛出）；`alibaba_avdetector.py` 增三常量 +
    `feed_image` 预检 + `_resize_image_frame`（两档 `asyncio.to_thread` 重压；预算
    `3*((_SEND_LIMIT − overhead)//4)`；重压失败/仍超抛 ProviderError——**全部发生在
    prime/发送之前，不进重连重试路径**，防把超限原帧误当发送失败重发）。日志样例按
    实现微调（成功行含输入像素尺寸 + 原/新字节数 + 档位 + 新帧长）。测试——media
    `TestImageResize` 6 例 + provider `TestFeedImageResize` 6 例（常量锁 / 真实噪声
    链路 960→640 / 未超限零重编码 / 两档失败与解码失败不发送不重连），**先失败后
    通过验证**（改码前两测试文件 ImportError 红）。**回归**：services 全量（`tests/`）
    **1525 passed / 0 failed / 1 skipped**（97 deselected；较实施前 1513 +12）；
    `ruff check` 四个改动文件通过。**端到端验证（2026-10-09，`/tmp/probe_resize_fix.py`，
    真实 DashScope 网关）**：1102685B 噪声 jpeg（b64 1.47MB，远超限）→ 重压
    （960 档不可达 → 640 档）→ 发送帧 175607B → check 正常返回 NONE，**不再 1009
    崩流**（修复前同路径即真机 receive loop crashed）。

29. **视觉回合抑制窗（D8，2026-10-22 已实施）**：动机——真机一条 cam 首帧 video message
    得到两条同场景回复（12:22:31 首帧直起轮回复 → 12:22:36 会话首次带图检查把同一桌面
    报为"画面变化" → 12:22:39 第二条）。实施——`AvDetectorConfig.visual_turn_suppress_s=5.0`
    （dev/test YAML 同步）；`DialogOrchestrator` 新增 `_av_visual_suppress_until`
    （session.start 复位）与 `_arm_av_visual_suppression()`；`_start_thinking(visual_turn=True)`
    在未中断 LISTENING 收尾开窗并强制 `request_video_check(force=True)`（5s 窗 < 10s
    节流，不能依赖窗口内帧触发）；`_on_av_event` 在冷却门之前丢弃窗口内 `kind="画面"`
    且非危险的事件（零落库、不更新 `_last_av_proactive`）；cam 分支的 turn_id 变化首帧
    传 `visual_turn=True`（同 turn_id 后续帧不算首帧；切镜头/重开各自开窗）。
    `AvDetectorMonitor.request_video_check` 新增 `force=False`：绕过节流，ready + 帧槽
    非空仍必需，两个检查时间戳照常更新。测试——monitor 强制检查 1 例；orchestrator D8
    门控/开窗/复位 10 例；integration cam 开窗/非视觉回合 2 例。回归 `tests/unit` +
    `tests/integration/orchestrator`：**1558 passed / 0 failed / 1 skipped**；
    `ruff check` 六个改动 Python 文件通过。真机验收待跑（§6.2-8）。

## 附录 A：探针复现与原始产物

```bash
# 2026-10-03 增量探针（本文主要依据；已入库，[R11]）
cd services && set -a && . ../.env && set +a
./.venv/bin/python scripts/probe_avdetector_check.py --variants N1,P1,P2,M,S,H
# 产物：/tmp/lvdet_probe/{report.json, gray.jpg, robot.jpg}
# 样本依赖：/tmp/lv3d_robot/robot_preview.png（机器人图）、~/projects/asr_test1.wav（真人语音）

# 2026-10-02 机制探针（fd_pipeline_omni_mode.md §11.2）
./.venv/bin/python /tmp/lvomni_probe/probe.py --variants A,C,C2,D,E
# 产物：/tmp/lvomni_probe/{cough.wav, probe_image.jpg, report.json}
```

**限制与诚实声明**：合成咳嗽≠真人咳嗽（识别质量待真人样本验收）；S 变体 3 次误报样本量小
（方向明确、比例不做统计断言）；单会话多轮上限（100 轮）未压测，靠周期重建规避。
