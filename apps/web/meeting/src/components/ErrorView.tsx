import * as React from 'react'
import { messages } from '../messages'
import styles from '../styles/home.module.css'

export type ErrorViewProps = {
  message: string
  onRetry: () => void | Promise<void>
  onBack: () => void
}

function sanitizeMessage(message: string): string {
  const withoutJwt = message.replace(
    /eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g,
    '<redacted-token>',
  )
  const withoutSecret = withoutJwt.replace(
    /(apiSecret|secret)=[^&\s]+/gi,
    '$1=<redacted>',
  )
  return withoutSecret.length > 300
    ? `${withoutSecret.slice(0, 300)}...`
    : withoutSecret
}

export function ErrorView({ message, onRetry, onBack }: ErrorViewProps) {
  const [retrying, setRetrying] = React.useState(false)

  const handleRetry = async () => {
    if (retrying) return
    setRetrying(true)
    try {
      await onRetry()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.card} role="alert">
        <h1 className={styles.title}>{messages.couldNotJoin}</h1>
        <p className={styles.error}>{sanitizeMessage(message)}</p>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            type="button"
            className="lk-button"
            style={{ flex: 1 }}
            onClick={() => void handleRetry()}
            disabled={retrying}
          >
            {retrying ? messages.joining : messages.retry}
          </button>
          <button
            type="button"
            className="lk-button"
            onClick={onBack}
            disabled={retrying}
          >
            {messages.back}
          </button>
        </div>
      </section>
    </main>
  )
}
