// The bar lengths of the Technical Analysis pages, between what Vela and Pine name
// them and what WATA's API serves (src/admin/prices.py INTERVALS).
//
// Vela's shortest bar is a minute (its timeframes have no seconds), and WATA has
// no bar longer than an hour.

export interface Timeframe {
  /** Pine's and Vela's name: a number of minutes. */
  pine: string
  /** The interval asked of WATA's API, in seconds. */
  seconds: number
  label: string
}

export const TIMEFRAMES: Timeframe[] = [
  { pine: '1', seconds: 60, label: '1 min' },
  { pine: '5', seconds: 300, label: '5 min' },
  { pine: '15', seconds: 900, label: '15 min' },
  { pine: '60', seconds: 3600, label: '1 hour' },
]

export const DEFAULT_TIMEFRAME = TIMEFRAMES[0]

// What one request of WATA's API may cover (src/admin/prices.py and src/admin/data.py).
export const MAX_BUCKETS = 12_000
export const MAX_PERIOD_DAYS = 366
export const HISTORY_MAX_DAYS = 31
export const RANGE_MAX_DAYS = 31

/** A timeframe from Pine's or Vela's name: "1", "5", "60", or "1m", "1h" as Vela also writes them. */
export function timeframeOf(name: string): Timeframe | null {
  const match = /^(\d+)\s*([mh])?$/i.exec(name.trim())
  if (!match) return null
  const minutes = Number(match[1]) * (match[2]?.toLowerCase() === 'h' ? 60 : 1)
  return TIMEFRAMES.find((timeframe) => timeframe.seconds === minutes * 60) ?? null
}

/**
 * How many days of bars of `seconds` one request may ask for: the whole days whose
 * bars stay under the API's cap, a market open around the clock taken as the worst.
 */
export function daysPerRequest(seconds: number, maxDays: number = MAX_PERIOD_DAYS): number {
  const days = Math.floor((MAX_BUCKETS * seconds) / 86_400)
  return Math.max(1, Math.min(days, maxDays))
}
