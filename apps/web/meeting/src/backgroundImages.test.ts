import { describe, expect, it } from 'vitest'
import {
  resolveBackgroundImageUrl,
  toBackgroundImageOptions,
} from './backgroundImages'

const origin = 'https://meeting.example.test'

describe('resolveBackgroundImageUrl', () => {
  it('resolves relative paths against the app base', () => {
    expect(
      resolveBackgroundImageUrl(
        'backgrounds/office.jpg',
        '/meeting/',
        origin,
      ),
    ).toBe('https://meeting.example.test/meeting/backgrounds/office.jpg')
  })

  it('keeps absolute, root-relative and data URLs', () => {
    expect(
      resolveBackgroundImageUrl(
        'https://cdn.example.test/office.jpg',
        '/meeting/',
        origin,
      ),
    ).toBe('https://cdn.example.test/office.jpg')
    expect(
      resolveBackgroundImageUrl(
        '/backgrounds/office.jpg',
        '/meeting/',
        origin,
      ),
    ).toBe('https://meeting.example.test/backgrounds/office.jpg')
    expect(
      resolveBackgroundImageUrl(
        'data:image/svg+xml,%3Csvg%3E%3C/svg%3E',
        '/meeting/',
        origin,
      ),
    ).toBe('data:image/svg+xml,%3Csvg%3E%3C/svg%3E')
  })
})

describe('toBackgroundImageOptions', () => {
  it('builds UI paths and falls back to the full image for thumbnails', () => {
    expect(
      toBackgroundImageOptions(
        [
          {
            label: 'Office',
            image: 'backgrounds/office.jpg',
            thumbnail: 'backgrounds/office-thumb.jpg',
          },
          {
            label: 'Library',
            image: 'https://cdn.example.test/library.jpg',
          },
        ],
        '/meeting/',
        origin,
      ),
    ).toEqual([
      {
        name: 'Office',
        path: 'https://meeting.example.test/meeting/backgrounds/office.jpg',
        thumbnail:
          'https://meeting.example.test/meeting/backgrounds/office-thumb.jpg',
      },
      {
        name: 'Library',
        path: 'https://cdn.example.test/library.jpg',
        thumbnail: 'https://cdn.example.test/library.jpg',
      },
    ])
  })
})
