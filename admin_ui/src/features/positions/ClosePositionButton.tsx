import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { useToast } from '@/components/toast-context'
import { Button } from '@/components/ui/button'
import { useFollow } from '@/features/live/follow-context'
import { api } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import type { Position } from '@/lib/types'

/** Ask the position monitor to sell one open position. */
export function ClosePositionButton({ position }: { position: Position }) {
  const toast = useToast()
  const follow = useFollow()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)

  const closeMutation = useMutation({
    // When it was asked, by this browser's clock: the card that follows it takes the sells sent from then on.
    mutationFn: (_askedAt: number) => api.closePosition(position.position_id),
    onSuccess: (_closing, askedAt) => {
      setConfirming(false)
      if (follow) {
        follow({
          kind: 'close',
          positionId: position.position_id,
          instrument: position.instrument_name,
          amount: position.position_amount,
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
  })

  return (
    <>
      <Button
        // It sells at market: it must not look like the neutral buttons around it.
        variant="destructive"
        size="sm"
        onClick={() => {
          closeMutation.reset()
          setConfirming(true)
        }}
      >
        Close
      </Button>
      <ConfirmLiveAction
        open={confirming}
        title="Close this position"
        confirmLabel="Slide to close"
        doneLabel="Requested"
        pending={closeMutation.isPending}
        error={closeMutation.error?.message}
        onConfirm={() => closeMutation.mutate(Date.now())}
        onClose={() => setConfirming(false)}
        description={
          <>
            The position monitor will sell{' '}
            <strong>
              {formatNumber(position.position_amount)} × {position.instrument_name ?? position.position_id}
            </strong>{' '}
            at market. Other positions are not touched. The request is dropped if the monitor has not taken it within
            a minute.
          </>
        }
      />
    </>
  )
}
