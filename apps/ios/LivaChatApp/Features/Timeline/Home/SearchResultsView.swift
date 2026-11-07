import SwiftUI

struct SearchResultsView: View {
    let hits: [SearchHitDTO]
    let total: Int
    let query: String
    let onHitClick: (SearchHitDTO) -> Void
    let onLoadMore: () -> Void

    private struct Group: Identifiable {
        let date: String
        let hits: [SearchHitDTO]
        var id: String { date }
    }

    private var groups: [Group] {
        var order: [String] = []
        var byDate: [String: [SearchHitDTO]] = [:]
        for hit in hits {
            if byDate[hit.date] == nil { order.append(hit.date) }
            byDate[hit.date, default: []].append(hit)
        }
        return order.map { Group(date: $0, hits: byDate[$0] ?? []) }
    }

    var body: some View {
        VStack(spacing: 0) {
            Text(L10n.plural("mylife_search_total", total))
                .mylifeFont(.label)
                .foregroundColor(MylifePalette.dust)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, AppSpacing.m)
                .padding(.vertical, AppSpacing.xs)
                .accessibilityIdentifier("timeline_search_total")

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(groups) { group in
                        Text(MylifeDateLabels.searchDayLabel(forDateKey: group.date))
                            .mylifeFont(.cardTitle)
                            .foregroundColor(MylifePalette.star)
                            .padding(.horizontal, AppSpacing.m)
                            .padding(.vertical, AppSpacing.xs)
                            .accessibilityAddTraits(.isHeader)

                        ForEach(group.hits) { hit in
                            Button {
                                onHitClick(hit)
                            } label: {
                                row(hit)
                            }
                            .buttonStyle(.plain)
                            .onAppear {
                                if group.id == groups.last?.id, hit.id == group.hits.last?.id {
                                    onLoadMore()
                                }
                            }
                        }
                    }
                }
                .padding(.bottom, AppSpacing.xxl)
            }
        }
    }

    private func row(_ hit: SearchHitDTO) -> some View {
        HStack(alignment: .top, spacing: AppSpacing.s) {
            Text(kindIcon(hit.kind))
                .mylifeFont(.body)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text(hit.tsMs.map { MylifeDateLabels.timeLabel(tsMs: $0) } ?? "")
                    .mylifeFont(.label)
                    .foregroundColor(MylifePalette.dust)
                highlighted(hit.snippet)
                    .mylifeFont(.body)
                    .foregroundColor(MylifePalette.star)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, AppSpacing.m)
        .padding(.vertical, AppSpacing.s)
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            LocalizedList.join([
                kindName(hit.kind),
                hit.tsMs.map { MylifeDateLabels.timeLabel(tsMs: $0) },
                hit.snippet,
            ])
        )
        .accessibilityAddTraits(.isButton)
        .accessibilityIdentifier("timeline_search_hit_\(hit.id)")
    }

    private func highlighted(_ snippet: String) -> Text {
        guard !query.isEmpty,
              let range = snippet.range(of: query, options: [.caseInsensitive]) else {
            return Text(snippet)
        }
        return Text(String(snippet[..<range.lowerBound]))
            + Text(String(snippet[range])).foregroundColor(MylifePalette.gold)
            + Text(String(snippet[range.upperBound...]))
    }

    private func kindIcon(_ kind: String) -> String {
        switch kind {
        case "chat": return "💬"
        case "transcribe": return "📄"
        case "translate": return "🌐"
        case "agent_job": return "🤖"
        case "video": return "🖼️"
        default: return "📍"
        }
    }

    private func kindName(_ kind: String) -> String {
        switch kind {
        case "chat": return L10n.t("search_kind_chat")
        case "transcribe": return L10n.t("search_kind_transcribe")
        case "translate": return L10n.t("search_kind_translate")
        case "agent_job": return L10n.t("conv_agent_header")
        case "video": return L10n.t("search_kind_video")
        default: return L10n.t("search_kind_event")
        }
    }
}
