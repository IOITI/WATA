import { formatPercent, LiveLineChart } from 'ioiti-ui'
import { useCallback, useMemo } from 'react'

import { ActionBadge } from '@/components/status'
import type { Position } from '@/lib/types'

import { liveSamples, onLiveSample } from './liveSamples'

const WINDOW_SECONDS = 15 * 60

const formatValue = (value: number) => formatPercent(value, true)
const formatTick = (value: number) => `${value.toFixed(1)}%`
// A performance changes by points of percentage, not by a percentage of itself.
const formatChange = (change: number) => `${Math.abs(change).toFixed(2)} pts`

/**
 * An open position's performance as the position monitor publishes it, scrolling over the last fifteen minutes.
 * Hovering or touching it stops it, to read a moment.
 */
export function LivePositionChart({ position }: { position: Position }) {
  const id = position.position_id
  const initialPoints = useMemo(() => liveSamples(id), [id])
  const source = useCallback(
    (push: (value: number, time?: number) => void) =>
      onLiveSample((positionId, sample) => {
        if (positionId === id) push(sample.v, sample.t)
      }),
    [id],
  )
  const name = position.instrument_name ?? `Position ${id}`

  return (
    <figure className="min-w-0 rounded-xl border border-border/60 p-3 sm:p-4">
      <figcaption className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="min-w-0 truncate font-medium">{name}</span>
        <ActionBadge action={position.action} />
      </figcaption>
      <LiveLineChart
        key={id}
        initialPoints={initialPoints}
        source={source}
        intervalMs={5_000}
        windowSeconds={WINDOW_SECONDS}
        height={160}
        color="var(--chart-series)"
        formatValue={formatValue}
        formatTick={formatTick}
        formatChange={formatChange}
        ariaLabel={`Performance of ${name} over the last 15 minutes`}
      />
      <p className="mt-2 text-xs text-muted-foreground">
        From when this tab first read it: the server keeps only the latest value.
      </p>
    </figure>
  )
}
