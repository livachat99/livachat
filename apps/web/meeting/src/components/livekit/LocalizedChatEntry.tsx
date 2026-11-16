import type { ChatEntryProps } from '@livekit/components-react'
import * as React from 'react'
import { useI18n } from '../../i18n'

/** Derived from @livekit/components-react@2.9.24 ChatEntry. */
export function LocalizedChatEntry({
  entry,
  hideName = false,
  hideTimestamp = false,
  messageFormatter,
  ...props
}: ChatEntryProps) {
  const { messages, formattingLocale } = useI18n()
  const formattedMessage = React.useMemo(() => {
    return messageFormatter ? messageFormatter(entry.message) : entry.message
  }, [entry.message, messageFormatter])
  const hasBeenEdited = !!entry.editTimestamp
  const time = new Date(entry.timestamp)
  const name = entry.from?.name ?? entry.from?.identity

  return (
    <li
      className="lk-chat-entry"
      title={time.toLocaleTimeString(formattingLocale, { timeStyle: 'full' })}
      data-lk-message-origin={entry.from?.isLocal ? 'local' : 'remote'}
      {...props}
    >
      {(!hideTimestamp || !hideName || hasBeenEdited) && (
        <span className="lk-meta-data">
          {!hideName && <strong className="lk-participant-name">{name}</strong>}
          {(!hideTimestamp || hasBeenEdited) && (
            <span className="lk-timestamp">
              {hasBeenEdited && `${messages.chatEdited} `}
              {time.toLocaleTimeString(formattingLocale, {
                timeStyle: 'short',
              })}
            </span>
          )}
        </span>
      )}

      <span className="lk-message-body">{formattedMessage}</span>
      <span className="lk-message-attachements">
        {entry.attachedFiles?.map(
          (file) =>
            file.type.startsWith('image/') && (
              <img
                style={{ maxWidth: '300px', maxHeight: '300px' }}
                key={file.name}
                src={URL.createObjectURL(file)}
                alt={file.name}
              />
            ),
        )}
      </span>
    </li>
  )
}
