# iOS 协议 DTO 设计与实现方案

> 状态：已评审并实现（2026-10-19）：D2 采用备选方案（Swift 生成器独立解析 Schema），
> 其余按推荐；实现与验证结果见 §9。
> 基准：`docs/ws-protocol-design.md` v1.2 + `packages/api-schema/`（协议 SSOT）。
> 参考实现：`scripts/generate_kotlin_api.py`、
> `packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/`、
> `apps/android/LivaChatSDK/src/main/kotlin/dev/livachat/sdk/internal/protocol/MessageCodec.kt`。
> 规范：`docs/ios_dev_rules.md`（iOS 15 / Swift + SwiftUI / SPM）。

## 0. 评审结论（2026-10-19）

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 生成还是手写 | 采用推荐：从 JSON Schema 生成，新增 Swift 生成器 |
| D2 | 生成器代码复用 | **采用备选：Swift 生成器独立解析 Schema；不修改 `generate_kotlin_api.py` 与 `packages/sdk-kotlin` 生成物** |
| D3 | Base64 字段类型 | 采用推荐：`Data`（Foundation 自动 Base64） |
| D4 | 无符号整型 | 采用推荐：精确宽度 `UInt8/16/32/64`（无 `maximum` 时用 `Int`） |
| D5 | 整数枚举 | 采用推荐：仅 `Orientation`、`AudioSampleRate` 生成 Swift 枚举（命名表见 §3.5） |
| D6 | 未知 S2C 枚举值 | 采用推荐：严格枚举，解码失败丢帧并记录 |
| D7 | 包/产物命名 | 采用推荐：包 `LivaChatSDK`，target/product `LivaChatProtocol` |
| D8 | Swift 语言模式 | 采用推荐：`swift-tools-version: 6.0`，DTO 全部 `Sendable` |

D2 取舍：独立解析会形成两套 Schema 解释逻辑；通过两端 `--check`、生成物入库 review 和
人工字段对照清单控制漂移，不额外引入校验脚本。好处是 Kotlin 生成链零改动、风险隔离。
其余决策按原推荐执行。

---

## 1. 目标与边界

### 1.1 目标

- 为 `docs/ws-protocol-design.md` 中全部 20 种消息（C2S 8 种、S2C 12 种）提供 Swift DTO。
- 与 Kotlin 一样以 `packages/api-schema/` 为唯一事实来源，由脚本生成，禁止手改。
- 提供明确的 JSON 编码/解码契约：可选字段、required-nullable、未知字段、未知消息类型、
  Base64、整数宽度、枚举。
- DTO 为纯 Swift + Foundation，不引入任何第三方依赖，不使用 UIKit/Objective-C/CocoaPods。
- 生成的 DTO 可在 iOS 15 上编译运行，并满足 Swift 6 并发检查（`Sendable`）。

### 1.2 非目标（本阶段不做）

- WebSocket 传输、重连、心跳、seq 重排、会话状态机（后续 SDK 层）。
- Opus/JPEG 编解码、音频播放队列、视频采集（后续 pipeline 层）。
- `apps/ios` 工程与 UI（后续单独设计）。
- Schema 的 pattern/range 运行期校验（如 `sess_` 前缀、quality 1–100）；
  这些由服务端权威校验，DTO 只保证类型可表达。

---

## 2. Kotlin 实现分析

### 2.1 结构与建模

- 生成器读取 `packages/api-schema/{common,c2s,s2c}`，输出三个文件：
  `ProtocolModels.kt`（枚举 + 所有 payload data class）、`ClientMessage.kt`、`ServerMessage.kt`。
- 消息使用 `sealed class` + `@JsonClassDiscriminator("type")`，每个子类一个
  `@SerialName("<wire type>")`。
- payload 类按方向命名（`ClientPingPayload` / `ServerPingPayload`），避免同名消息合并。
- 可选字段统一生成为「可空 + `= null` 默认值」；`required` 且 `oneOf: [null, T]` 的字段
  生成为可空但**无默认值**。
- 枚举由 Schema 驱动：`type:string + enum` 生成 Kotlin enum；整数枚举（如 `Orientation`）
  退化为 `Int`；const 退化为 `String`/`Int`/`Boolean`。
- Schema `description` 保留为 KDoc；空 payload（ping）生成空类。
- 生成器支持 `--check`，用于 CI 检查产物漂移。

### 2.2 运行时 JSON 契约（以 `MessageCodec` 实际代码为准）

```kotlin
Json {
    classDiscriminator = "type"
    ignoreUnknownKeys = true
    explicitNulls = true
}
// encodeDefaults 保持 kotlinx 默认 false
```

有效语义：

| 场景 | 行为 |
|------|------|
| 可选字段为 null | 因 `encodeDefaults=false` 且默认值就是 null，序列化时省略 |
| `session_id`（required + nullable） | 无默认值，`explicitNulls=true`，必须输出 `null` 或字符串 |
| 未知字段 | 解码时忽略（`ignoreUnknownKeys=true`） |
| 编码失败 | 抛 `CodecException` |
| 解码失败/未知 type | 返回 `null`，由上层丢弃 |

> 注：`generate_kotlin_api.py` 文件头的注释写的是 `explicitNulls = false`，
> 但 `MessageCodec` 实际使用 `true`。本设计以实际代码为准；建议后续顺手修正该注释。

### 2.3 可复用与必须差异

| 维度 | Kotlin | Swift 方案 |
|------|--------|------------|
| 多态消息 | sealed class + discriminator 注解 | `enum` + 手写 `Codable`（Swift 无内建类型判别） |
| 可选字段省略 | 默认值 + `encodeDefaults=false` | synthesized `encodeIfPresent` 天然省略 nil |
| required-nullable | 无默认值 + explicitNulls | 手写 `encodeNil` + 解码时检查 key 存在 |
| 整数 | 统一 `Long`/`Int` | 精确宽度无符号整型（无 `maximum` 时用 `Int`） |
| Base64 | `String` | `Data`（默认 Base64 strategy） |
| 未知字段 | 显式配置 | Swift 合成 Codable 默认忽略 |
| 空 payload | 空 class | 空 struct |
| 并发安全 | JVM 侧自行约束 | 全部 `Sendable` |
| 解码失败 | 返回 null | 抛类型化错误，传输层丢帧 |

---

## 3. Swift DTO 设计

### 3.1 Package 结构

```text
packages/sdk-swift/
├── Package.swift
├── Sources/
│   └── LivaChatProtocol/
│       ├── Generated/                  # 生成产物，禁止手改
│       │   ├── ProtocolModels.swift
│       │   ├── ClientMessage.swift
│       │   └── ServerMessage.swift
│       └── ProtocolJSON.swift          # 手写：codec 配置 + header 预解析 + 错误类型
└── Tests/
    └── LivaChatProtocolTests/
        ├── Fixtures/                   # 协议示例报文
        ├── EncodingTests.swift
        ├── DecodingTests.swift
        └── RoundTripTests.swift
```

`Package.swift` 草案：

```swift
// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "LivaChatProtocol",
    platforms: [.iOS(.v15), .macOS(.v13)], // macOS 仅用于主机侧 swift test
    products: [
        .library(name: "LivaChatProtocol", targets: ["LivaChatProtocol"]),
    ],
    targets: [
        .target(name: "LivaChatProtocol"),
        .testTarget(
            name: "LivaChatProtocolTests",
            dependencies: ["LivaChatProtocol"],
            resources: [.copy("Fixtures")]   // 测试通过 Bundle.module 访问
        ),
    ]
)
```

后续 SDK target（WebSocket、音频管线）追加到同一 Package，依赖 `LivaChatProtocol`；
`apps/ios` 通过本地 SPM path 依赖本 Package。

### 3.2 DTO 建模规则

- payload 一律 `public struct`，属性 `public let`，显式生成 `public init`。
- 所有 DTO 声明 `Codable, Equatable, Sendable`；消息枚举声明 `Equatable, Sendable` 并单独扩展 `Codable`。
- 属性名与 Kotlin 生成物一致（`sessionId`、`durationMs`、`isFinal`、`serverTimeMs` 等），
  便于双端对照 review。
- 生成的 `CodingKeys` 显式列出 wire key，不依赖 `.convertFromSnakeCase` 等全局策略。
- 可选属性在 `init` 中给 `= nil` 默认值；required 属性无默认值；
  required-nullable 无默认值（调用方必须显式传 `nil`）。
- 空 payload 生成空 struct（`public struct ClientPingPayload: Codable, Equatable, Sendable { public init() {} }`）。

消息枚举草案（与 Kotlin sealed class 一一对应）：

```swift
public enum ClientMessage: Equatable, Sendable {
    case sessionStart(seq: UInt32, timestamp: UInt64, payload: ClientSessionStartPayload)
    case sessionEnd(seq: UInt32, timestamp: UInt64, payload: ClientSessionEndPayload)
    case sessionChange(seq: UInt32, timestamp: UInt64, payload: ClientSessionChangePayload)
    case audio(seq: UInt32, timestamp: UInt64, payload: ClientAudioPayload)
    case video(seq: UInt32, timestamp: UInt64, payload: ClientVideoPayload)
    case interrupt(seq: UInt32, timestamp: UInt64, payload: ClientInterruptPayload)
    case ping(seq: UInt32, timestamp: UInt64, payload: ClientPingPayload)
    case pong(seq: UInt32, timestamp: UInt64, payload: ClientPongPayload)

    public var type: String { /* "session.start" ... */ }
    public var seq: UInt32 { /* 各 case 解构 */ }
    public var timestamp: UInt64 { /* 各 case 解构 */ }
}
```

`ServerMessage` 同理（12 个 case），并通过 `init(from:)` 按 `type` 分发。

payload 示例：

```swift
public struct ClientAudioPayload: Codable, Equatable, Sendable {
    public let data: Data                 // Base64 on the wire
    public let codec: String              // "opus"
    public let durationMs: Int            // 20 / 40
    public let vadState: VadState

    public init(data: Data, codec: String, durationMs: Int, vadState: VadState) { ... }

    private enum CodingKeys: String, CodingKey {
        case data, codec
        case durationMs = "duration_ms"
        case vadState = "vad_state"
    }
}
```

### 3.3 编解码实现

Swift 没有 kotlinx 的 `classDiscriminator`，`ClientMessage` / `ServerMessage` 需生成手写 `Codable`：

```swift
extension ClientMessage: Codable {
    private enum CodingKeys: String, CodingKey {
        case type, seq, timestamp, payload
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case let .sessionStart(seq, timestamp, payload):
            try c.encode("session.start", forKey: .type)
            try c.encode(seq, forKey: .seq)
            try c.encode(timestamp, forKey: .timestamp)
            try c.encode(payload, forKey: .payload)
        // ... 其余 case
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        let seq = try c.decode(UInt32.self, forKey: .seq)
        let timestamp = try c.decode(UInt64.self, forKey: .timestamp)
        switch type {
        case "session.start":
            self = .sessionStart(
                seq: seq, timestamp: timestamp,
                payload: try c.decode(ClientSessionStartPayload.self, forKey: .payload)
            )
        // ... 其余 case
        default:
            throw ProtocolDecodingError.unknownMessageType(
                type: type, seq: seq, timestamp: timestamp
            )
        }
    }
}
```

要点：

- 编码时 `type` 与 case 绑定，不存在 type/payload 不一致的可能。
- 解码先取 `type`，再解码对应 payload；未知 type 抛错（携带 seq/timestamp，供传输层记录/重排）。
- `ServerMessage` 同样双向 `Codable`（应用侧只用到 decode，encode 用于测试与调试）。
- 不引入 `AnyCodable`、SwiftyJSON 等依赖。

### 3.4 Null / 可选字段语义（关键）

| Schema 形态 | Swift 表达 | 编码行为 | 解码行为 |
|-------------|-----------|----------|----------|
| required 非空 | `let x: T` | 必输出 | 缺失即抛错 |
| required + nullable（当前仅 `session_id`） | `let x: T?`（无默认值） | 恒输出 key，nil 时输出 `null` | 必须存在；`null` 解为 nil |
| 可选（optional） | `let x: T? = nil` | nil 时省略 key | 缺失或 null 均为 nil |

`session_id` 需要手写 `encode(to:)` / `init(from:)`：

```swift
public func encode(to encoder: Encoder) throws {
    var c = encoder.container(keyedBy: CodingKeys.self)
    if let sessionId {
        try c.encode(sessionId, forKey: .sessionId)
    } else {
        try c.encodeNil(forKey: .sessionId)
    }
    try c.encode(config, forKey: .config)
}

public init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    guard c.contains(.sessionId) else {
        throw DecodingError.keyNotFound(
            CodingKeys.sessionId,
            .init(codingPath: c.codingPath, debugDescription: "session_id is required")
        )
    }
    self.sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId)
    self.config = try c.decode(ClientSessionStartConfig.self, forKey: .config)
}
```

在 null / 可选语义上，这是 Swift 与 Kotlin 唯一的显著差异点，生成器必须特判，不能依赖 synthesized Codable。

> 上述关键语义已在本机 Xcode 16.4 / Swift 6.1.2 上实测：
> nil optional 合成编码结果为 `{}`；required-nullable 输出 `{"session_id":null}`；
> 非 nil 的 `session_id` 正常编码，`null` 正常解为 nil；
> 空 struct 编码/解码 `{}` 正常；`Data` 默认按 Base64（`AQID`）且 round-trip 一致；
> `UInt32.max` / `UInt64.max` 可正常编解码；缺失 `session_id` 抛 `keyNotFound`；
> `UInt8` 越界（256 / -1）与未知枚举值均抛错；未知字段被忽略；
> `.error` 可作为 Swift enum case 名；Swift 6 `-strict-concurrency=complete` 下
> DTO 的 `Sendable` 类型检查通过；主机侧 `swift test` 与 iOS 模拟器 `xcodebuild test`
> 已在等价最小 Package 上跑通（scheme 名见 §6.2）。

### 3.5 类型映射

| JSON Schema | Swift 类型 | 说明 |
|-------------|-----------|------|
| `string` | `String` | |
| `boolean` | `Bool` | |
| `integer` 且声明 maximum ≤ 255 | `UInt8` | `quality`、`keepalive_interval_s` |
| `integer` 且声明 maximum ≤ 65535 | `UInt16` | `video.width/height`、错误 `code` |
| `integer` 且声明 maximum ≤ 4294967295 | `UInt32` | `seq`、`frame_id`、`ack_frame_id`、`index`、`ping_seq`、`turn_id` |
| `integer` 且声明 maximum ≤ 18446744073709551615 | `UInt64` | `timestamp`、`server_time_ms`、`started_at_ms`、`running_time_ms` |
| `integer` 未声明 maximum | `Int` | `server_ai_text.image.width/height`（仅有 `minimum: 1`）、`AudioDurationMs`（oneOf 20/40） |
| `type:string + enum` | `String` raw enum | wire 值保持原字符串 |
| `type:integer + enum` | `Int` raw enum | 仅 `Orientation`、`AudioSampleRate`，命名见下方 |
| `Base64String` | `Data` | 依赖 JSONEncoder/Decoder 默认 Base64 strategy |
| array | `[T]` | |
| object | struct | |
| const | 对应基础类型 | 如 `codec: String` |
| `oneOf:[null, X]`（required） | `Optional<X>` 无默认值 | 见 §3.4 |
| 可选属性 | `Optional<X> = nil` | 见 §3.4 |

> 映射优先级：先匹配已知 `$defs`（`Base64String`、`AudioDurationMs`、整数枚举等），
> 再按声明的 `maximum` 选择无符号宽度；**没有 `maximum` 的整数统一用 `Int`**，与 Kotlin 对齐。
> 注意 `server_ai_text.image.width/height` 只有 `minimum: 1`、没有 `maximum`，不能套用 `UInt16`。
> `ai_text.image.data` 在 Schema 中是普通 `string`（未引用 `Base64String`），但描述明确为
> Base64 JPEG；Swift 生成器特判为 `Data`，并叠加 Schema 的 nullability，与音频/视频
> Base64 字段保持一致。

整数枚举的 case 名不做通用推导，按下表显式登记（当前仅两个）；未登记的整数枚举保持 `Int`、不生成枚举：

| 枚举 | Swift cases |
|------|-------------|
| `Orientation` | `degrees0` / `degrees90` / `degrees180` / `degrees270` |
| `AudioSampleRate` | `hz16000` / `hz24000` |

对应 Swift 声明：

```swift
public enum Orientation: Int, Codable, Equatable, Sendable {
    case degrees0 = 0
    case degrees90 = 90
    case degrees180 = 180
    case degrees270 = 270
}

public enum AudioSampleRate: Int, Codable, Equatable, Sendable {
    case hz16000 = 16000
    case hz24000 = 24000
}
```

字符串枚举统一 lowerCamel case，rawValue 保留 wire 值：

```swift
public enum StatusReasonCode: String, Codable, Equatable, Sendable {
    case connected
    case asrFinal = "asr_final"
    case ttsStart = "tts_start"
    case ttsFinish = "tts_finish"
    case interrupted
    case idleTimeout = "idle_timeout"
    case modeChanged = "mode_changed"
}
```

### 3.6 枚举兼容策略

- 采用严格枚举（D6 已确认）：遇到未知枚举值，该帧解码失败，由传输层丢弃并记录日志，
  不影响连接与后续帧。
- 理由：协议变更需三方评审并更新 Schema；iOS DTO 会随 Schema 重新生成。
  引入 `.unknown(String)` 会让所有 switch 多出分支、增加业务处理成本。
- 如果 review 更看重前向兼容，可改为 `RawRepresentable` + `.unknown(String)`；
  代价是每个枚举的构造、rawValue、Codable 都需手写。

### 3.7 并发与线程安全

- 所有 DTO 为不可变值类型，声明 `Sendable`，可跨 actor 传递。
- `JSONEncoder` / `JSONDecoder` 本身不是线程安全的，DTO 层只提供工厂方法，不提供共享单例；
  SDK 层应在单一串行队列/actor 内持有 codec 实例。
- 50Hz 音频编码时复用同一个 encoder 实例，避免每帧创建。

### 3.8 校验边界

- DTO 只做 JSON 类型与枚举合法性校验，不校验 pattern/range/字符串长度。
- 服务端仍是协议权威；客户端在构造 C2S 报文时应保证取值合法。
- 未知字段解码时忽略（Swift Codable 默认行为），与 Kotlin `ignoreUnknownKeys=true` 等价。
- `session.start.config` 的 `additionalProperties: true` 只影响解码容错；当前 Swift DTO
  不保存未知 config 字段。未来新增配置项需更新 Schema 并重新生成。

### 3.9 供传输层使用的 header 预解析

为满足 `ws-protocol-design.md` §4.1 的 seq 重排要求，`LivaChatProtocol` 额外提供：

```swift
public struct MessageHeader: Decodable, Equatable, Sendable {
    public let type: String
    public let seq: UInt32
    public let timestamp: UInt64
}

public enum ProtocolDecodingError: Error, Equatable {
    case unknownMessageType(type: String, seq: UInt32, timestamp: UInt64)
}
```

传输层可先解 header 拿 seq（即使 payload 结构未知或不符合 DTO，只要整体 JSON 合法），再决定是否完整解码；
这样单帧解码失败不会造成 seq 空洞导致重排缓冲等待。

---

## 4. 消息清单与 Swift 命名

| 方向 | wire type | Swift case | payload 类型 |
|------|-----------|------------|--------------|
| C2S | `session.start` | `.sessionStart` | `ClientSessionStartPayload` |
| C2S | `session.end` | `.sessionEnd` | `ClientSessionEndPayload` |
| C2S | `session.change` | `.sessionChange` | `ClientSessionChangePayload` |
| C2S | `audio` | `.audio` | `ClientAudioPayload` |
| C2S | `video` | `.video` | `ClientVideoPayload` |
| C2S | `interrupt` | `.interrupt` | `ClientInterruptPayload` |
| C2S | `ping` | `.ping` | `ClientPingPayload` |
| C2S | `pong` | `.pong` | `ClientPongPayload` |
| S2C | `session.ready` | `.sessionReady` | `ServerSessionReadyPayload` |
| S2C | `session.ended` | `.sessionEnded` | `ServerSessionEndedPayload` |
| S2C | `session.changed` | `.sessionChanged` | `ServerSessionChangedPayload` |
| S2C | `transcription` | `.transcription` | `ServerTranscriptionPayload` |
| S2C | `ai_text` | `.aiText` | `ServerAiTextPayload` |
| S2C | `job_status` | `.jobStatus` | `ServerJobStatusPayload` |
| S2C | `ai_audio` | `.aiAudio` | `ServerAiAudioPayload` |
| S2C | `ai_video_ack` | `.aiVideoAck` | `ServerAiVideoAckPayload` |
| S2C | `status` | `.status` | `ServerStatusPayload` |
| S2C | `error` | `.error` | `ServerErrorPayload` |
| S2C | `ping` | `.ping` | `ServerPingPayload` |
| S2C | `pong` | `.pong` | `ServerPongPayload` |

命名与 Kotlin 生成物保持一致，包含由 Schema 推导出的内联枚举名
（`ClientSessionChangeMode`、`ClientSessionChangeUsecase`、`ServerSessionChangedMode`、
`ServerSessionChangedUsecase`）和数组元素类名（`ServerJobStatusJobsItem`）。

---

## 5. 代码生成方案

### 5.1 Swift 生成器独立解析 Schema（D2）

按评审结论，**不修改** `scripts/generate_kotlin_api.py`，也不修改
`packages/sdk-kotlin/src/main/kotlin/dev/livachat/protocol/generated/` 下的任何产物。
新增独立脚本：

```text
scripts/generate_swift_api.py        # 新增：自带 Schema 解析 + Swift 渲染 + --check
```

- 生成器只读取 `packages/api-schema/`；不 import Kotlin 生成器的渲染逻辑，不共享 IR。
- 命令行参数与 Kotlin 版对齐：`--schema`、`--out`、`--check`。
- 两端唯一的共享物是 JSON Schema 输入；Kotlin 生成链保持原样。
- 基线（2026-10-19）：`generate_kotlin_api.py --check` 当前为 up to date；
  实现期间不得改变该状态。
- 独立解析的漂移风险通过两端 `--check`、生成物入库 review 和人工字段对照清单控制：
  消息 wire type、字段 wire name、必填/可选、required-nullable、枚举 wire value。
- 同名类型冲突检测：同名 struct/enum 只有在规范化定义完全一致时才幂等复用；
  否则生成期报错退出，同时检测 struct 与 enum 的跨类型同名冲突，以及 union 中重复的
  消息 type / 归一化后重复的 Swift case 名。
- 验收：Kotlin 生成器与生成物零 diff；`generate_kotlin_api.py --check`（只读）与
  `make check-swift-api-generated` 均通过。

### 5.2 Swift 渲染规则

- 输出三个文件，文件名、类型顺序、payload 命名与 Kotlin 对齐。
- 文件头：`Auto-generated by scripts/generate_swift_api.py. DO NOT EDIT MANUALLY.` + Schema 源文件列表。
- `public` 访问级别：类型、属性、枚举 case、init、消息 case。
- Schema `description` 渲染为 `///` 文档注释。
- 属性顺序按 Schema `properties` 顺序；枚举与 class 列表按名称排序，消息按 union 文件顺序。
- 枚举 case 命名：
  - 字符串枚举：lowerCamel（`asr_final` → `asrFinal`），rawValue 保留原值。
  - 整数枚举：仅对 §3.5 命名表登记的两个枚举生成 Swift 枚举
    （`Orientation.degrees90`、`AudioSampleRate.hz24000`）；未登记的一律保持 `Int`，
    避免生成不可推导的 case 名。
- 布尔字段用 `isFinal` 等与 Kotlin 一致的命名。
- Swift 关键字属性名、枚举 case 名、消息 case 名统一用反引号转义；
  `CodingKeys` 的 raw value 保持 wire name 不变。
- 生成器支持 `--check`：内存渲染后与磁盘文件逐字节比较，漂移则非零退出。

### 5.3 Makefile 目标

```make
SWIFT_API_OUT ?= packages/sdk-swift/Sources/LivaChatProtocol/Generated

.PHONY: generate-swift-api check-swift-api-generated swift-protocol-test

generate-swift-api:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT)

check-swift-api-generated:
	python3 scripts/generate_swift_api.py --schema packages/api-schema --out $(SWIFT_API_OUT) --check

swift-protocol-test:
	cd packages/sdk-swift && swift test
```

CI 接入待 iOS CI（`.github/workflows/ci-mobile.yml`）建立后补上；当前先保证本地命令可跑。

---

## 6. 测试计划

### 6.1 主机侧（`swift test`，macOS）

不需要模拟器即可验证纯 DTO 逻辑，作为最快反馈回路。

用例清单：

1. **编码 golden**：每种 C2S 消息构造 DTO，编码后与期望 JSON 语义比较（解析为对象比较，不依赖 key 顺序）。
2. **解码**：每种 S2C 消息从协议示例 JSON 解码，断言关键字段与枚举值。
3. **round-trip**：20 种消息 encode → decode → 与原值相等。
4. **required-nullable**：`session_id = nil` 必须输出 `"session_id": null`；缺失该 key 必须解码失败。
5. **可选字段**：`turn_id`、`text`、`hint`、`server_version`、`image` 等为 nil 时不出现在 JSON 中。
6. **空 payload**：ping 编码为 `"payload": {}`，解码 `{}` 成功。
7. **未知字段**：payload 中混入额外字段仍能解码（前向兼容）。
8. **未知消息类型**：抛 `unknownMessageType`，并携带正确的 seq/timestamp。
9. **未知枚举值**：验证严格枚举解码失败（丢帧）行为。
10. **整数边界**：`UInt8/UInt16/UInt32/UInt64` 最大值可编解码，越界值解码抛错。
11. **Base64**：音频/视频/`ai_text.image.data` 的 `Data` 编解码与标准 Base64 字符串互通；
    空/非法数据行为明确。
12. **type 属性**：每个消息 case 的 `type` 与 wire 值一致。

fixture 来源：Schema 中的 `examples` + 手工补充边界用例。建议实现一个测试辅助函数，
从各 Schema 文件的 `examples` 字段提取样例，避免手工复制漂移（可作为后续增强）。

### 6.2 模拟器侧

- 在 `packages/sdk-swift` 目录执行：
  `xcodebuild test -scheme LivaChatProtocol -destination 'platform=iOS Simulator,name=iPhone 16 Pro'`
  （2026-10-19 更新：SDK 源码已迁到 `apps/ios/LivaChatSDK`，本包只保留协议 DTO；
  单 product 包的测试 scheme 为 `LivaChatProtocol`。SDK 的 scheme 为 `LivaChatSDK`，
  在 `apps/ios/LivaChatSDK` 目录执行）。
- 本机 iOS 15 runtime 为 unavailable（宿主 macOS 过高），现有 iOS 17.0 / 18.6 模拟器可用；
  iOS 15 兼容性以编译期 deployment target 检查为准，发布前补 iOS 15 真机冒烟。

### 6.3 生成器校验

- `generate_kotlin_api.py --check` 保持通过（只读，确认 Kotlin 生成物仍与 Schema 同步；
  不运行会写文件的 `make check-android-api-generated`）。
- `make check-swift-api-generated` 通过。
- 人工对照清单：消息 wire type、字段 wire name、必填/可选、required-nullable、枚举 wire value
  与 Kotlin 生成物一致。
- Schema 变更后双端同时重新生成，CI 阻止手改产物。

---

## 7. 兼容性与演进

| 变更类型 | 老客户端行为 | iOS 需要做什么 |
|----------|--------------|----------------|
| 新增可选字段 | 忽略未知字段 | 重新生成 DTO，按需使用新字段 |
| 新增枚举值 | 严格枚举解码失败，丢该帧 | 协议评审时同步升级客户端 |
| 新增消息类型 | 抛 `unknownMessageType`，丢该帧 | 升级客户端后重新生成 |
| required 字段变可选 | 老客户端仍按 required 解码；服务端停止发送会导致老客户端解码失败 | 服务端在过渡期继续发送该字段；新客户端重新生成后按 optional 处理 |
| 可选字段变 required | 老客户端可能不发 | 属破坏性变更，需三方评审并保证服务端兼容 |
| 提高最低 iOS 版本 | — | 按 `ios_dev_rules.md` 单独审批 |

DTO 与 SDK 版本策略：DTO 只随协议变化；App 版本与协议版本解耦。
`client_version` 由 App/传输层按 `ios-<versionName>.<build>` 构造（`<build>` 用单段数字，
如 `ios-1.0.0.42`；不要使用 `1.2.3` 形式的多段 build），DTO 只承载字符串。

---

## 8. 风险与待确认项

| 风险/问题 | 影响 | 推荐处理 |
|-----------|------|----------|
| Swift 无 discriminator 自动解码 | 需生成手写 Codable，代码量增加 | 生成器统一产出，测试覆盖 20 种消息 |
| `UInt32` 运算下溢 | seq/ack 差值计算若直接相减会在 debug 崩溃 | SDK 层先比较再相减；本设计在文档中明确约束 |
| `Data` 与 Kotlin `String` 不一致 | 双端对照时字段类型不同 | D3 已确认；wire 层完全等价 |
| 严格枚举遇到新值丢帧 | 新特性上线但客户端未升级时该帧不可用 | 协议变更走三方评审；必要时改 `.unknown(String)` |
| Swift 生成器独立解析 Schema | 与 Kotlin 的解释可能漂移 | 两端 `--check` + 生成物入库 review + 人工字段对照清单；Schema 为 SSOT |
| 未知 config 字段无法回传 | 未来客户端能力协商可能受限 | 需要时在 Schema 中显式建模，不保留任意 JSON |
| Intel Mac 构建速度 | 测试反馈较慢 | DTO 包纯 Swift，规模小；主机侧 `swift test` 足够快 |

---

## 9. 实施步骤

> 实施结果（2026-10-19）：1–7 已完成。主机侧 `swift test` 37 passed；
> iPhone 16 Pro 模拟器 `xcodebuild test` 37 passed；`make check-swift-api-generated`
> up to date；Kotlin 只读校验 up to date，且 `scripts/generate_kotlin_api.py` 与
> `packages/sdk-kotlin` 零 diff。

1. 实现独立生成器 `scripts/generate_swift_api.py`（自带 Schema 解析与 Swift 渲染，
   不触碰 Kotlin 生成链）。
2. 创建 `packages/sdk-swift/Package.swift`，生成三个 Swift 文件。
3. 实现 `ProtocolJSON.swift`（codec 工厂、`MessageHeader`、`ProtocolDecodingError`）。
4. 编写测试与 fixtures，跑通 `swift test`。
5. 增加 Makefile 目标，跑通只读的 `generate_kotlin_api.py --check` 与
   `make check-swift-api-generated`。
6. 在 iPhone 16 Pro 模拟器上按 §6.2 跑一次 `xcodebuild test`。
7. 提交前自审：消息/字段/必填性/required-nullable/枚举值逐项对照 Kotlin 产物；
   确认 `scripts/generate_kotlin_api.py` 与 `packages/sdk-kotlin` 零 diff。
