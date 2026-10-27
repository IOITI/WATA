import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Button, formatDateTime, useToast } from 'ioiti-ui'
import { useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { api } from '@/lib/api'

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
  const toast = useToast()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const paused = pausedSince !== null

  const mutation = useMutation({
    mutationFn: paused ? api.resumeEntries : api.pauseEntries,
    onSuccess: () => {
      setConfirming(false)
      toast({
        title: paused ? 'New entries resumed' : 'New entries paused',
        description: paused ? 'Signals open positions again.' : 'The trader refuses signals that would open a position.',
        variant: 'success',
      })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
  })

  const open = () => {
    mutation.reset()
    setConfirming(true)
  }

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

      <ConfirmLiveAction
        open={confirming}
        title={paused ? 'Resume new entries' : 'Pause new entries'}
        // Pausing only holds things back, and may be wanted in a hurry: one
        // click. Resuming lets signals open positions again: the slide.
        gesture={paused ? 'slide' : 'button'}
        confirmLabel={paused ? 'Slide to resume' : 'Pause new entries'}
        doneLabel="Resumed"
        warning={
          paused
            ? 'From now on, long and short signals open positions again.'
            : 'Nothing is closed and nothing is stopped: only new positions are held back.'
        }
        pending={mutation.isPending}
        error={mutation.error?.message}
        onConfirm={() => mutation.mutate()}
        onClose={() => setConfirming(false)}
        description={
          paused ? (
            <>The trader goes back to applying its usual rules to every signal.</>
          ) : (
            <>
              Until resumed, the trader refuses every <strong>long</strong> and <strong>short</strong> signal, from
              TradingView or sent from here. Closing signals, the end-of-day close, the stop-loss, take-profit and
              trailing stop all keep working.
            </>
          )
        }
      />
    </>
  )
}
