import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { EventLogContext, MAX_LOGGED, storedEvents, storeEvents } from './eventLog'
import type { EventLog, LoggedEvent } from './eventLog'
import type { EventNotice } from './liveEvents'

/** Keeps the events of the tab for the bell, newest first. */
export function EventLogProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<LoggedEvent[]>(storedEvents)
  const counter = useRef(0)

  useEffect(() => storeEvents(events), [events])

  const add = useCallback((notice: EventNotice, href: string | null) => {
    counter.current += 1
    const at = new Date().toISOString()
    const logged: LoggedEvent = { ...notice, id: `${at}-${counter.current}`, at, href, read: false }
    setEvents((current) => [logged, ...current].slice(0, MAX_LOGGED))
  }, [])
  const markRead = useCallback(
    (id: string) => setEvents((current) => current.map((event) => (event.id === id ? { ...event, read: true } : event))),
    [],
  )
  const markAllRead = useCallback(() => setEvents((current) => current.map((event) => ({ ...event, read: true }))), [])

  const log = useMemo<EventLog>(() => ({ events, add, markRead, markAllRead }), [events, add, markRead, markAllRead])
  return <EventLogContext.Provider value={log}>{children}</EventLogContext.Provider>
}
