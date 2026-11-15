import { messages } from '../messages'
import styles from '../styles/home.module.css'

export function Loading() {
  return (
    <main className={`${styles.page} ${styles.light}`}>
      <p className={styles.subtitle}>{messages.waitingForConfig}</p>
    </main>
  )
}
