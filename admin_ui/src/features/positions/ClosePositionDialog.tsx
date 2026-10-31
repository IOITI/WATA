// The confirmation of a close of one position: the Positions page's, and the AI agent's proposal of the same (its
// card), sent with the proposal it applies so that the audit trail says so. Once asked, the close is followed to
// its outcome by the shell's card (FollowProvider), or said in a toast.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { formatNumber, useToast } from 'ioiti-ui'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { useFollow } from '@/features/live/follow-context'
import { api } from '@/lib/api'
import type { Position } from '@/lib/types'

export function ClosePositionDialog({ position, open, onClose, proposal, onDone, onRefused }: {
  position: Pick<Position, 'position_id' | 'instrument_name'> & { position_amount?: number | null }
  open: boolean
  onClose: () => void
  /** The AI agent's proposal this applies (<conversation>/<proposal>). */
  proposal?: string
  onDone?: () => void
  /** What the server answered when it refused it. */
  onRefused?: (message: string) => void
}) {
  const toast = useToast()
  const follow = useFollow()
  const queryClient = useQueryClient()
  const mutation = useMutation({
    // When it was asked, by this browser's clock: the card that follows it takes the sells sent from then on.
    mutationFn: (_askedAt: number) => api.closePosition(position.position_id, proposal),
    onSuccess: (_closing, askedAt) => {
      onClose()
      onDone?.()
      if (follow) {
        follow({
          kind: 'close',
          positionId: position.position_id,
          instrument: position.instrument_name,
          amount: position.position_amount ?? null,
          askedAt,
        })
      } else {
        toast({
          title: 'Close requested',
          description: 'The position monitor sells it now; the result shows here and on Telegram.',
          variant: 'success',
        })
      }
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      void queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
    onError: (error) => onRefused?.(error.message),
  })
  const what = position.instrument_name ?? position.position_id

  return (
    <ConfirmLiveAction
      open={open}
      title="Close this position"
      confirmLabel="Slide to close"
      doneLabel="Requested"
      pending={mutation.isPending}
      error={mutation.error?.message}
      onConfirm={() => mutation.mutate(Date.now())}
      onClose={() => {
        mutation.reset()
        onClose()
      }}
      description={
        <>
          The position monitor will sell{' '}
          <strong>
            {position.position_amount != null ? `${formatNumber(position.position_amount)} × ${what}` : what}
          </strong>{' '}
          at market. Other positions are not touched. The request is dropped if the monitor has not taken it within
          a minute.
        </>
      }
    />
  )
}
