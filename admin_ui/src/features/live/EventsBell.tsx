import { formatDateTime, NotificationBell } from 'ioiti-ui'
import type { NotificationTone } from 'ioiti-ui'

import { HoverPeekRouterLink } from '@/components/PeekRouterLink'
import { useSession } from '@/features/auth/auth-context'

import { useEventLog } from './eventLog'
import type { LoggedEvent } from './eventLog'
import { NotificationToggle } from './NotificationToggle'
import { notificationsSupported } from './notifications'

const TONE: Record<LoggedEvent['variant'], NotificationTone> = {
  default: 'neutral',
  info: 'info',
  success: 'success',
  warning: 'warning',
  error: 'danger',
}

// As the top bar's other buttons.
const TRIGGER =
  'grid size-9 shrink-0 place-items-center rounded-lg text-neutral-500 hover:bg-neutral-200/70 hover:text-neutral-900 focus-ring dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white'

/**
 * What the live events told this tab, kept once their toasts are gone: each
 * leads to where it is dealt with. The browser notifications are switched here.
 */
export function EventsBell() {
  const log = useEventLog()
  const { timezone } = useSession()
  if (!log) return null
  return (
    <NotificationBell
      notifications={log.events.map((event) => ({
        id: event.id,
        message: event.title,
        detail: event.body,
        timestamp: formatDateTime(event.at, timezone),
        read: event.read,
        href: event.href ?? undefined,
        tone: TONE[event.variant],
      }))}
      onMarkAsRead={log.markRead}
      onMarkAllAsRead={log.markAllRead}
      linkComponent={HoverPeekRouterLink}
      triggerClassName={TRIGGER}
      footer={
        notificationsSupported() && (
          <div className="flex items-center justify-between gap-3">
            <span>As browser notifications too, while this tab is in the background</span>
            <NotificationToggle />
          </div>
        )
      }
    />
  )
}
