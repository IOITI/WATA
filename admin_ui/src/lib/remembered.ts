// The palettes that keep the entries chosen last in this browser (ioiti-ui's SpotlightSearch `remember`): ⌘K's and
// the AI agent's attach picker. Kept under the application's theme key (`wata.theme.spotlight.<name>`) until the
// operator signs out: a sign-out forgets them, a session that lapsed keeps them.

import { themeStorageKey } from 'ioiti-ui'

export const REMEMBERED = { goTo: 'go-to', attach: 'attach' } as const

/** Forgets what every palette kept: the browser may be someone else's next. */
export function forgetRemembered(): void {
  for (const name of Object.values(REMEMBERED)) {
    try {
      window.localStorage.removeItem(`${themeStorageKey()}.spotlight.${name}`)
    } catch {
      // A browser that keeps nothing has nothing to forget.
    }
  }
}
