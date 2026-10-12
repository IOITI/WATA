import { vi } from 'vitest'

/**
 * A stand-in for the system's light or dark setting in tests: jsdom has no
 * `matchMedia`. `set` changes it the way a phone going dark in the evening does.
 */
export function stubSystemTheme(dark: boolean) {
  const listeners = new Set<() => void>()
  const query = {
    matches: dark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  }
  vi.stubGlobal('matchMedia', () => query)
  return {
    set(next: boolean) {
      query.matches = next
      for (const listener of listeners) listener()
    },
    /** How many parts of the page are listening for a change. */
    listening: () => listeners.size,
  }
}
