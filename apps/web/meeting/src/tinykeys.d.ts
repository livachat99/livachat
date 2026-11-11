// tinykeys@3 ships types at dist/tinykeys.d.ts but its package.json "exports"
// does not expose a "types" condition, so TypeScript with moduleResolution
// "bundler" cannot resolve them. Keep a minimal declaration for our usage.
declare module 'tinykeys' {
  export type KeyBindingMap = Record<string, (event: KeyboardEvent) => void>
  export type KeyBindingOptions = {
    timeout?: number
    event?: 'keydown' | 'keyup'
    capture?: boolean
  }
  export function tinykeys(
    target: Window | HTMLElement,
    keyBindingMap: KeyBindingMap,
    options?: KeyBindingOptions,
  ): () => void
}
