# iOS 本地第三方依赖

本目录存放 LivaChatApp 的 Markdown 渲染依赖，构建时**不再从网络解析或下载 SPM 包**。

## 清单

| 目录 | 上游 | 版本 | revision | 许可证 |
|---|---|---|---|---|
| `MarkdownUI/` | https://github.com/gonzalezreal/swift-markdown-ui | 2.4.1 | `5f613358148239d0292c0cef674a3c2314737f9e` | MIT（`LICENSE`） |
| `NetworkImage/` | https://github.com/gonzalezreal/NetworkImage | 6.0.1 | `2849f5323265386e200484b0d0f896e73c3411b9` | MIT（`LICENSE`） |
| `swift-cmark/` | https://github.com/swiftlang/swift-cmark | 0.9.0 | `08ddb528923cc1a6527e02b7a1aee9e516ca749a` | BSD-2-Clause（`COPYING`） |

工程通过 `LivaChatApp.xcodeproj` 的 `XCLocalSwiftPackageReference` 引用 `MarkdownUI`；MarkdownUI 的 `Package.swift` 再以相对路径引用另外两个包。

## 本地修改

- `MarkdownUI/Package.swift`：
  - `NetworkImage`、`swift-cmark` 从远程 URL 改为 `.package(path: "../NetworkImage")` / `.package(path: "../swift-cmark")`；
  - 删除上游的 `swift-snapshot-testing` 依赖与 `MarkdownUITests` test target（我们不需要跑上游快照测试，且该依赖会触发联网解析）；
  - 未修改任何 `Sources` 代码。
- 已剔除各包的 `.git`、`.github`、`.swiftpm`、`Examples`（MarkdownUI）、`Demo`（NetworkImage）、`Tests`（MarkdownUI）等非运行期内容。
- `NetworkImage`、`swift-cmark` 的源码与 manifest 保持上游原样（仅剔除非运行期目录）。

工程中已无远程 SPM 包，因此不再需要 `Package.resolved`。

## 升级步骤

1. 从上游取目标 tag 的源码（例如 `git archive` 或临时 clone）。
2. 替换对应目录，保留 `LICENSE` / `COPYING`；剔除 `.git`、测试/示例目录。
3. **重新套用** `MarkdownUI/Package.swift` 的两处修改：本地 path 依赖、删除 snapshot-testing 与 testTarget。
4. 清理 DerivedData 后构建，并跑 iOS 单测/UI 测试与真机构建；确认无网络 Fetching。
5. 更新本文件的版本与 revision，提交依赖源码和工程改动。

## 校验

```bash
rm -rf /tmp/localdeps-check
xcodebuild -resolvePackageDependencies \
  -project apps/ios/LivaChatApp/LivaChatApp.xcodeproj \
  -scheme LivaChatApp \
  -derivedDataPath /tmp/localdeps-check
```

输出中不应出现 `Fetching` / `Cloning`；随后正常构建即可。`swift-markdown-ui` 的上游 README 仍带有远程安装示例，仅作文档参考，不代表本工程接入方式。
