import {
  ChatCloseIcon,
  ChatToggle,
  useChat,
  useMaybeLayoutContext,
  type ChatEntryProps,
  type ChatMessage,
  type ChatProps,
} from '@livekit/components-react'
import * as React from 'react'
import { useMessages } from '../../i18n'
import { LocalizedChatEntry } from './LocalizedChatEntry'

/** Derived from @livekit/components-react@2.9.24 Chat. */
export function LocalizedChat({
  messageFormatter,
  messageDecoder,
  messageEncoder,
  channelTopic,
  ...props
}: ChatProps) {
  const messages = useMessages()
  const ulRef = React.useRef<HTMLUListElement>(null)
  const inputRef = React.useRef<HTMLInputElement>(null)

  const chatOptions = React.useMemo(() => {
    return { messageDecoder, messageEncoder, channelTopic }
  }, [messageDecoder, messageEncoder, channelTopic])

  const { chatMessages, send, isSending } = useChat(chatOptions)
  const layoutContext = useMaybeLayoutContext()
  const lastReadMsgAt = React.useRef<ChatMessage['timestamp']>(0)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (inputRef.current && inputRef.current.value.trim() !== '') {
      await send(inputRef.current.value)
      inputRef.current.value = ''
      inputRef.current.focus()
    }
  }

  React.useEffect(() => {
    ulRef.current?.scrollTo({ top: ulRef.current.scrollHeight })
  }, [ulRef, chatMessages])

  React.useEffect(() => {
    if (!layoutContext || chatMessages.length === 0) return

    if (
      layoutContext.widget.state?.showChat &&
      chatMessages.length > 0 &&
      lastReadMsgAt.current !== chatMessages[chatMessages.length - 1]?.timestamp
    ) {
      lastReadMsgAt.current = chatMessages[chatMessages.length - 1]?.timestamp
      return
    }

    const unreadMessageCount = chatMessages.filter(
      (msg) => !lastReadMsgAt.current || msg.timestamp > lastReadMsgAt.current,
    ).length

    const { widget } = layoutContext
    if (
      unreadMessageCount > 0 &&
      widget.state?.unreadMessages !== unreadMessageCount
    ) {
      widget.dispatch?.({ msg: 'unread_msg', count: unreadMessageCount })
    }
  }, [chatMessages, layoutContext?.widget])

  const customEntry = props.children
    ? (React.Children.only(props.children) as React.ReactElement<ChatEntryProps>)
    : null

  return (
    <div {...props} className="lk-chat">
      <div className="lk-chat-header">
        {messages.chatMessages}
        {layoutContext && (
          <ChatToggle
            className="lk-close-button"
            aria-label={messages.chatClose}
            title={messages.chatClose}
          >
            <ChatCloseIcon />
          </ChatToggle>
        )}
      </div>

      <ul className="lk-list lk-chat-messages" ref={ulRef}>
        {customEntry
          ? chatMessages.map((msg, idx) =>
              React.cloneElement(customEntry, {
                entry: msg,
                key: msg.id ?? idx,
                messageFormatter,
              }),
            )
          : chatMessages.map((msg, idx, allMsg) => {
              const hideName = idx >= 1 && allMsg[idx - 1].from === msg.from
              const hideTimestamp =
                idx >= 1 &&
                msg.timestamp - allMsg[idx - 1].timestamp < 60_000

              return (
                <LocalizedChatEntry
                  key={msg.id ?? idx}
                  hideName={hideName}
                  hideTimestamp={hideName === false ? false : hideTimestamp}
                  entry={msg}
                  messageFormatter={messageFormatter}
                />
              )
            })}
      </ul>
      <form
        className="lk-chat-form"
        aria-label={messages.chatMessages}
        onSubmit={handleSubmit}
      >
        <input
          className="lk-form-control lk-chat-form-input"
          disabled={isSending}
          ref={inputRef}
          type="text"
          placeholder={messages.chatPlaceholder}
          onInput={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
          onKeyUp={(event) => event.stopPropagation()}
        />
        <button
          type="submit"
          className="lk-button lk-chat-form-button"
          disabled={isSending}
        >
          {messages.chatSend}
        </button>
      </form>
    </div>
  )
}
