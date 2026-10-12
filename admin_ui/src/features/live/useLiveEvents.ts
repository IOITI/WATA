import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { useToast } from '@/components/toast-context'
import type { LiveEvent } from '@/lib/types'

import { describeEvent, LIVE_EVENT_TYPES, staleQueries } from './liveEvents'
import { notificationsEnabled } from './notifications'

/**
 * Listen to the server's event stream while a signed-in page is open: refresh
 * what an event made out of date, say what happened, and raise a browser
 * notification when the tab is not the one being looked at.
 *
 * Returns whether the stream is connected. The pages keep polling either way.
 */
export function useLiveEvents(): boolean {
  const queryClient = useQueryClient()
  const toast = useToast()
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    if (typeof EventSource === 'undefined') return

    const source = new EventSource('/api/events')
    source.addEventListener('connected', () => setConnected(true))
    // The browser reconnects by itself; this only reflects the state meanwhile.
    source.addEventListener('error', () => setConnected(false))

    const handle = (message: MessageEvent<string>) => {
      let event: LiveEvent
      try {
        event = JSON.parse(message.data) as LiveEvent
      } catch {
        return
      }
      for (const key of staleQueries(event)) void queryClient.invalidateQueries({ queryKey: [key] })

      const notice = describeEvent(event)
      if (!notice) return
      toast({ title: notice.title, description: notice.body, variant: notice.variant })
      if (document.visibilityState !== 'visible' && notificationsEnabled()) {
        new Notification(`WATA: ${notice.title}`, { body: notice.body, tag: event.type })
      }
    }
    for (const type of LIVE_EVENT_TYPES) source.addEventListener(type, handle)

    return () => {
      source.close()
      setConnected(false)
    }
  }, [queryClient, toast])

  return connected
}
