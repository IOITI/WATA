import { cn, formatDuration, formatMoney, SignedValue } from 'ioiti-ui'

import type { Position } from '@/lib/types'

import { unrealisedProfitLoss } from './unrealised'

/** Past this age the value is no longer presented as current. */
const STALE_AFTER_MS = 60_000

/**
 * The performance, the unrealised profit or loss and the price the position
 * monitor last saw for an open position: the figures the operator watches,
 * so at the size of the rest of the row, not below it.
 */
export function LiveValue({ position, now }: { position: Position; now: number }) {
  if (position.live_performance_percent == null) {
    return <span className="text-muted-foreground">waiting for the monitor</span>
  }
  const observedAt = position.live_observed_at ? new Date(position.live_observed_at).getTime() : null
  const age = observedAt === null ? null : Math.max(0, now - observedAt)
  const stale = age !== null && age > STALE_AFTER_MS
  const unrealised = unrealisedProfitLoss(position)

  return (
    <span className="inline-flex flex-col items-end gap-0.5 leading-tight">
      <span className={cn('inline-flex items-baseline gap-2 text-sm font-medium', stale && 'opacity-60')}>
        <SignedValue value={position.live_performance_percent} kind="percent" animate />
        {unrealised !== null && (
          <span title="Unrealised: what selling at this bid would bring">
            <SignedValue value={unrealised} kind="money" currency={position.instrument_currency} animate />
          </span>
        )}
      </span>
      <span className="text-xs text-muted-foreground">
        bid {formatMoney(position.live_bid, position.instrument_currency)}
        {age !== null && ` · ${formatDuration(age)} ago`}
      </span>
    </span>
  )
}
