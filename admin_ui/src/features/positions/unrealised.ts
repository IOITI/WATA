import type { Position } from '@/lib/types'

/**
 * What an open position would bring if it were sold at the bid the monitor
 * last saw. A turbo is bought whichever way it bets, so this is the same sum
 * for a long and for a short.
 */
export function unrealisedProfitLoss(position: Position): number | null {
  const { live_bid: bid, position_open_price: openPrice, position_amount: amount } = position
  if (bid == null || openPrice == null || amount == null) return null
  return (bid - openPrice) * amount
}
