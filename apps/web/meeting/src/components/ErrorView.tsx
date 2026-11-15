import { messages } from '../messages'
import styles from '../styles/home.module.css'

export type ErrorViewProps = {
  message: string
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

export function ErrorView({ message, onBack }: ErrorViewProps) {
  return (
    <main className={`${styles.page} ${styles.light}`}>
      <section className={styles.card} role="alert">
        <h1 className={styles.title} style={{ textAlign: 'center' }}>
          {messages.couldNotJoin}
        </h1>
        <p className={styles.error} style={{ textAlign: 'center' }}>
          {sanitizeMessage(message)}
        </p>
        <button
          type="button"
          className="lk-button"
          style={{ width: '100%' }}
          onClick={onBack}
        >
          {messages.back}
        </button>
      </section>
    </main>
  )
}
