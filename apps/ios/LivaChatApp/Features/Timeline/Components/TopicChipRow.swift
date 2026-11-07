import SwiftUI

struct TopicChipRow: View {
    let topics: [MylifeDayTopic]
    var onTopicClick: ((MylifeDayTopic) -> Void)? = nil

    var body: some View {
        MylifeFlowLayout(items: topics) { topic in
            if let onTopicClick {
                Button {
                    onTopicClick(topic)
                } label: {
                    chip(topic.topic)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(topic.topic)
                .accessibilityAddTraits(.isButton)
            } else {
                chip(topic.topic)
                    .accessibilityLabel(topic.topic)
            }
        }
    }

    private func chip(_ text: String) -> some View {
        Text(text)
            .mylifeFont(.label)
            .foregroundColor(MylifePalette.dust)
            .padding(.horizontal, AppSpacing.s)
            .padding(.vertical, 2)
            .overlay {
                Capsule()
                    .stroke(MylifePalette.line, lineWidth: 1)
            }
            .clipShape(Capsule())
    }
}
