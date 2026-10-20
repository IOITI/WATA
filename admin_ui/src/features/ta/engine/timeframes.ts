// The bar lengths of the Technical Analysis pages, between what Vela and Pine name
// them and what WATA's API serves (src/admin/prices.py, src/admin/ta_rules.py).
//
// WATA records ten-second bars, and makes every bar of time of them: from ten
// seconds to a day, each bar beginning at a whole number of its length since
// 1970-01-01 UTC (so on the minute, the hour, midnight UTC when the length divides
// them). A length is named as Pine names a timeframe: whole minutes as a number
// ("1", "60", "1440"), any other length in seconds ("10S", "45S", "90S").
//
// A length that is no multiple of ten seconds cannot cut the recorded bars: each of
// its bars holds the ten seconds that begin in it, a 45-second bar 50 or 40 seconds
// of prices by turns, and is over once its last ten seconds are (`barEnd`).
//
// Vela's own code knows no seconds: vite/velaSeconds.ts teaches it.

export interface Timeframe {
  /** Pine's and Vela's name: whole minutes as a number ("5"), any other length in seconds ("45S"). */
  pine: string
  /** The interval asked of WATA's API, in seconds. */
  seconds: number
  label: string
}

/** What WATA records: every bar of time is made of ten-second bars. */
export const RECORDED_SECONDS = 10
/** The shortest and longest bars WATA makes. */
export const MIN_SECONDS = RECORDED_SECONDS
export const MAX_SECONDS = 86_400

export function lengthLabel(seconds: number): string {
  if (seconds % 3600 === 0) return seconds === 3600 ? '1 hour' : `${seconds / 3600} hours`
  if (seconds % 60 === 0) return `${seconds / 60} min`
  return `${seconds} s`
}

/** A length as it is typed: 45s, 2m, 1h. */
export function shortName(seconds: number): string {
  if (seconds % 3600 === 0) return `${seconds / 3600}h`
  if (seconds % 60 === 0) return `${seconds / 60}m`
  return `${seconds}s`
}

/** The timeframe of `seconds`, or null for a length WATA does not make (under ten seconds, over a day, a fraction). */
export function timeframeOfSeconds(seconds: number): Timeframe | null {
  if (!Number.isInteger(seconds) || seconds < MIN_SECONDS || seconds > MAX_SECONDS) return null
  return { pine: seconds % 60 === 0 ? String(seconds / 60) : `${seconds}S`, seconds, label: lengthLabel(seconds) }
}

/** The lengths the pickers offer: the Charts page, the editor's test and the backtests; any other can be typed. */
export const TIMEFRAMES: Timeframe[] = [10, 20, 30, 45, 60, 300, 900, 3600].map((seconds) => timeframeOfSeconds(seconds)!)

/**
 * What a signal rule runs on: whole minutes. The TA engine runs a rule's script over
 * all of its bars at each new one, which bars of seconds would make several times a minute.
 */
export const RULE_TIMEFRAMES: Timeframe[] = TIMEFRAMES.filter((timeframe) => timeframe.seconds >= 60)

export const DEFAULT_TIMEFRAME = timeframeOfSeconds(60)!

// A plain number is minutes, as in Pine; a unit is written after it, with or without a space.
const UNIT_SECONDS: Record<string, number> = {
  '': 60,
  s: 1,
  sec: 1,
  secs: 1,
  second: 1,
  seconds: 1,
  m: 60,
  min: 60,
  mins: 60,
  minute: 60,
  minutes: 60,
  h: 3600,
  hr: 3600,
  hrs: 3600,
  hour: 3600,
  hours: 3600,
  d: 86_400,
  day: 86_400,
  days: 86_400,
}

export const TIMEFRAME_HINT = 'From 10 seconds to a day: 45s, 90s, 2m, 4h. A plain number is minutes.'

/** A length as Pine, Vela or a person writes it ("45S", "45s", "45 sec", "5", "5m", "1h", "1 day"), or why it is none. */
export function readTimeframe(text: string): { timeframe: Timeframe } | { error: string } {
  const typed = text.trim()
  if (!typed) return { error: 'Write a length: 45s, 2m, 1h.' }
  const match = /^(\d{1,6})\s*([a-z]*)$/i.exec(typed)
  const unit = match ? UNIT_SECONDS[match[2].toLowerCase()] : undefined
  if (!match || unit === undefined) return { error: 'Not a length of time: write it as 45s, 2m or 1h.' }
  const seconds = Number(match[1]) * unit
  if (seconds < MIN_SECONDS) return { error: 'WATA records ten-second bars: 10 seconds at least.' }
  if (seconds > MAX_SECONDS) return { error: 'A day at most.' }
  return { timeframe: timeframeOfSeconds(seconds)! }
}

/** A timeframe from Pine's or Vela's name ("1", "45S", "60"), or as typed ("45s", "2m", "1h"); null for none WATA makes. */
export function timeframeOf(name: string): Timeframe | null {
  const read = readTimeframe(name)
  return 'timeframe' in read ? read.timeframe : null
}

/** When a bar that opens at `start` (epoch seconds) is over: once the last ten seconds that begin in it are. */
export function barEnd(start: number, seconds: number): number {
  return Math.ceil((start + seconds) / RECORDED_SECONDS) * RECORDED_SECONDS
}

/** What to know of a length that cuts no whole ten seconds; null for one that does. */
export function unevenNote(seconds: number): string | null {
  if (seconds % RECORDED_SECONDS === 0) return null
  const shorter = Math.floor(seconds / RECORDED_SECONDS) * RECORDED_SECONDS
  return (
    `${lengthLabel(seconds)} is no multiple of the ten seconds WATA records: each bar holds the ten-second bars ` +
    `that begin in it, ${shorter} or ${shorter + RECORDED_SECONDS} s of prices, and closes when the last of them does.`
  )
}

// What one request of WATA's API may cover (src/admin/prices.py and src/admin/data.py).
export const MAX_BUCKETS = 12_000
export const MAX_PERIOD_DAYS = 366
export const HISTORY_MAX_DAYS = 31
export const RANGE_MAX_DAYS = 31

/**
 * How many days of bars of `seconds` one request may ask for: the whole days whose
 * bars stay under the API's cap, a market open around the clock taken as the worst.
 */
export function daysPerRequest(seconds: number, maxDays: number = MAX_PERIOD_DAYS): number {
  const days = Math.floor((MAX_BUCKETS * seconds) / 86_400)
  return Math.max(1, Math.min(days, maxDays))
}
