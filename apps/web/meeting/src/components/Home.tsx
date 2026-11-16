import * as React from 'react'
import type { MeetingConfig } from '../config'
import { useI18n } from '../i18n'
import {
  isValidMeetingNumber,
  isValidParticipantName,
  isValidRoomName,
  normalizeMeetingNumber,
  normalizeRoomName,
} from '../validation'
import { readStoredParticipantName, saveParticipantName } from '../storage'
import styles from '../styles/home.module.css'

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
  const { locale, messages } = useI18n()
  const footerSlogan =
    config.homepageSlogan[locale] ?? config.homepageSlogan.en ?? ''
  const [roomName, setRoomName] = React.useState(
    defaultRoomName ?? '',
  )
  const [participantName, setParticipantName] = React.useState(
    defaultParticipantName ?? readStoredParticipantName() ?? '',
  )
  const [errors, setErrors] = React.useState<{
    room?: string
    name?: string
  }>({})
  const [submitting, setSubmitting] = React.useState(false)
  const serverMode = config.tokenMode === 'server'
  // Touch devices must not autofocus (it pops the soft keyboard). Only a
  // fine pointer (desktop) focuses the first empty field on mount.
  const autoFocusEnabled = React.useMemo(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false
    }
    return !window.matchMedia('(pointer: coarse)').matches
  }, [])

  React.useEffect(() => {
    if (defaultRoomName !== undefined) setRoomName(defaultRoomName)
  }, [defaultRoomName])

  React.useEffect(() => {
    if (defaultParticipantName !== undefined) {
      setParticipantName(defaultParticipantName)
    }
  }, [defaultParticipantName])

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    // Do not submit while an IME composition is active.
    if (event.key === 'Enter' && event.nativeEvent.isComposing) {
      event.preventDefault()
    }
  }

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return

    const normalizedRoom = serverMode
      ? normalizeMeetingNumber(roomName)
      : normalizeRoomName(roomName)
    const trimmedName = participantName.trim()
    const nextErrors: { room?: string; name?: string } = {}

    if (!normalizedRoom) {
      nextErrors.room = messages.meetingNumberRequired
    } else if (
      serverMode
        ? !isValidMeetingNumber(normalizedRoom)
        : !isValidRoomName(normalizedRoom)
    ) {
      nextErrors.room = serverMode
        ? messages.meetingNumberFormat
        : messages.meetingNumberInvalid
    }

    if (!trimmedName) {
      nextErrors.name = messages.participantNameRequired
    } else if (!isValidParticipantName(trimmedName)) {
      nextErrors.name = messages.participantNameTooLong
    }

    setErrors(nextErrors)
    if (nextErrors.room || nextErrors.name) return

    saveParticipantName(trimmedName)
    setSubmitting(true)
    try {
      await onJoin(normalizedRoom, trimmedName)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className={`${styles.page} ${styles.light} ${styles.home}`}>
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
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              inputMode={serverMode ? 'numeric' : 'text'}
              maxLength={serverMode ? 12 : 64}
              autoFocus={autoFocusEnabled && !roomName}
              aria-invalid={Boolean(errors.room)}
              aria-describedby={errors.room ? 'meeting-number-error' : undefined}
            />
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
            className={styles.input}
            value={participantName}
            onChange={(event) => {
              setParticipantName(event.target.value)
              setErrors((current) => ({ ...current, name: undefined }))
            }}
            onKeyDown={handleKeyDown}
            autoComplete="name"
            autoFocus={
              autoFocusEnabled && Boolean(roomName) && !participantName
            }
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
      {footerSlogan && config.homepageLink ? (
        <footer className={styles.footer}>
          <a
            href={config.homepageLink}
            target="_blank"
            rel="noopener noreferrer"
          >
            {footerSlogan}
          </a>
        </footer>
      ) : null}
    </main>
  )
}
