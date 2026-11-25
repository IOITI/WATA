import type { StatusTone } from 'ioiti-ui'
import { createContext, useContext } from 'react'

import type { EventNotice } from './liveEvents'

/** An event the operator was told of, kept for the bell: what the toast said, when, and where it leads. */
export interface LoggedEvent extends EventNotice {
  id: string
  /** When it arrived, ISO. */
  at: string
  href: string | null
  read: boolean
}

export interface EventLog {
  events: LoggedEvent[]
  add: (notice: EventNotice, href: string | null) => void
  markRead: (id: string) => void
  markAllRead: () => void
}

/** The newest kept, the older ones dropped. */
export const MAX_LOGGED = 50

const STORAGE_KEY = 'wata-admin-events'

/** The tones the events kept before v0.102.0 were drawn in (`variant`, ioiti-ui's names of before 1.0). */
const TONE_OF_VARIANT: Record<string, StatusTone> = {
  default: 'neutral',
  info: 'info',
  success: 'success',
  warning: 'warning',
  error: 'danger',
}

/** An event kept by this tab, its tone read from the `variant` an event kept before v0.102.0 has in its place. */
function kept(event: LoggedEvent & { variant?: string }): LoggedEvent {
  if (event.tone) return event
  const { variant, ...rest } = event
  return { ...rest, tone: TONE_OF_VARIANT[variant ?? ''] ?? 'neutral' }
}

/** The tab's events: in its session storage, so that a reload keeps them and another tab has its own. */
export function storedEvents(): LoggedEvent[] {
  try {
    const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? '[]') as unknown
    return Array.isArray(stored) ? (stored as LoggedEvent[]).slice(0, MAX_LOGGED).map(kept) : []
  } catch {
    return []
  }
}

export function storeEvents(events: LoggedEvent[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(events))
  } catch {
    // Storage refused (a private window, full): the bell still holds them until the tab closes.
  }
}

/** Where the shell keeps the tab's events: null outside it (the tests of a page alone). */
export const EventLogContext = createContext<EventLog | null>(null)

export function useEventLog(): EventLog | null {
  return useContext(EventLogContext)
}
