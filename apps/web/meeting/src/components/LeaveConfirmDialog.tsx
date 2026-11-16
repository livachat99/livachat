import * as React from 'react'
import { createPortal } from 'react-dom'
import { useMessages } from '../i18n'
import styles from './LeaveConfirmDialog.module.css'

export type LeaveConfirmDialogProps = {
  open: boolean
  onCancel: () => void
  onConfirm: () => void
}

const TITLE_ID = 'leave-confirm-title'
const MESSAGE_ID = 'leave-confirm-message'

export function LeaveConfirmDialog({
  open,
  onCancel,
  onConfirm,
}: LeaveConfirmDialogProps) {
  const messages = useMessages()
  const dialogRef = React.useRef<HTMLDivElement>(null)
  const cancelRef = React.useRef<HTMLButtonElement>(null)
  const previousFocusRef = React.useRef<HTMLElement | null>(null)

  React.useEffect(() => {
    if (!open) return
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
    cancelRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCancel()
        return
      }
      if (event.key !== 'Tab') return

      const dialog = dialogRef.current
      if (!dialog) return
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      previousFocusRef.current?.focus()
    }
  }, [open, onCancel])

  if (!open) return null

  return createPortal(
    <div
      className={styles.backdrop}
      onClick={(event) => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      <div
        ref={dialogRef}
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        aria-describedby={MESSAGE_ID}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={TITLE_ID} className={styles.title}>
          {messages.leaveConfirmTitle}
        </h2>
        <p id={MESSAGE_ID} className={styles.message}>
          {messages.leaveConfirmMessage}
        </p>
        <div className={styles.actions}>
          <button
            ref={cancelRef}
            type="button"
            className="lk-button"
            onClick={onCancel}
          >
            {messages.cancel}
          </button>
          <button
            type="button"
            className={`lk-button ${styles.confirm}`}
            onClick={onConfirm}
          >
            {messages.leave}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
