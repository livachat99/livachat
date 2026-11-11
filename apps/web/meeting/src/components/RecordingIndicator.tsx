import { useIsRecording } from '@livekit/components-react'
import * as React from 'react'
import toast from 'react-hot-toast'

export function RecordingIndicator() {
  const isRecording = useIsRecording()
  const [wasRecording, setWasRecording] = React.useState(false)

  React.useEffect(() => {
    if (isRecording === wasRecording) return
    setWasRecording(isRecording)
    if (isRecording) {
      toast('This meeting is being recorded', {
        duration: 3000,
        icon: 'REC',
        position: 'top-center',
        className: 'lk-button',
        style: {
          backgroundColor: 'var(--lk-danger3)',
          color: 'var(--lk-fg)',
        },
      })
    }
  }, [isRecording, wasRecording])

  return (
    <div
      aria-hidden="true"
      style={{
        position: 'absolute',
        inset: 0,
        pointerEvents: 'none',
        boxShadow: isRecording ? 'var(--lk-danger3) 0 0 0 3px inset' : 'none',
      }}
    />
  )
}
