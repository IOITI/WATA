import { useCallback, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { VelocityToast } from '@/components/ui/velocity-toast'

import { ToastContext } from './toast-context'
import type { ShowToast, ToastMessage } from './toast-context'

/** Beyond this, the oldest waiting toasts are dropped: a burst must not take minutes to read out. */
const MAX_WAITING = 6

interface QueuedToast extends ToastMessage {
  id: number
}

function displayTime(toast: QueuedToast, waiting: number): number {
  if (toast.variant === 'error') return 8000
  // Others are waiting behind it: move along faster.
  return waiting > 0 ? 2500 : 4000
}

/**
 * Toasts are shown one at a time, each for its full time. Two things happening
 * at once (an action's result and the event it caused) are shown one after the
 * other rather than the second replacing the first.
 *
 * Bottom centre, above the bar of the configuration page: the one place where
 * no page has a control. Bottom right is where the buttons that save and the
 * paging of tables are; top right, the date ranges, the exports and the pause.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<QueuedToast[]>([])
  const nextId = useRef(0)

  const show = useCallback<ShowToast>((message) => {
    nextId.current += 1
    const toast = { ...message, id: nextId.current }
    setQueue((waiting) => [...waiting, toast].slice(-MAX_WAITING))
  }, [])

  const current = queue[0]

  return (
    <ToastContext.Provider value={show}>
      {children}
      {current && (
        <VelocityToast
          key={current.id}
          open
          onDismiss={() => setQueue((waiting) => waiting.filter((toast) => toast.id !== current.id))}
          title={current.title}
          description={current.description}
          variant={current.variant ?? 'default'}
          position="bottom-center"
          wrapperClassName="bottom-20"
          duration={displayTime(current, queue.length - 1)}
        />
      )}
    </ToastContext.Provider>
  )
}
