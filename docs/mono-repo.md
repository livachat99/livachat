已根据 PRD 和 UX 文档，为 LivaChat 设计了企业级 monorepo 目录结构。

---

## LivaChat Monorepo 结构

```
monorepo/
├── .github/                              # GitHub 配置
│   ├── workflows/                        # CI/CD 流水线
│   │   ├── ci-mobile.yml                 #   iOS/Android 构建与测试
│   │   ├── ci-backend.yml                #   Python 服务构建与测试
│   │   ├── cd-staging.yml                #   自动部署到 Staging
│   │   └── cd-production.yml             #   生产环境发布（手动触发）
│   ├── CODEOWNERS                        # 代码审查责任人
│   └── PULL_REQUEST_TEMPLATE.md          # PR 模板
│
├── apps/                                 # 可独立部署的应用
│   ├── ios/                              # iOS App (Swift + SwiftUI) + LivaChatSDK 本地 SPM package
│   ├── android/                          # Android App (Kotlin + Jetpack Compose) + LivaChatSDK module
│   └── web/                              # Web 前端预留 (React + TypeScript)
│
├── services/                             # 后端服务 (Python + FastAPI)
│   ├── gateway/                          # WebSocket 网关服务
│   ├── asr/                              # 语音识别服务 (Whisper, ElevenLabs, Deepgram,...)
│   ├── llm/                              # LLM 推理服务 (OpenAI, Anthropic, Deepseek, Kimi, Qwen..)
│   ├── tts/                              # 语音合成服务 (ElevenLabs, Deepgram, Qwen...)
│   ├── session/                          # 会话管理服务
│   ├── memory/                           # 记忆系统服务 (P2)
│   ├── user-profile/                     # 用户画像服务 (P2)
│   └── shared/                           # 服务间共享代码
│       ├── models/                       #   数据库模型
│       └── utils/                        #   工具函数
│
├── packages/                             # 跨项目共享包
│   ├── api-schema/                       # API 协议定义 (JSON Schema, 单一事实来源)
│   │   ├── c2s/                          #   客户端 → 服务端消息 schema
│   │   │   ├── audio.json
│   │   │   ├── interrupt.json
│   │   │   ├── message.json
│   │   │   ├── ping.json / pong.json
│   │   │   ├── session-start.json / session-end.json
│   │   │   └── video.json
│   │   ├── s2c/                          #   服务端 → 客户端消息 schema
│   │   │   ├── ai-audio.json / ai-text.json / ai-video-ack.json
│   │   │   ├── error.json / status.json
│   │   │   ├── message.json
│   │   │   ├── ping.json / pong.json
│   │   │   ├── session-ready.json / session-ended.json
│   │   │   └── transcription.json
│   │   ├── common.json                   #   公共类型定义
│   │   ├── message.json                  #   顶层消息封装
│   │   └── README.md                     #   Schema 使用说明
│   ├── proto/                            # Protobuf / gRPC 协议预留
│   ├── sdk-swift/                        # iOS 协议 DTO（LivaChatProtocol；SDK 在 apps/ios/LivaChatSDK）
│   ├── sdk-kotlin/                       # Android 协议 DTO（SDK 在 apps/android/LivaChatSDK）
│   │   └── src/main/                     #   生成的 Kotlin DTO 输出根目录
│   ├── sdk-python/                       # 后端 / Mock Server 共用 Python SDK
│   │   ├── pyproject.toml
│   │   ├── src/livachat_protocol/        #   包根
│   │   │   ├── __init__.py
│   │   │   ├── py.typed                  #     PEP 561 类型标记
│   │   │   └── generated/                #     从 api-schema 生成的 DTO
│   │   │       ├── protocol_models.py
│   │   │       ├── client_message.py
│   │   │       └── server_message.py
│   │   └── tests/                        #   单元测试 (含生成器集成测试)
│   └── sdk-ts/                           # [预留] Web/Node TypeScript SDK
│
├── 3rdparty/                             # 第三方预编译库与源码
│   ├── dist-android/                     #   Android 预编译静态库
│   │   └── arm64-v8a/
│   │       ├── include/                  #     公共头文件 (opus, webrtc_vad)
│   │       │   ├── opus/
│   │       │   ├── webrtc_vad.h
│   │       │   └── webrtc_vad_lite.h
│   │       └── lib/                      #     静态库
│   │           ├── libopus.a
│   │           └── libwebrtc_vad_lite.a
│   ├── dist-ios/                         #   iOS xcframework
│   │   ├── Opus.xcframework/             #     (ios-arm64 + ios-arm64_x86_64-simulator)
│   │   └── WebRtcVadLite.xcframework/    #     预编译 VAD（仅二进制入库）
│   └── opus/                             #   Opus 源码与构建脚本
│       ├── opus-1.6.1.tar.gz
│       ├── build_opus_android.sh
│       └── build_opus_ios.sh
│
├── infra/                                # 基础设施即代码 (IaC)
│   ├── terraform/                        # Terraform 配置
│   │   ├── modules/                      #   可复用模块
│   │   ├── environments/
│   │   │   ├── dev/
│   │   │   ├── staging/
│   │   │   └── production/
│   │   └── main.tf
│   ├── kubernetes/                       # K8s 部署清单
│   │   ├── base/                         #   基础配置
│   │   └── overlays/
│   │       ├── dev/
│   │       ├── staging/
│   │       └── production/
│   └── docker/                           # 共享 Dockerfile 模板
│
├── configs/                              # 环境配置
│   ├── dev/                              # 开发环境
│   ├── staging/                          # 预发布环境
│   └── production/                       # 生产环境
│
├── scripts/                              # 自动化脚本
│   ├── bootstrap.sh                      # 新开发者环境初始化
│   ├── build-ios.sh                      # iOS 构建
│   ├── build-android.sh                  # Android 构建
│   ├── deploy-backend.sh                 # 后端部署
│   ├── lint-all.sh                       # 全仓库代码检查
│   ├── generate-api-clients.sh           # 从 schema 生成客户端代码（入口）
│   ├── generate_kotlin_api.py            # Kotlin DTO 代码生成
│   └── generate_python_api.py            # Python DTO 代码生成
│
├── agents/                               # AI Agent 协作配置
│   ├── prompts/                          # 系统提示词模板
│   │   ├── code-review.md                #   代码审查 Agent 提示
│   │   ├── ios-dev.md                    #   iOS 开发 Agent 提示
│   │   ├── android-dev.md                #   Android 开发 Agent 提示
│   │   └── backend-dev.md                #   后端开发 Agent 提示
│   └── rules/                            # Agent 行为规则
│       ├── coding-standards.md           #   编码规范
│       └── architecture-decisions.md     #   架构决策记录
│
├── docs/                                 # 项目文档
│   ├── livachat_prd.md                   # 产品需求文档 (PRD)
│   ├── livachat_ux.md                    # 交互/UX 设计文档
│   ├── ws-protocol-design.md             # WebSocket 协议设计 (Phase 1)
│   ├── mono-repo.md                      # 本文件：Monorepo 结构说明
│   ├── architecture/                     # 架构设计文档
│   ├── api/                              # API 文档 (自动生成)
│   ├── runbooks/                         # 运维手册
│   └── adr/                              # 架构决策记录 (Architecture Decision Records)
│
├── tests/                                # 端到端测试与集成测试
│   ├── e2e-mobile/                       # App 端到端测试 (Maestro / Appium)
│   ├── e2e_backend/                      # 后端集成测试
│   ├── load-tests/                       # 压力测试 (k6 / Locust)
│   └── fixtures/                         # 测试数据
│
├── tools/                                # 内部开发工具
│   ├── codegen/                          # 从 api-schema 生成 Swift/Kotlin/Python 类型
│   ├── proto-gen/                        # Protocol Buffer 代码生成器
│   ├── schema-validator/                 # API Schema 校验工具
│   └── local-dev/                        # 本地开发辅助工具
│       ├── docker-compose.yml            #   本地依赖 (Redis, PostgreSQL 等)
│       └── mock-services/                #   模拟外部服务
│           └── livachat_mock_server/     #     LivaChat WebSocket Mock Server
│               ├── DESIGN.md             #       设计文档
│               ├── README.md             #       使用说明
│               ├── Dockerfile            #       容器化
│               ├── pyproject.toml        #       Python 项目配置
│               ├── src/livachat_mock_server/  #   源码包
│               │   ├── __init__.py
│               │   ├── app.py            #         FastAPI 应用
│               │   ├── main.py           #         入口
│               │   ├── config.py         #         配置管理
│               │   ├── protocol.py       #         WS 协议实现
│               │   ├── scenarios.py      #         测试场景编排
│               │   ├── session_store.py  #         会话状态存储
│               │   └── media_fixtures.py #         测试媒体资源
│               └── tests/                #       Mock Server 自身测试
│                   ├── test_protocol_flow.py
│                   └── test_scenarios.py
│
├── .editorconfig                         # 编辑器统一配置
├── .gitignore                            # Git 忽略规则
├── .pre-commit-config.yaml               # Pre-commit 钩子
├── Makefile                              # 常用命令入口 (codegen / mock-server)
├── Taskfile.yml                          # 任务运行器 (替代 Make)
└── REPO.md                               # 项目总览
```

---

## 目录说明

| 目录 | 说明 |
|------|------|
| `.github/` | GitHub Actions CI/CD、PR 模板、代码审查规则 |
| `apps/` | 所有可独立部署的客户端应用，按平台隔离；`apps/web/mylife/` = MyLife 只读视图 SPA（Timeline/Graph/Replay，React + Vite，生产由 BFF 静态托管；**已废止**，MyLife 唯一消费方为 Android App，见 timezone-support.md §9） |
| `services/` | 后端微服务，按业务域拆分，支持独立部署和扩展。Phase 1: 单一服务，不同模块；`services/bff/` = MyLife 查询面模块（只读 lvdata，挂载于 `/api/mylife`） |
| `packages/` | 跨平台共享代码，API 协议 (JSON Schema) 是核心，确保多端一致性 |
| `3rdparty/` | 第三方预编译库和源码 (Opus 音频编解码)，按平台分发 |
| `infra/` | 基础设施即代码，环境隔离清晰，支持多云部署 |
| `configs/` | 环境配置集中管理，避免散落在各服务中 |
| `scripts/` | 标准化构建、部署、lint、代码生成脚本，降低协作成本 |
| `agents/` | AI Agent 提示词和规则，支持自动生成代码和审查 |
| `docs/` | 技术文档和架构决策记录，与代码同版本管理 |
| `tests/` | 端到端和集成测试，独立于单元测试 |
| `tools/` | 内部工具 (代码生成、Mock Server、本地开发辅助)，提升开发效率 |

---
