import { Alert, AlertDescription, AlertTitle, Button, formatDateTime } from 'ioiti-ui'
import { useState } from 'react'

import { PauseDialog } from './PauseDialog'

interface PauseControlProps {
  pausedSince: string | null
  /** Set when the account guard paused entries (src/trade/account_guard.py), not the operator. */
  pausedByGuard?: string | null
  timeZone: string
  actionsEnabled: boolean
}

/**
 * Stops the trader opening positions without stopping anything else: closing
 * signals, the end-of-day close and the position monitor's exits keep working.
 */
export function PauseControl({ pausedSince, pausedByGuard = null, timeZone, actionsEnabled }: PauseControlProps) {
  const [confirming, setConfirming] = useState(false)
  const paused = pausedSince !== null

  const open = () => setConfirming(true)

  return (
    <>
      {paused ? (
        <Alert
          tone="warning"
          action={
            actionsEnabled && (
              <Button variant="outline" size="sm" onClick={open}>
                Resume entries
              </Button>
            )
          }
        >
          <AlertTitle>{pausedByGuard ? 'New entries are paused by the account guard' : 'New entries are paused'}</AlertTitle>
          <AlertDescription>
            Since {formatDateTime(pausedSince, timeZone, false)}.{' '}
            {pausedByGuard &&
              'The account fell under the guard’s floor, or that far under its highest since entries were last resumed: Telegram said by how much. '}
            Signals that would open a position are refused; open positions are still monitored and closed as usual.
          </AlertDescription>
        </Alert>
      ) : (
        actionsEnabled && (
          <Button variant="outline" size="sm" onClick={open}>
            Pause new entries
          </Button>
        )
      )}

      <PauseDialog paused={paused} open={confirming} onClose={() => setConfirming(false)} />
    </>
  )
}
