import type { MouseEvent } from 'react'

/**
 * A click that follows a link in this tab. One the browser opens elsewhere (with ⌘, Ctrl, Shift or Alt:
 * another tab or window) is left to it, and leaves the page as it is.
 */
export function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}
