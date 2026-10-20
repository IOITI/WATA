import { describe, expect, it } from 'vitest'

import type { PriceBarRow } from '@/lib/types'
import { SETTINGS, trade } from '@/test/backtests'

import { exitSettings, tradeLevels, tradeOverlays, tradePath, trailingLevel } from './tradeChart'

const utc = (iso: string) => Date.parse(iso) / 1000

/** A ten-second bar of the turbo's bid (the ask five cents above). */
function bar(iso: string, open: number, high: number, low: number, close: number): PriceBarRow {
  return [utc(iso), open, high, low, close, open + 0.05, high + 0.05, low + 0.05, close + 0.05, 4]
}

// The trade was bought at 10.48 at 06:51:04 and sold at 10.66 at 07:07:10.
const BARS: PriceBarRow[] = [
  bar('2026-10-14T06:50:40Z', 10.4, 10.42, 10.38, 10.4),
  bar('2026-10-14T06:51:00Z', 10.43, 10.5, 10.4, 10.45),
  bar('2026-10-14T06:51:10Z', 10.45, 11.1, 10.45, 11),
  bar('2026-10-14T06:51:20Z', 11, 11.02, 10.9, 10.95),
  bar('2026-10-14T07:07:10Z', 10.66, 10.7, 10.6, 10.66),
  bar('2026-10-14T07:07:20Z', 10.7, 10.9, 10.7, 10.8),
]

describe('the exit settings of a run', () => {
  it('reads them from its settings, with the live defaults for what is missing', () => {
    expect(exitSettings(SETTINGS)).toEqual({
      stoploss_percent: -15,
      max_profit_percent: 60,
      trailing_enabled: true,
      trailing_activation_percent: 5,
      trailing_drawdown_percent: 8,
      day_profit_target: 1.7,
    })
    expect(exitSettings({ stoploss_percent: '-3' }).stoploss_percent).toBe(-15)
    expect(exitSettings({}).trailing_enabled).toBe(false)
  })
})

describe('the levels of a trade', () => {
  it('puts its stop, take-profit and the day\'s target on the price it paid', () => {
    const levels = tradeLevels(trade(), exitSettings(SETTINGS))
    expect(levels.map((level) => [level.id, Number(level.price.toFixed(4)), Number(level.percent.toFixed(4))])).toEqual([
      ['stop', 8.908, -15],
      ['take_profit', 16.768, 60],
      ['day_target', 10.6582, 1.7],
    ])
    expect(levels[2].label).toBe("Day's target (+1.70 % for this trade)")
  })

  it('lowers the day\'s target by what the day had already made', () => {
    const [, , target] = tradeLevels(trade({ day_percent_before: 1 }), exitSettings(SETTINGS))
    // 1.017 / 1.01: the day reaches 1.7 % when this trade makes 0.69 %.
    expect(target.percent).toBeCloseTo(0.6931, 4)
    expect(target.label).toBe("Day's target (+0.69 % for this trade)")
  })
})

describe('the trailing stop', () => {
  it('follows the best the trade reached once armed, the drawdown under it', () => {
    const points = trailingLevel(trade(), BARS, exitSettings(SETTINGS), 'UTC')
    // Armed at 06:51:10, when the bid's high made +5.92 %: 8 points under it from then on, until the sale.
    const level = Number((10.48 * (1 + (5.92 - 8) / 100)).toFixed(6))
    expect(points.map((point) => [point.time, Number(point.value.toFixed(6))])).toEqual([
      [utc('2026-10-14T06:51:10Z'), level],
      [utc('2026-10-14T06:51:20Z'), level],
      [utc('2026-10-14T07:07:10Z'), level],
    ])
  })

  it('is not drawn when the run had none', () => {
    expect(trailingLevel(trade(), BARS, { ...exitSettings(SETTINGS), trailing_enabled: false }, 'UTC')).toEqual([])
  })
})

describe('the lines over the turbo', () => {
  it('draws the price paid, the stop and the target while the trade was held, the take-profit only when near', () => {
    const overlays = tradeOverlays(trade(), BARS, exitSettings(SETTINGS), 'UTC')
    expect(overlays.map((overlay) => [overlay.id, overlay.tone])).toEqual([
      ['bought', 'reference'],
      ['stop', 'band'],
      ['day_target', 'band'],
      ['trailing', 'trend'],
    ])
    // From the bar before the fill to the bar of the sale.
    expect(overlays[0].points.map((point) => point.time)).toEqual([
      utc('2026-10-14T06:51:00Z'),
      utc('2026-10-14T06:51:10Z'),
      utc('2026-10-14T06:51:20Z'),
      utc('2026-10-14T07:07:10Z'),
    ])
    const near = tradeOverlays(trade(), BARS, { ...exitSettings(SETTINGS), max_profit_percent: 5 }, 'UTC')
    expect(near.map((overlay) => overlay.id)).toContain('take_profit')
  })

  it('draws nothing without a bar of the trade', () => {
    expect(tradeOverlays(trade(), BARS.slice(0, 1), exitSettings(SETTINGS), 'UTC')).toEqual([])
  })

  it('gives its result at each ten seconds it was held', () => {
    expect(tradePath(trade(), BARS)).toEqual([
      { t: utc('2026-10-14T06:51:00Z'), value: -0.29 },
      { t: utc('2026-10-14T06:51:10Z'), value: 4.96 },
      { t: utc('2026-10-14T06:51:20Z'), value: 4.48 },
      { t: utc('2026-10-14T07:07:10Z'), value: 1.72 },
    ])
  })
})
