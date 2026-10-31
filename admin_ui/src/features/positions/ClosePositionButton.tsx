import { Button } from 'ioiti-ui'
import { useState } from 'react'

import type { Position } from '@/lib/types'

import { ClosePositionDialog } from './ClosePositionDialog'

/** Ask the position monitor to sell one open position. */
export function ClosePositionButton({ position }: { position: Position }) {
  const [confirming, setConfirming] = useState(false)

  return (
    <>
      <Button
        // It sells at market: it must not look like the neutral buttons around it.
        variant="destructive"
        size="sm"
        onClick={() => setConfirming(true)}
      >
        Close
      </Button>
      <ClosePositionDialog position={position} open={confirming} onClose={() => setConfirming(false)} />
    </>
  )
}
