import { formatMoney, Hint, LiveValue, RelativeTime, SignedValue, useNow } from 'ioiti-ui'

import type { Position } from '@/lib/types'

import { unrealisedProfitLoss } from './unrealised'

/** Past this age the value is no longer presented as current. */
const STALE_AFTER_MS = 60_000

/**
 * The performance, the unrealised profit or loss and the price the position
 * monitor last saw for an open position: the figures the operator watches,
 * so at the size of the rest of the row, not below it. Muted, and said stale
 * to a screen reader, once the monitor has said nothing for a minute; its age
 * is the server's clock, so never said to be in the future.
 */
export function PositionLiveValue({ position, timeZone }: { position: Position; timeZone?: string }) {
  const now = useNow(1000)
  if (position.live_performance_percent == null) {
    return <span className="text-muted-foreground">waiting for the monitor</span>
  }
  const observedAt = position.live_observed_at ? Date.parse(position.live_observed_at) : null
  const stale = observedAt !== null && now - observedAt > STALE_AFTER_MS
  const unrealised = unrealisedProfitLoss(position)
  const currency = position.instrument_currency

  return (
    <span className="inline-flex flex-col items-end gap-0.5 leading-tight">
      <span className="inline-flex items-baseline gap-2 text-sm font-medium">
        <SignedValue value={position.live_performance_percent} kind="percent" animate stale={stale} />
        {unrealised !== null && (
          <Hint content="Unrealised: what selling at this bid would bring" wrap>
            <SignedValue value={unrealised} kind="money" currency={currency} animate stale={stale} />
          </Hint>
        )}
      </span>
      <span className="text-xs text-muted-foreground">
        bid <LiveValue value={position.live_bid ?? null} format={(bid) => formatMoney(bid, currency)} stale={stale} />
        {position.live_observed_at && (
          <>
            {' · '}
            <RelativeTime value={position.live_observed_at} timeZone={timeZone} past />
          </>
        )}
      </span>
    </span>
  )
}
