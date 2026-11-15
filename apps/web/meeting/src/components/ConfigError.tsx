import { messages } from '../messages'
import styles from '../styles/home.module.css'

export type FatalErrorProps = {
  title: string
  message: string
  hint?: string
}

export function FatalError({ title, message, hint }: FatalErrorProps) {
  return (
    <main className={`${styles.page} ${styles.light}`}>
      <section className={styles.card} role="alert">
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.error}>{message}</p>
        {hint ? <p className={styles.subtitle}>{hint}</p> : null}
      </section>
    </main>
  )
}

export function ConfigError({ message }: { message: string }) {
  return (
    <FatalError
      title={messages.configErrorTitle}
      message={message}
      hint={messages.configErrorHint}
    />
  )
}
