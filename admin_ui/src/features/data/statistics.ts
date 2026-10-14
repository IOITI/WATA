import { valueDecimals } from '@/features/prices/chartData'
import type { AssetDayStatistics, AssetSpreadSlot } from '@/lib/types'

/** The decimals the prices of these days are written with: those of the asset. */
export function statisticsDecimals(days: readonly AssetDayStatistics[]): number {
  return valueDecimals(days.flatMap((day) => [day.open, day.high, day.low, day.close]))
}

/** A period of an asset in a few figures, from its days. */
export interface PeriodFigures {
  days: number
  bars: number
  updates: number
  /** From the first open to the last close. */
  change: number | null
  changePercent: number | null
  /** What a day covered from its low to its high, on average. */
  averageRange: number | null
  averageRangePercent: number | null
  /** Over every bar that had an ask, whatever its day: a short day counts for what it has. */
  spreadAverage: number | null
  spreadWidest: number | null
  /** The share of the period's trading time that has a bar. */
  covered: number | null
  /** What Saxo counted as traded, when it was recorded on some day of the period. */
  volume: number | null
}

const mean = (values: number[]) => (values.length > 0 ? values.reduce((sum, value) => sum + value, 0) / values.length : null)

export function periodFigures(days: readonly AssetDayStatistics[]): PeriodFigures {
  const first = days[0]
  const last = days[days.length - 1]
  const quoted = days.filter((day) => day.spread_average !== null)
  const withAsk = (day: AssetDayStatistics) => day.bars - day.without_ask
  const spreadBars = quoted.reduce((sum, day) => sum + withAsk(day), 0)
  const tradingSeconds = days.reduce((sum, day) => sum + day.trading_seconds, 0)
  const volumes = days.filter((day) => day.volume !== null)
  const widest = quoted.map((day) => day.spread_widest).filter((value): value is number => value !== null)
  return {
    days: days.length,
    bars: days.reduce((sum, day) => sum + day.bars, 0),
    updates: days.reduce((sum, day) => sum + day.updates, 0),
    change: first && last ? last.close - first.open : null,
    changePercent: first && last && first.open ? (100 * (last.close - first.open)) / first.open : null,
    averageRange: mean(days.map((day) => day.range)),
    averageRangePercent: mean(days.flatMap((day) => (day.range_percent === null ? [] : [day.range_percent]))),
    spreadAverage:
      spreadBars > 0 ? quoted.reduce((sum, day) => sum + (day.spread_average ?? 0) * withAsk(day), 0) / spreadBars : null,
    spreadWidest: widest.length > 0 ? Math.max(...widest) : null,
    covered:
      tradingSeconds > 0
        ? days.reduce((sum, day) => sum + (day.covered ?? 0) * day.trading_seconds, 0) / tradingSeconds
        : null,
    volume: volumes.length > 0 ? volumes.reduce((sum, day) => sum + (day.volume ?? 0), 0) : null,
  }
}

/** The slot of the day the spread is widest in on average: when the asset is dearest to trade. */
export function dearestSlot(slots: readonly AssetSpreadSlot[]): AssetSpreadSlot | null {
  return slots.reduce<AssetSpreadSlot | null>(
    (found, slot) =>
      slot.spread_average !== null && (found === null || slot.spread_average > (found.spread_average ?? -Infinity))
        ? slot
        : found,
    null,
  )
}
