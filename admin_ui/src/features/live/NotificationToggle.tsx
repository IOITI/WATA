import { useToast } from 'ioiti-ui'
import { Bell, BellOff } from 'lucide-react'
import { useState } from 'react'

import { notificationsEnabled, notificationsSupported, setNotificationsEnabled } from './notifications'

/** Browser notifications for what happens while the tab is in the background. */
export function NotificationToggle() {
  const toast = useToast()
  const [enabled, setEnabled] = useState(notificationsEnabled)

  if (!notificationsSupported()) return null

  const toggle = async () => {
    const next = await setNotificationsEnabled(!enabled)
    setEnabled(next)
    if (!enabled && !next) {
      toast({
        title: 'Notifications are blocked',
        description: 'Allow them for this site in the browser settings, then try again.',
        variant: 'warning',
      })
    }
  }

  const label = enabled ? 'Turn browser notifications off' : 'Turn browser notifications on'
  return (
    <button
      type="button"
      onClick={() => void toggle()}
      aria-label={label}
      aria-pressed={enabled}
      title={label}
      className="grid size-9 shrink-0 place-items-center rounded-lg text-neutral-500 transition-colors hover:bg-neutral-200/70 hover:text-neutral-900 focus-ring dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-white"
    >
      {enabled ? <Bell className="size-[18px]" /> : <BellOff className="size-[18px]" />}
    </button>
  )
}
