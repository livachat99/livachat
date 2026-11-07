# LivaChat Data Analysis Plan

> **For Hermes:** Execute tasks sequentially; each task builds on prior results.

**Goal:** 对 /opt/lvdata/ 下的三组 multimodal session 数据（音频 + 截图 + 聊天记录）进行全量处理、分析、洞察提取。

**Architecture:** Pipeline 模式：数据盘点 → 音频转写 → 图像分析 → 多模态对齐 → 主题挖掘 → 综合报告。

**Tech Stack:** ffmpeg (音频转换), Whisper (ASR), vision_analyze (图像), Python (数据处理/对齐/统计)

**Data Inventory:**

| Session | Date | Status | Audio (segments) | Video (frames) | Chat (msgs) | Total Size |
|---------|------|--------|-------------------|----------------|-------------|------------|
| 019f92f2 | 7/24 | finalized | 6 | 7 | 8 | ~1.5 MB |
| 019fa7d3 | 7/28 | finalized | 40 | 0 | 9 | ~10 MB |
| 019fabe0 | 7/29 | active | 13 | 23 | 8 | ~5 MB |

**Data Specs:**
- Audio: OGG Opus, 48kHz → original input 16kHz, mono, 60-sec segments (~243KB/ea)
- Video: JPEG screenshots (desktop captures, ~20-55KB/ea)
- Chat: JSONL, timestamped user↔assistant turns
- Metadata: session.json with config/stats/timestamps

---

## Phase 1: 环境准备与数据盘点

### Task 1.1: 检查可用工具链

**Objective:** 确认 ffmpeg, ffprobe, whisper 及 Python 依赖是否可用

**Files:** None (read-only check)

**Step 1: 检查 ffmpeg/ffprobe**
```bash
ffmpeg -version | head -1
ffprobe -version | head -1
```

**Step 2: 检查 Whisper**
```bash
python3 -c "import whisper; print(whisper.__version__)" 2>&1
```
若未安装，安装方案: `pip install openai-whisper`

**Step 3: 检查 Python 数据处理库**
```bash
python3 -c "import json, csv, os, glob, collections; print('stdlib OK')"
```

**Verification:** All commands return version strings without errors.

---

### Task 1.2: 生成全量数据盘点报表

**Objective:** 遍历所有 session 生成数据索引 CSV，包含每个文件的路径、类型、大小、时间戳

**Files:**
- Create: `/opt/lvdata/_inventory.csv`

**Step 1: 编写 Python inventory 脚本**

```python
# inventory.py
import os, json, csv, glob
from datetime import datetime

base = '/opt/lvdata/b53985c7-cb7f-4382-95c9-18d33a9bc36a'
rows = []

for session_id in sorted(os.listdir(base)):
    spath = os.path.join(base, session_id)
    if not os.path.isdir(spath): continue
    
    # Parse session.json
    sjson = os.path.join(spath, 'session.json')
    with open(sjson) as f:
        meta = json.load(f)
    
    for root, dirs, files in os.walk(spath):
        for fname in files:
            fpath = os.path.join(root, fname)
            fsize = os.path.getsize(fpath)
            ftype = os.path.relpath(fpath, spath).split('/')[0]  # audio/video/chat
            rows.append({
                'session_id': session_id[:36],
                'session_date': meta['session_date'],
                'session_status': meta['status'],
                'file_type': ftype,
                'file_path': fpath,
                'file_name': fname,
                'size_bytes': fsize,
                'size_kb': round(fsize/1024, 1),
            })

with open('/opt/lvdata/_inventory.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys())
    w.writeheader()
    w.writerows(rows)

print(f'Done: {len(rows)} files indexed')
```

**Step 2: 执行**
```bash
python3 /tmp/inventory.py
```

**Verification:** `/opt/lvdata/_inventory.csv` 存在，行数 = 音频数 + 视频帧数 + chat + session.json 数

---

## Phase 2: 音频转写 (ASR)

### Task 2.1: 将 OGG 批转为 WAV (Whisper 输入格式)

**Objective:** 用 ffmpeg 将所有 .ogg 转为 16kHz mono WAV

**Files:**
- Create: `/opt/lvdata/_wav/<session_id>/<segment_id>.wav`

**Step 1: 批转脚本**

```bash
#!/bin/bash
for ogg in $(find /opt/lvdata -name '*.ogg'); do
    wav="${ogg%.ogg}.wav"
    ffmpeg -y -i "$ogg" -ar 16000 -ac 1 "$wav" 2>/dev/null
    echo "Converted: $(basename $ogg) -> $(basename $wav)"
done
```

**Step 2: 执行**
```bash
bash convert_all.sh
```

**Verification:** WAV 文件数与 OGG 文件数一致 (共 59 个)

---

### Task 2.2: 对所有 WAV 文件进行语音转写

**Objective:** 用 Whisper (base 或 small 模型) 转写所有音频，输出带时间戳的文本

**Files:**
- Create: `/opt/lvdata/_transcripts/<session_id>/<segment_id>.txt`
- Create: `/opt/lvdata/_transcripts/<session_id>/<segment_id>.json` (含时间戳)

**Step 1: 选择模型** (small 模型对中文效果更好)
```python
import whisper
model = whisper.load_model("small")
```

**Step 2: 逐个转写**

```python
import whisper, os, json, glob

model = whisper.load_model("small")
audio_dir = '/opt/lvdata/b53985c7-cb7f-4382-95c9-18d33a9bc36a'

for wav in sorted(glob.glob(f'{audio_dir}/**/audio/*.ogg', recursive=True)):
    # 先转为 wav
    import subprocess
    wav_path = wav.replace('.ogg', '.wav')
    subprocess.run(['ffmpeg', '-y', '-i', wav, '-ar', '16000', '-ac', '1', wav_path], 
                   capture_output=True)
    
    result = model.transcribe(wav_path, language='zh', verbose=False)
    
    # 输出路径
    rel = os.path.relpath(wav, audio_dir)
    out_txt = f'/opt/lvdata/_transcripts/{rel}.txt'
    out_json = f'/opt/lvdata/_transcripts/{rel}.json'
    os.makedirs(os.path.dirname(out_txt), exist_ok=True)
    
    with open(out_txt, 'w') as f:
        f.write(result['text'])
    with open(out_json, 'w') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f'Transcribed: {os.path.basename(wav)} → {len(result["text"])} chars')
```

**Step 3: 执行**

**Verification:** 每个 OGG 对应一个 .txt 和一个 .json，文本非空

---

## Phase 3: 图像分析

### Task 3.1: 对所有截图进行内容描述

**Objective:** 用 vision_analyze 逐一分析 JPEG 截图并生成文字描述

**Files:**
- Create: `/opt/lvdata/_image_descriptions/<session_id>/<frame_id>.txt`

**Step 1: 逐个调用 vision_analyze**

对每个 .jpg 调用 `vision_analyze(image_url="<path>", question="描述这张截图中的内容：有哪些窗口和应用在运行？用户在做什么？")`

**Step 2: 保存结果**

将所有描述文本按 session 归档到 `/opt/lvdata/_image_descriptions/`

**Verification:** 每个 JPG 对应一个描述文件

---

## Phase 4: 多模态数据对齐与整合

### Task 4.1: 按时间戳对齐音频转写 & 聊天记录

**Objective:** 将音频转写（按 segment 时间戳）与聊天 JSONL 对齐，重建完整 user journey

**Files:**
- Create: `/opt/lvdata/_aligned/<session_id>/timeline.json`

**Step 1: 解析时间戳**

音频 segment 命名模式: `019fabe0-3c05-708d-8000-...` (ULID)
ULID 前 10 字节 = 48-bit timestamp (毫秒): `int(ulid[:10], 32)` = ms since epoch

Chat JSONL: `ts` 字段 = ms since epoch

**Step 2: 合并排序**

```python
events = []
# Add chat events
for chat_msg in chat_data:
    events.append({'ts': chat_msg['ts'], 'type': 'chat', 'role': chat_msg['role'], 'text': chat_msg['text']})
# Add audio segments
for audio_seg in audio_transcripts:
    ts = ulid_to_ms(audio_seg['id'][:10])
    events.append({'ts': ts, 'type': 'audio', 'text': audio_seg['text']})
# Add video frames
for frame in video_frames:
    ts = ulid_to_ms(frame['id'][:10])
    events.append({'ts': ts, 'type': 'video', 'path': frame['path'], 'description': frame['desc']})

events.sort(key=lambda e: e['ts'])
```

**Step 3: 输出 timeline.json**

```json
[
  {"ts": 1784876788183, "utc": "2026-07-24T07:06:28.183Z", "type": "chat", "role": "user", "text": "Hello, 你好。"},
  {"ts": 1784876789000, "utc": "2026-07-24T07:06:29.000Z", "type": "audio", "text": "你好我想问一个问题"},
  {"ts": 1784876828272, "utc": "2026-07-24T07:07:08.272Z", "type": "chat", "role": "assistant", "text": "Hi there!"},
  ...
]
```

**Verification:** `timeline.json` 包含所有事件，按时间排序，无遗漏

---

### Task 4.2: 生成会话摘要

**Objective:** 基于对齐后的 timeline 为每个 session 生成结构化摘要

**Files:**
- Create: `/opt/lvdata/_reports/session_summaries.md`

**摘要内容模板:**

```markdown
## Session: 2026-07-24 (7/24)
- Duration: 07:06 - 08:14 UTC (约68分钟)
- Audio: 6 segments (~6分钟用户语音)
- Video: 7 frames (桌面截图)
- Chat: 8 messages

### 用户行为时间线
1. 07:06 - 问候、聊天测试、讲故事请求
2. 07:07 - 抱怨响应慢
3. 07:11 - 第二轮互动：讲故事 + 图片分析（桌面照片）

### 主要话题
- AI 性能反馈 ("为什么这么慢")
- 故事创作需求
- 桌面环境分析

### 屏幕活动 (从截图中)
- macOS 桌面环境，包含浏览器、聊天应用等
```

**Verification:** 3 份 session summary 生成完毕

---

## Phase 5: 跨 Session 分析与洞察

### Task 5.1: 用户行为模式挖掘

**Objective:** 分析三次 session 间的模式

**分析维度:**
1. **活跃时段**: 用户主要在 UTC 7:00-15:00 互动 (对应北京时间 15:00-23:00)
2. **使用频率**: 7/24, 7/28, 7/29 — 间隔 3-4 天
3. **语言偏好**: 中英混用，中文为主
4. **核心需求**:
   - 故事创作/内容生成
   - 信息查询 (天气、财经、民族)
   - 桌面环境分析 (截图共享)
   - AI 性能反馈
5. **对话模式**: 短交互为主，单次 session 持续 20-70 分钟
6. **Fail Patterns**: AI 无法提供实时信息 (天气/新闻) — 重复出现

---

### Task 5.2: AI 助手表现评估

**Objective:** 评估聊天记录中 AI 助手的回复质量

**评估维度:**
1. **响应速度** — 用户抱怨过慢
2. **信息准确性** — 民族数据来自 2020 年普查，合理
3. **能力边界处理** — 对实时信息无法获取的诚实回应
4. **多语言能力** — 中英文切换流畅
5. **创意质量** — 故事创作有温度、有寓意

---

## Phase 6: 工具化与技能化

### Task 6.1: 构建可复用的分析 Pipeline

**Objective:** 将上述流程封装为可对任意新 session 自动运行的脚本

**Files:**
- Create: `~/projects/wt_d4f/scripts/lvdata_pipeline.py`
- Create: `~/projects/wt_d4f/scripts/lvdata_report.py`

**Pipeline 功能:**
1. `lvdata_pipeline.py --session-id <id>` → 执行 Phase 1-4
2. `lvdata_report.py --all` → 生成 HTML/Markdown 综合报告

---

### Task 6.2: 创建 Hermes Skill

**Objective:** 将此能力封装为可复用的 skill

**Skill 名称:** `lvdata-analyzer`
**触发条件:** 用户上传新数据到 /opt/lvdata/
**内含:**
- 数据解析逻辑
- ASR 转写命令
- 报告模板

---

## Phase 7: 可能的延伸任务

以下为可选的高级分析任务，根据实际转写内容判断是否值得做：

1. **语音情感分析**: 从音频中提取用户情绪变化
2. **桌面活动分类**: 从截图判断用户工作类型 (编程/浏览/写作)
3. **对话满意度预测**: 基于用户语气判断对 AI 回复的满意程度
4. **个性化推荐**: 根据历史话题推荐相关内容
5. **隐私审计**: 检查截图和音频中是否有敏感信息泄露
6. **Anomaly Detection**: 检测异常使用模式

---

## Execution Order

```
Phase 1 (环境 + 盘点)  ← 先做
    ↓
Phase 2 (音频转写)     ← 最耗时，可后台跑
    ↓
Phase 3 (图像分析)     ← 可与 Phase 2 并行
    ↓
Phase 4 (对齐 + 摘要)  ← 依赖 Phase 2 & 3 完成
    ↓
Phase 5 (跨 session)   ← 依赖 Phase 4
    ↓
Phase 6 (工具化)       ← 最后
```

---

## Quick Start (最小可行路径)

如果想先快速看到结果而不跑完整流程：

1. **先跑 Phase 1** (5 分钟) — 了解有什么数据
2. **手动看聊天记录** — 已可读，不需转写
3. **转写 1-2 个音频** — 小样本测试
4. **分析 1 个 session 的截图** — 验证图像分析质量
5. **决定是否跑全量**

---

*Plan version: 1.0 | Created: 2026-07-29*
