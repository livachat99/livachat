import * as React from 'react'
import type { MeetingConfig } from '../config'
import { messages } from '../messages'
import {
  isValidParticipantName,
  isValidRoomName,
  normalizeRoomName,
  randomRoomId,
} from '../validation'
import styles from '../styles/home.module.css'

const NAME_STORAGE_KEY = 'livachat-meeting:display-name'

function readStoredName(): string {
  try {
    return localStorage.getItem(NAME_STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export type HomeProps = {
  config: MeetingConfig
  defaultRoomName?: string
  defaultParticipantName?: string
  onJoin: (roomName: string, participantName: string) => void | Promise<void>
}

export function Home({
  config,
  defaultRoomName,
  defaultParticipantName,
  onJoin,
}: HomeProps) {
  const [roomName, setRoomName] = React.useState(
    defaultRoomName ?? '',
  )
  const [participantName, setParticipantName] = React.useState(
    defaultParticipantName ?? readStoredName(),
  )
  const [errors, setErrors] = React.useState<{
    room?: string
    name?: string
  }>({})
  const [submitting, setSubmitting] = React.useState(false)
  const nameInputRef = React.useRef<HTMLInputElement>(null)

  React.useEffect(() => {
    if (defaultRoomName !== undefined) setRoomName(defaultRoomName)
  }, [defaultRoomName])

  React.useEffect(() => {
    if (defaultParticipantName !== undefined) {
      setParticipantName(defaultParticipantName)
    }
  }, [defaultParticipantName])

  const handleGenerate = () => {
    setRoomName(randomRoomId())
    setErrors((current) => ({ ...current, room: undefined }))
    nameInputRef.current?.focus()
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    // Do not submit while an IME composition is active.
    if (event.key === 'Enter' && event.nativeEvent.isComposing) {
      event.preventDefault()
    }
  }

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return

    const normalizedRoom = normalizeRoomName(roomName)
    const trimmedName = participantName.trim()
    const nextErrors: { room?: string; name?: string } = {}

    if (!normalizedRoom) {
      nextErrors.room = messages.meetingNumberRequired
    } else if (!isValidRoomName(normalizedRoom)) {
      nextErrors.room = messages.meetingNumberInvalid
    }

    if (!trimmedName) {
      nextErrors.name = messages.participantNameRequired
    } else if (!isValidParticipantName(trimmedName)) {
      nextErrors.name = messages.participantNameTooLong
    }

    setErrors(nextErrors)
    if (nextErrors.room || nextErrors.name) return

    setSubmitting(true)
    try {
      await onJoin(normalizedRoom, trimmedName)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className={styles.page}>
      <form className={styles.card} onSubmit={handleSubmit} noValidate>
        <header className={styles.header}>
          <h1 className={styles.title}>{config.appTitle}</h1>
          <p className={styles.subtitle}>{messages.homeSubtitle}</p>
        </header>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="meeting-number">
            {messages.meetingNumberLabel}
          </label>
          <div className={styles.roomRow}>
            <input
              id="meeting-number"
              className={styles.input}
              value={roomName}
              onChange={(event) => {
                setRoomName(event.target.value)
                setErrors((current) => ({ ...current, room: undefined }))
              }}
              onKeyDown={handleKeyDown}
              placeholder={messages.meetingNumberPlaceholder}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              inputMode="text"
              maxLength={64}
              autoFocus
              aria-invalid={Boolean(errors.room)}
              aria-describedby={errors.room ? 'meeting-number-error' : undefined}
            />
            <button
              type="button"
              className={`lk-button ${styles.randomButton}`}
              onClick={handleGenerate}
              title={messages.generateRoom}
              aria-label={messages.generateRoom}
            >
              🎲
            </button>
          </div>
          {errors.room ? (
            <p id="meeting-number-error" className={styles.error} role="alert">
              {errors.room}
            </p>
          ) : null}
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="participant-name">
            {messages.participantNameLabel}
          </label>
          <input
            id="participant-name"
            ref={nameInputRef}
            className={styles.input}
            value={participantName}
            onChange={(event) => {
              setParticipantName(event.target.value)
              setErrors((current) => ({ ...current, name: undefined }))
            }}
            onKeyDown={handleKeyDown}
            placeholder={messages.participantNamePlaceholder}
            autoComplete="name"
            aria-invalid={Boolean(errors.name)}
            aria-describedby={errors.name ? 'participant-name-error' : undefined}
          />
          {errors.name ? (
            <p id="participant-name-error" className={styles.error} role="alert">
              {errors.name}
            </p>
          ) : null}
        </div>

        <button
          type="submit"
          className={`lk-button ${styles.submit}`}
          disabled={submitting}
        >
          {submitting ? messages.joining : messages.joinMeeting}
        </button>

        {config.tokenMode === 'client' ? (
          <p className={styles.devWarning}>⚠ {messages.devWarning}</p>
        ) : null}
      </form>
    </main>
  )
}
