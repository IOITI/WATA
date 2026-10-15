import { useEffect } from 'react'
import { useBlocker } from 'react-router'

import { Button } from '@/components/ui/button'
import { SettleModal } from '@/components/ui/settle-modal'

interface LeaveGuardProps {
  /** There is something on this page that leaving would lose. */
  dirty: boolean
  /** What would be lost, as a sentence. */
  summary: string
}

/**
 * Asks before unsaved work is lost: when moving to another page of the
 * interface, and (in the browser's own words) on a reload or a closed tab.
 */
export function LeaveGuard({ dirty, summary }: LeaveGuardProps) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty &&
      nextLocation.pathname !== currentLocation.pathname &&
      // A session that ended sends the page to the login: nothing could be saved by staying.
      nextLocation.pathname !== '/login',
  )

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const stay = () => blocker.reset?.()

  return (
    <SettleModal
      open={blocker.state === 'blocked'}
      onClose={stay}
      title="Leave without saving?"
      size="sm"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={() => blocker.proceed?.()}>
            Leave and discard
          </Button>
          <Button size="sm" onClick={stay}>
            Stay on this page
          </Button>
        </>
      }
    >
      <p className="text-sm leading-relaxed text-text-secondary">{summary} Nothing of it has been saved.</p>
    </SettleModal>
  )
}
