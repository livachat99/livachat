import { useTrackToggle } from '@livekit/components-react'
import { Track } from 'livekit-client'
import * as React from 'react'
import { isEditableTarget } from '../utils'

/**
 * Google Meet-style shortcuts. Shift+D is intentionally NOT handled here:
 * DebugMode owns it, otherwise both handlers toggle it twice.
 */
export function KeyboardShortcuts() {
  const { toggle: toggleMic } = useTrackToggle({
    source: Track.Source.Microphone,
  })
  const { toggle: toggleCamera } = useTrackToggle({
    source: Track.Source.Camera,
  })

  React.useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.repeat || isEditableTarget(event.target)) return
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return

      switch (event.key.toLowerCase()) {
        case 'd':
          event.preventDefault()
          void toggleMic()
          break
        case 'e':
          event.preventDefault()
          void toggleCamera()
          break
        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [toggleMic, toggleCamera])

  return null
}
