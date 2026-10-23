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

/** The tab's events: in its session storage, so that a reload keeps them and another tab has its own. */
export function storedEvents(): LoggedEvent[] {
  try {
    const kept = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? '[]') as unknown
    return Array.isArray(kept) ? (kept as LoggedEvent[]).slice(0, MAX_LOGGED) : []
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
