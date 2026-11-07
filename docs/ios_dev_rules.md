# iOS 开发技术规范

> 状态：生效中（2026-10-19）
> 适用范围：`apps/ios/`、`packages/sdk-swift/` 及仓库内一切 iOS 相关代码与工程配置。
> 本规范为强制性要求；违反规范的改动不应合入。

## 1. 最低部署版本：iOS 15.0

- 所有 iOS target（App、SDK、扩展、单元/UI 测试）的最低部署版本不得低于 iOS 15.0：
  - Xcode 工程：`IPHONEOS_DEPLOYMENT_TARGET = 15.0`
  - Swift Package：`platforms: [.iOS(.v15)]`
- 仅在高版本可用的 API 必须使用 `if #available(iOS X, *)` 判断，并提供 iOS 15 下的降级实现。
- 未经批准，不得在普通功能开发中顺带提高最低部署版本。

## 2. 语言与 UI 框架：Swift + SwiftUI

- 业务逻辑、网络、状态管理、UI 一律默认使用 **Swift + SwiftUI**。
- **UIKit 仅在必要时使用**，典型场景：
  - SwiftUI 没有等价能力，必须通过 `UIViewRepresentable` / `UIViewControllerRepresentable` 桥接的系统能力；
  - 必须集成的第三方 SDK 只提供 UIKit 接口；
  - 已实测 SwiftUI 无法满足性能或行为要求。
- **Objective-C 仅在必要时使用**，典型场景：
  - 依赖只提供 Objective-C 接口；
  - C 库优先使用 Swift 的 C interop，确有必要时才引入 Objective-C 桥接。
- UIKit / Objective-C 的使用必须**事先获得批准**；未获批准的改动在 review 阶段应被拒绝。
- UIKit 代码必须封装在独立适配层（Representable/封装组件）内，不得扩散到业务层；
  业务模型、网络与状态层不得依赖 UIKit 类型。

## 3. 依赖管理：Swift Package Manager

- 新增第三方依赖默认使用 **SPM**。
- 本地或预编译库优先使用 **XCFramework**，通过 SPM binary target 或工程直接引用。
- **CocoaPods 仅在依赖不提供 SPM/XCFramework 集成方式时使用，且必须事先获得批准**。
- 禁止同一依赖同时通过 SPM 与 CocoaPods 引入。
- 新增依赖前应评估：能否用系统框架替代、维护活跃度、许可证、包体积、
  以及是否兼容 iOS 15。

## 4. 例外审批流程

- 在方案或 PR 中说明：
  1. 使用 UIKit / Objective-C / CocoaPods 的具体场景；
  2. 已评估的 SwiftUI / Swift / SPM 替代方案及不可行原因；
  3. 影响范围（模块、维护成本、包体积、最低版本）。
- 经项目负责人批准后方可实现；批准结论记录在方案或 PR 中。
- 例外仅对当次改动有效，不构成后续默认许可。

## 5. 执行与检查

- Code review 必须检查本规范。
- iOS CI 搭建后，需校验各 target 的 deployment target 与依赖引入方式。
- 协议 DTO 等生成产物必须由仓库脚本生成，禁止手改。
