import type { BackgroundImageConfig } from './config'

export type BackgroundImageOption = {
  name: string
  path: string
  thumbnail: string
}

export function resolveBackgroundImageUrl(
  value: string,
  baseUrl: string,
  origin: string,
): string {
  if (/^data:image\//i.test(value)) return value
  if (/^https?:\/\//i.test(value)) return value
  try {
    return new URL(value, new URL(baseUrl, origin)).href
  } catch {
    return value
  }
}

export function toBackgroundImageOptions(
  images: BackgroundImageConfig[],
  baseUrl: string,
  origin: string,
): BackgroundImageOption[] {
  return images.map((image) => ({
    name: image.label,
    path: resolveBackgroundImageUrl(image.image, baseUrl, origin),
    thumbnail: resolveBackgroundImageUrl(
      image.thumbnail || image.image,
      baseUrl,
      origin,
    ),
  }))
}
