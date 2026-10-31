// The confirmation of a pause of new entries, or of their resume: the dashboard's, and the AI agent's proposal of
// the same (its card), sent with the proposal it applies so that the audit trail says so.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useToast } from 'ioiti-ui'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { api } from '@/lib/api'

export function PauseDialog({ paused, open, onClose, proposal, onDone, onRefused }: {
  /** Whether entries are paused now: the dialog resumes them. */
  paused: boolean
  open: boolean
  onClose: () => void
  /** The AI agent's proposal this applies (<conversation>/<proposal>). */
  proposal?: string
  onDone?: () => void
  /** What the server answered when it refused it. */
  onRefused?: (message: string) => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => (paused ? api.resumeEntries(proposal) : api.pauseEntries(proposal)),
    onSuccess: () => {
      onClose()
      onDone?.()
      toast({
        title: paused ? 'New entries resumed' : 'New entries paused',
        description: paused ? 'Signals open positions again.' : 'The trader refuses signals that would open a position.',
        variant: 'success',
      })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
    onError: (error) => onRefused?.(error.message),
  })

  return (
    <ConfirmLiveAction
      open={open}
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
      onClose={() => {
        mutation.reset()
        onClose()
      }}
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
  )
}
