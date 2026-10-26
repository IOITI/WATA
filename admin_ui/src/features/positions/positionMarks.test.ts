import { describe, expect, it } from 'vitest'

import type { Position, PriceBarRow } from '@/lib/types'

import { positionFocus, positionMarkers, positionOverlays, reasonLevel } from './positionMarks'

const AT = (clock: string) => Date.parse(`2026-10-19T${clock}Z`) / 1000

const position = (changes: Partial<Position> = {}): Position =>
  ({
    position_id: '1', action: 'long', position_status: 'Closed',
    execution_time_open: '2026-10-19T09:00:05Z', execution_time_close: '2026-10-19T09:03:00Z',
    position_open_price: 5, position_close_price: 4.6, position_total_performance_percent: -8,
    position_max_performance_percent: 2, position_min_performance_percent: -9,
    position_close_reason: 'Performance (Stoploss (-7%) hit at -8.47%)', ...changes,
  }) as Position

// Every ten seconds from 08:58 to 09:05: open, high, low, close of the bid.
const BARS: PriceBarRow[] = Array.from({ length: 43 }, (_, index) => {
  const t = AT('08:58:00') + index * 10
  return [t, 5, 5.1, 4.5, 5, 5.05, 5.15, 4.55, 5.05, 0] as unknown as PriceBarRow
})
const locate = (instant: number) => (instant >= AT('08:58:00') && instant <= AT('09:05:00') ? Math.floor(instant / 10) * 10 : null)

describe('a real position on its chart', () => {
  it('reads the level its close reason names', () => {
    const stop = reasonLevel('Performance (Stoploss (-7%) hit at -8.47%)', 5)
    expect(stop?.label).toBe('Stop-loss -7 %')
    expect(stop?.price).toBeCloseTo(4.65)
    expect(reasonLevel('Performance (Takeprofit (60%) hit at 61%)', 5)?.label).toBe('Take-profit +60 %')
    expect(reasonLevel('Performance (Trailing Stop: peak 2.7%, now -0.5% (dd 3.2% >= 2.5%))', 10)?.price).toBeCloseTo(10.27)
    expect(reasonLevel('Explicit Close (All)', 5)).toBeNull()
    expect(reasonLevel(null, 5)).toBeNull()
  })

  it('marks the signal, the purchase and the sale', () => {
    const markers = positionMarkers(position(), { action: 'long', signal_timestamp: '2026-10-19T08:59:40Z' }, locate)

    expect(markers.map((marker) => marker.id)).toEqual(['signal', 'bought', 'sold'])
    expect(markers[1]).toMatchObject({ text: 'Bought 5', position: 'atPriceBottom', price: 5 })
    expect(markers[2]).toMatchObject({ text: 'Sold 4.6 (-8.00 %)', tone: 'loss' })
    // A position without its signal: the purchase and the sale alone.
    expect(positionMarkers(position(), null, locate).map((marker) => marker.id)).toEqual(['bought', 'sold'])
  })

  it('draws over the bid while held the price paid, its best and worst, and the level of its close', () => {
    const overlays = positionOverlays(position(), BARS, 'UTC')

    expect(overlays.map((overlay) => overlay.id)).toEqual(['bought', 'best', 'worst', 'reason'])
    const value = (id: string) => {
      const found = overlays.find((overlay) => overlay.id === id)
      return found ? (found.points[0] as { value: number }).value : Number.NaN
    }
    expect(value('best')).toBeCloseTo(5.1)
    expect(value('worst')).toBeCloseTo(4.55)
    expect(value('reason')).toBeCloseTo(4.65)
    // A position closed before v0.48.0 has no worst: none drawn.
    expect(positionOverlays(position({ position_min_performance_percent: null }), BARS, 'UTC').map((overlay) => overlay.id))
      .toEqual(['bought', 'best', 'reason'])
  })

  it('brings into view from before the signal to after the sale', () => {
    const focus = positionFocus(position(), { action: 'long', signal_timestamp: '2026-10-19T08:59:40Z' }, locate, 60)

    expect(focus).toEqual({ from: AT('08:58:40'), to: AT('09:04:00') })
  })
})
