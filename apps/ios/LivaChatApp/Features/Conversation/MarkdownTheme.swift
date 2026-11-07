import MarkdownUI
import SwiftUI

@MainActor
enum MarkdownThemeFactory {
    private static var cache: [Int: Theme] = [:]

    static func theme(bodyFontSize: CGFloat) -> Theme {
        let key = Int((bodyFontSize * 10).rounded())
        if let cached = cache[key] {
            return cached
        }
        let theme = make(bodyFontSize: bodyFontSize)
        cache[key] = theme
        return theme
    }

    static func make(bodyFontSize: CGFloat = 16) -> Theme {
        Theme()
            .text {
                FontSize(bodyFontSize)
                ForegroundColor(AppColor.textPrimary)
            }
            .link {
                ForegroundColor(AppColor.brandIndigo)
            }
            .strong {
                FontWeight(.semibold)
            }
            .code {
                FontFamilyVariant(.monospaced)
                FontSize(.em(0.88))
                ForegroundColor(AppColor.textPrimary)
                BackgroundColor(AppColor.surfaceVariant)
            }
            .heading1 { heading($0, bodyFontSize: bodyFontSize) }
            .heading2 { heading($0, bodyFontSize: bodyFontSize) }
            .heading3 { heading($0, bodyFontSize: bodyFontSize) }
            .heading4 { heading($0, bodyFontSize: bodyFontSize) }
            .heading5 { heading($0, bodyFontSize: bodyFontSize) }
            .heading6 { heading($0, bodyFontSize: bodyFontSize) }
            .paragraph { configuration in
                configuration.label
                    .fixedSize(horizontal: false, vertical: true)
                    .relativeLineSpacing(.em(0.5))
                    .markdownMargin(top: 0, bottom: 8)
            }
            .blockquote { configuration in
                HStack(spacing: 0) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(AppColor.surfaceVariant)
                        .relativeFrame(width: .em(0.22))
                    configuration.label
                        .relativePadding(.leading, length: .em(0.75))
                        .markdownTextStyle {
                            ForegroundColor(AppColor.textSecondary)
                        }
                }
                .fixedSize(horizontal: false, vertical: true)
                .markdownMargin(top: 0, bottom: 8)
            }
            .codeBlock { configuration in
                ScrollView(.horizontal, showsIndicators: false) {
                    configuration.label
                        .fixedSize(horizontal: false, vertical: true)
                        .relativeLineSpacing(.em(0.3))
                        .markdownTextStyle {
                            FontFamilyVariant(.monospaced)
                            FontSize(.em(0.85))
                            ForegroundColor(AppColor.textPrimary)
                        }
                        .padding(12)
                }
                .environment(\.layoutDirection, .leftToRight)
                .background(AppColor.surfaceVariant)
                .clipShape(RoundedRectangle(cornerRadius: AppRadius.s))
                .markdownMargin(top: 0, bottom: 8)
            }
            .listItem { configuration in
                configuration.label
                    .markdownMargin(top: .em(0.25))
            }
            .taskListMarker { configuration in
                Image(systemName: configuration.isCompleted ? "checkmark.square.fill" : "square")
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(AppColor.brandIndigo, AppColor.textSecondary)
                    .imageScale(.small)
                    .relativeFrame(minWidth: .em(1.5), alignment: .trailing)
            }
            .table { configuration in
                configuration.label
                    .fixedSize(horizontal: false, vertical: true)
                    .markdownTableBorderStyle(.init(color: AppColor.surfaceVariant))
                    .markdownTableBackgroundStyle(
                        .alternatingRows(AppColor.background, AppColor.surface.opacity(0.6))
                    )
                    .markdownMargin(top: 0, bottom: 8)
            }
            .tableCell { configuration in
                configuration.label
                    .markdownTextStyle {
                        if configuration.row == 0 {
                            FontWeight(.semibold)
                        }
                        BackgroundColor(nil)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.vertical, 6)
                    .padding(.horizontal, 8)
                    .relativeLineSpacing(.em(0.25))
            }
            .thematicBreak {
                Divider()
                    .overlay(AppColor.surfaceVariant)
                    .markdownMargin(top: 16, bottom: 16)
            }
    }

    private static func heading(
        _ configuration: BlockConfiguration,
        bodyFontSize: CGFloat
    ) -> some View {
        configuration.label
            .relativeLineSpacing(.em(0.5))
            .markdownMargin(top: 16, bottom: 8)
            .markdownTextStyle {
                FontWeight(.bold)
                FontSize(bodyFontSize)
            }
    }
}
