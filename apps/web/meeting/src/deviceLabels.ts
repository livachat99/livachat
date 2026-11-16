import type { Messages } from './i18n'

type MediaDeviceFallback = 'audioinput' | 'audiooutput' | 'videoinput'

const FALLBACK_KINDS: readonly MediaDeviceFallback[] = [
  'audioinput',
  'audiooutput',
  'videoinput',
]

function isFallbackKind(
  kind: MediaDeviceInfo['kind'],
): kind is MediaDeviceFallback {
  return FALLBACK_KINDS.includes(kind as MediaDeviceFallback)
}

function fallbackLabel(
  kind: MediaDeviceFallback,
  index: number,
  messages: Messages,
): string {
  const base =
    kind === 'videoinput'
      ? messages.settingsCamera
      : kind === 'audioinput'
        ? messages.settingsMicrophone
        : messages.settingsSpeaker
  return `${base} ${index}`
}

/**
 * Android WebView can expose devices with empty labels even after camera /
 * microphone permission was granted. LiveKit's MediaDeviceMenu renders the
 * labels verbatim, so the dropdown looks empty. Give every unlabeled device a
 * stable, localized fallback; labeled devices are left untouched.
 *
 * When `grantedKinds` is provided, only labels for kinds whose permission has
 * already been acquired are replaced. Before permission is granted, browsers
 * return placeholder devices with empty labels and empty device IDs; masking
 * those labels would make LiveKit's permission probe in DeviceManager.getDevices
 * skip getUserMedia, leaving a menu that looks selectable but cannot work.
 */
export function addDeviceLabelFallbacks(
  devices: MediaDeviceInfo[],
  messages: Messages,
  grantedKinds?: ReadonlySet<string>,
): MediaDeviceInfo[] {
  const counts: Record<MediaDeviceFallback, number> = {
    audioinput: 0,
    audiooutput: 0,
    videoinput: 0,
  }
  return devices.map((device) => {
    if (!isFallbackKind(device.kind)) return device
    if (grantedKinds && !grantedKinds.has(device.kind)) return device
    const label = typeof device.label === 'string' ? device.label.trim() : ''
    if (label) return device
    counts[device.kind] += 1
    return {
      deviceId: device.deviceId,
      groupId: device.groupId,
      kind: device.kind,
      label: fallbackLabel(device.kind, counts[device.kind], messages),
    } as MediaDeviceInfo
  })
}

let installed = false

function markGranted(
  constraints: MediaStreamConstraints,
  grantedKinds: Set<MediaDeviceFallback>,
): void {
  try {
    if (constraints.audio) {
      grantedKinds.add('audioinput')
      // Audio output labels are exposed with the same audio permission in the
      // affected WebView; keep the menu usable once audio was acquired.
      grantedKinds.add('audiooutput')
    }
    if (constraints.video) {
      grantedKinds.add('videoinput')
    }
  } catch {
    // Constraints can be exotic objects in third-party code. Never let
    // bookkeeping turn a successful getUserMedia into a rejection.
  }
}

/** Install once, before the React app / LiveKit mounts. */
export function installDeviceLabelFallback(messages: Messages): void {
  if (
    installed ||
    typeof navigator === 'undefined' ||
    typeof navigator.mediaDevices?.enumerateDevices !== 'function' ||
    typeof navigator.mediaDevices.getUserMedia !== 'function'
  ) {
    return
  }
  const mediaDevices = navigator.mediaDevices
  const grantedKinds = new Set<MediaDeviceFallback>()
  const originalEnumerate = mediaDevices.enumerateDevices.bind(mediaDevices)
  const originalGetUserMedia = mediaDevices.getUserMedia.bind(mediaDevices)

  const wrappedEnumerate = async () =>
    addDeviceLabelFallbacks(await originalEnumerate(), messages, grantedKinds)
  const wrappedGetUserMedia: typeof mediaDevices.getUserMedia = async (
    constraints,
  ) => {
    const stream = await originalGetUserMedia(constraints)
    if (constraints) markGranted(constraints, grantedKinds)
    return stream
  }

  const enumerateDescriptor = Object.getOwnPropertyDescriptor(
    mediaDevices,
    'enumerateDevices',
  )
  const getUserMediaDescriptor = Object.getOwnPropertyDescriptor(
    mediaDevices,
    'getUserMedia',
  )
  let enumeratePatched = false
  let getUserMediaPatched = false
  try {
    Object.defineProperty(mediaDevices, 'getUserMedia', {
      configurable: true,
      writable: true,
      value: wrappedGetUserMedia,
    })
    getUserMediaPatched = true
    Object.defineProperty(mediaDevices, 'enumerateDevices', {
      configurable: true,
      writable: true,
      value: wrappedEnumerate,
    })
    enumeratePatched = true
    installed = true
  } catch {
    // Some embedded engines make the methods non-configurable. Restore any
    // partially applied patch and leave the native implementations in place;
    // preserving media/permission behavior is more important than labels.
    if (enumeratePatched) {
      restore(mediaDevices, 'enumerateDevices', enumerateDescriptor)
    }
    if (getUserMediaPatched) {
      restore(mediaDevices, 'getUserMedia', getUserMediaDescriptor)
    }
    installed = false
  }
}

function restore(
  mediaDevices: MediaDevices,
  key: 'enumerateDevices' | 'getUserMedia',
  descriptor: PropertyDescriptor | undefined,
): void {
  try {
    if (descriptor) {
      Object.defineProperty(mediaDevices, key, descriptor)
    } else {
      delete (mediaDevices as unknown as Record<string, unknown>)[key]
    }
  } catch {
    // Ignore cleanup failures; the native method may live on the prototype.
  }
}
