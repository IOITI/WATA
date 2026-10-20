import { describe, expect, it } from 'vitest'

import type { Locator } from '@/features/prices/chartData'
import { signal, trade } from '@/test/backtests'

import { signalMarkers, signalMoment, tradeFocus, tradeMarkers } from './dayChart'

const utc = (iso: string) => Date.parse(iso) / 1000

/** A chart of minute bars from 06:00 to 08:00 UTC, its times left in UTC. */
const locate: Locator = (instant) => {
  if (instant < utc('2026-10-14T06:00:00Z') || instant >= utc('2026-10-14T08:00:00Z')) return null
  return instant - (instant % 60)
}

describe('signals on the chart of the day', () => {
  it('puts a signal at the bar it was raised on, else when it reached the trader', () => {
    expect(signalMoment(signal())).toBe(utc('2026-10-14T06:50:00Z'))
    expect(signalMoment(signal({ bar_time: undefined }))).toBe(utc('2026-10-14T06:51:03Z'))
    expect(signalMoment(signal({ bar_time: undefined, arrived_at: null, bar_closed_at: undefined }))).toBeNull()
  })

  it('marks entries by their direction and fate, and a close of the day only when it closed something', () => {
    const markers = signalMarkers(
      [
        signal({ id: 3, action: 'long', fate: 'refused', bar_time: '2026-10-14T07:10:00Z' }),
        signal(),
        signal({ id: 20, source: 'schedule', action: 'close-position', fate: 'ignored', bar_time: undefined, arrived_at: '2026-10-14T07:30:00Z' }),
        signal({ id: 21, source: 'schedule', action: 'close-position', fate: 'closed', bar_time: undefined, arrived_at: '2026-10-14T07:40:00Z' }),
        // Before the chart's first bar: nowhere to put it.
        signal({ id: 1, bar_time: '2026-10-14T05:00:00Z' }),
      ],
      locate,
    )
    expect(markers).toEqual([
      { id: 'signal-12', time: utc('2026-10-14T06:50:00Z'), shape: 'arrowDown', tone: 'entry', text: 'Short', position: 'aboveBar' },
      { id: 'signal-3', time: utc('2026-10-14T07:10:00Z'), shape: 'arrowUp', tone: 'neutral', text: 'Long ✕', position: 'belowBar' },
      { id: 'signal-21', time: utc('2026-10-14T07:40:00Z'), shape: 'circle', tone: 'neutral', text: 'Close', position: 'aboveBar' },
    ])
  })

  it('tells a signal with no recorded turbo from one the rules refused', () => {
    const [marker] = signalMarkers([signal({ fate: 'unpriced' })], locate)
    expect(marker).toMatchObject({ tone: 'warning', text: 'Short ✕' })
  })
})

describe('trades on the chart', () => {
  it('marks the purchase by its direction and the sale by what it made', () => {
    expect(tradeMarkers([trade(), trade({ id: 2, direction: 'long', performance_percent: -3.1, exit: { ...trade().exit, filled_at: '2026-10-14T07:20:05Z' } })], locate)).toEqual([
      { id: 'buy-1', time: utc('2026-10-14T06:51:00Z'), shape: 'arrowDown', tone: 'entry', text: '#1 short', position: 'aboveBar' },
      { id: 'buy-2', time: utc('2026-10-14T06:51:00Z'), shape: 'arrowUp', tone: 'entry', text: '#2 long', position: 'belowBar' },
      { id: 'sell-1', time: utc('2026-10-14T07:07:00Z'), shape: 'circle', tone: 'profit', text: '+1.72 %', position: 'aboveBar' },
      { id: 'sell-2', time: utc('2026-10-14T07:20:00Z'), shape: 'circle', tone: 'loss', text: '-3.10 %', position: 'aboveBar' },
    ])
  })

  it('marks them at their prices on the chart of the turbo', () => {
    expect(tradeMarkers([trade()], locate, true)).toEqual([
      { id: 'buy-1', time: utc('2026-10-14T06:51:00Z'), shape: 'arrowUp', tone: 'entry', text: 'Bought 10.48', position: 'atPriceBottom', price: 10.48 },
      { id: 'sell-1', time: utc('2026-10-14T07:07:00Z'), shape: 'arrowDown', tone: 'profit', text: 'Sold 10.66 (+1.72 %)', position: 'atPriceTop', price: 10.66 },
    ])
  })

  it('brings a trade into view with a margin, nearer when the chart has no bar there', () => {
    expect(tradeFocus(trade(), locate, 300)).toEqual({ from: utc('2026-10-14T06:46:00Z'), to: utc('2026-10-14T07:12:00Z') })
    // Fifteen minutes before 06:05 is before the first bar: as far back as the chart goes.
    const early = trade({ entry: { ...trade().entry, filled_at: '2026-10-14T06:05:00Z' } })
    expect(tradeFocus(early, locate)?.from).toBe(utc('2026-10-14T06:00:00Z'))
    expect(tradeFocus(trade({ entry: { ...trade().entry, filled_at: '' } }), locate)).toBeNull()
  })
})
