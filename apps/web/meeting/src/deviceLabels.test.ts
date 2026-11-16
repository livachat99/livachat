import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  addDeviceLabelFallbacks,
  installDeviceLabelFallback,
} from './deviceLabels'
import { messages as en } from './i18n/locales/en'
import { messages as zhHans } from './i18n/locales/zh-Hans'

function device(
  kind: MediaDeviceInfo['kind'],
  deviceId: string,
  label = '',
): MediaDeviceInfo {
  return {
    deviceId,
    groupId: 'group',
    kind,
    label,
  } as MediaDeviceInfo
}

describe('addDeviceLabelFallbacks', () => {
  it('labels empty devices per kind with stable indexes', () => {
    const result = addDeviceLabelFallbacks(
      [
        device('videoinput', 'cam-1'),
        device('videoinput', 'cam-2'),
        device('audioinput', 'mic-1'),
        device('audiooutput', 'speaker-1'),
      ],
      en,
    )
    expect(result.map((entry) => entry.label)).toEqual([
      'Camera 1',
      'Camera 2',
      'Microphone 1',
      'Speaker 1',
    ])
  })

  it('leaves non-empty labels untouched', () => {
    const labeled = device('videoinput', 'cam-1', 'Built-in camera')
    expect(addDeviceLabelFallbacks([labeled], en)[0]).toBe(labeled)
  })

  it('uses the selected locale', () => {
    const result = addDeviceLabelFallbacks(
      [device('videoinput', 'cam-1')],
      zhHans,
    )
    expect(result[0].label).toBe('摄像头 1')
  })

  it('leaves kinds without granted permission untouched', () => {
    const result = addDeviceLabelFallbacks(
      [device('videoinput', 'cam-1'), device('audioinput', 'mic-1')],
      en,
      new Set(['videoinput']),
    )
    expect(result.map((entry) => entry.label)).toEqual([
      'Camera 1',
      '',
    ])
  })

  it('ignores devices with an unknown kind instead of throwing', () => {
    const unknown = device('unknown' as MediaDeviceInfo['kind'], 'x')
    expect(addDeviceLabelFallbacks([unknown], en)[0]).toBe(unknown)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('installDeviceLabelFallback', () => {
  it('keeps placeholder labels until getUserMedia grants a kind', async () => {
    const mediaDevices = {
      enumerateDevices: async () => [device('videoinput', '')],
      getUserMedia: async () => ({}) as MediaStream,
    } as unknown as MediaDevices
    vi.stubGlobal('navigator', { mediaDevices })

    installDeviceLabelFallback(en)

    // Chrome returns empty labels/deviceIds before permission. LiveKit relies
    // on seeing that empty label to request getUserMedia.
    expect((await navigator.mediaDevices.enumerateDevices())[0].label).toBe('')

    await navigator.mediaDevices.getUserMedia({ video: true })

    expect((await navigator.mediaDevices.enumerateDevices())[0].label).toBe(
      'Camera 1',
    )
  })
})
