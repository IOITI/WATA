/**
 * "At this pace": what a run would make over the trading days to come, two years at most, drawn from its own days
 * (src/admin/run_projection.py gives them, with the days to come). Each path takes, for each trading day, one of the
 * run's days at random, with replacement, as the run's total range and its drawdown stress draw them: a day that
 * closes early takes one of the days' mornings. 2,000 paths, seeded by the run: the same run draws the same paths.
 *
 * The amounts are the account's: a start, and with a cap on the stake, the account grows each day by the result of
 * no more than the cap, the rest set aside. Pure: the panel only shows what this says.
 */
import { formatDay, formatMoney, formatPercent } from 'ioiti-ui'
import type { FanMarker, FanPoint } from 'ioiti-ui'

import type { BacktestProjection, ProjectionDay, ProjectionLeftOut, ProjectionSkipped } from '@/lib/types'

export const PATHS = 2_000
/** The levels drawn and said: the band of nine paths in ten, the band of half of them, and the middle. */
export const LEVELS = [5, 25, 50, 75, 95] as const
/** The horizons offered, in months. */
export const HORIZONS = [3, 6, 12, 24] as const
export type Horizon = (typeof HORIZONS)[number]
export const HORIZON_LABEL: Record<Horizon, string> = { 3: '3 months', 6: '6 months', 12: '1 year', 24: '2 years' }
/** About as many places a chart draws, whatever its horizon: every day up to it, every second or fourth beyond. */
const CHART_POINTS = 130
/** A start this far from the run's capital, either way, weighs its fixed costs and whole turbos otherwise. */
const FAR_FROM_CAPITAL = 2

export type Unit = 'money' | 'percent'

/** A seeded generator of numbers in [0, 1): mulberry32, small and good enough for drawing days. */
export function mulberry32(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296
  }
}

export interface ProjectionInput {
  days: ProjectionDay[]
  growths: number[]
  earlyGrowths: number[]
  seed: number
}

/** What the paths give at one place: the levels (in euros) and, at a horizon, how they ended and fell. */
export interface Levels {
  index: number
  values: number[]
}

export interface HorizonFigures {
  months: Horizon
  /** Trading days from now. */
  index: number
  day: string
  levels: number[]
  pace: number
  /** Share of the paths ending under the start, and under half of it. */
  belowStart: number
  belowHalf: number
  /** The deepest fall from a path's highest on the way, in percent: the middle path's, and the one in twenty's. */
  fallMedian: number
  fallBad: number
}

export interface Simulated {
  start: number
  cap: number | null
  /** Places kept for the charts, each with its levels. */
  levels: Map<number, number[]>
  horizons: HorizonFigures[]
  /** The account at the run's own average day, day after day (index 0: now). */
  pace: number[]
}

/** The run's average day: the geometric mean of its growths; 0 once a day emptied the account. */
export function geometricMean(growths: number[]): number {
  if (growths.length === 0) return 1
  if (growths.some((growth) => growth <= 0)) return 0
  return Math.exp(growths.reduce((sum, growth) => sum + Math.log(growth), 0) / growths.length)
}

/** The account after a day of growth ``growth``: with a cap, only the cap's worth of it moves. */
export function nextAccount(account: number, growth: number, cap: number | null): number {
  const staked = cap === null ? account : Math.min(account, cap)
  return Math.max(0, account + staked * (growth - 1))
}

/** How many trading days to come lie within ``months`` of the first: the horizon's place on the chart. */
export function horizonIndex(days: ProjectionDay[], from: string, months: number): number {
  const [year, month, day] = from.split('-').map(Number)
  // The day before the same date ``months`` later (a 31st of a shorter month falls on its last day).
  const end = new Date(Date.UTC(year, month - 1 + months, 1))
  const last = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0)).getUTCDate()
  end.setUTCDate(Math.min(day, last))
  end.setUTCDate(end.getUTCDate() - 1)
  const until = end.toISOString().slice(0, 10)
  let count = 0
  for (const one of days) {
    if (one.day > until) break
    count += 1
  }
  return count
}

/** The places a chart of a horizon of ``index`` days draws: 0, every ``step``-th day, and the horizon's own. */
export function chartPlaces(index: number): number[] {
  const step = Math.max(1, Math.ceil(index / CHART_POINTS))
  const places = []
  for (let place = 0; place < index; place += step) places.push(place)
  places.push(index)
  return places
}

function quantile(sorted: Float64Array, share: number): number {
  if (sorted.length === 0) return 0
  const place = (sorted.length - 1) * share
  const below = Math.floor(place)
  const above = Math.min(sorted.length - 1, below + 1)
  return sorted[below] + (sorted[above] - sorted[below]) * (place - below)
}

function levelsOf(values: Float64Array): number[] {
  const sorted = Float64Array.from(values).sort()
  return LEVELS.map((level) => quantile(sorted, level / 100))
}

/** The paths, from ``start`` euros, with a cap on the stake or none; the levels where the charts and horizons need them. */
export function simulate(input: ProjectionInput, start: number, cap: number | null, paths = PATHS): Simulated {
  const { days, growths } = input
  const early = input.earlyGrowths.length > 0 ? input.earlyGrowths : growths
  const total = days.length
  const from = days[0]?.day ?? ''
  const horizonPlaces = HORIZONS.map((months) => ({ months, index: horizonIndex(days, from, months) }))
  const kept = new Set<number>()
  for (const { index } of horizonPlaces) for (const place of chartPlaces(index)) kept.add(place)

  const accounts = new Float64Array(paths).fill(start)
  const peaks = new Float64Array(paths).fill(start)
  const falls = new Float64Array(paths)
  const levels = new Map<number, number[]>()
  levels.set(0, LEVELS.map(() => start))
  const ended = new Map<number, { values: Float64Array; falls: Float64Array }>()
  const random = mulberry32(input.seed)
  const atHorizon = new Set(horizonPlaces.map(({ index }) => index))

  for (let place = 1; place <= total && growths.length > 0; place += 1) {
    const pool = days[place - 1].kind === 'early_close' ? early : growths
    for (let path = 0; path < paths; path += 1) {
      const account = nextAccount(accounts[path], pool[Math.floor(random() * pool.length)], cap)
      accounts[path] = account
      if (account > peaks[path]) peaks[path] = account
      const fall = peaks[path] > 0 ? 1 - account / peaks[path] : 0
      if (fall > falls[path]) falls[path] = fall
    }
    if (kept.has(place)) levels.set(place, levelsOf(accounts))
    if (atHorizon.has(place)) ended.set(place, { values: Float64Array.from(accounts), falls: Float64Array.from(falls) })
  }

  const average = geometricMean(growths)
  const averageEarly = geometricMean(early)
  const pace = [start]
  for (const day of days) pace.push(nextAccount(pace[pace.length - 1], day.kind === 'early_close' ? averageEarly : average, cap))

  const horizons = horizonPlaces.map(({ months, index }): HorizonFigures => {
    const end = ended.get(index)
    const values = end?.values ?? new Float64Array(paths).fill(start)
    const sortedFalls = Float64Array.from(end?.falls ?? new Float64Array(paths)).sort()
    let belowStart = 0
    let belowHalf = 0
    for (const value of values) {
      if (value < start) belowStart += 1
      if (value < start / 2) belowHalf += 1
    }
    return {
      months,
      index,
      day: index === 0 ? from : days[index - 1].day,
      levels: levels.get(index) ?? LEVELS.map(() => start),
      pace: pace[index],
      belowStart: belowStart / paths,
      belowHalf: belowHalf / paths,
      fallMedian: quantile(sortedFalls, 0.5) * 100,
      fallBad: quantile(sortedFalls, 0.95) * 100,
    }
  })
  return { start, cap, levels, horizons, pace }
}

/** An amount in the unit shown: euros, or percent of the start. */
export function inUnit(value: number, unit: Unit, start: number): number {
  return unit === 'money' ? value : start > 0 ? (value / start - 1) * 100 : 0
}

export function formatIn(value: number, unit: Unit): string {
  return unit === 'money' ? formatMoney(value, 'EUR') : formatPercent(value, true)
}

/** The chart's places up to the horizon: the two bands, and the run's pace as a line. */
export function fanPoints(simulated: Simulated, horizon: HorizonFigures, unit: Unit): FanPoint[] {
  return chartPlaces(horizon.index).map((place) => {
    const values = simulated.levels.get(place) ?? LEVELS.map(() => simulated.start)
    const [p5, p25, p50, p75, p95] = values.map((value) => inUnit(value, unit, simulated.start))
    return { x: place, p5, p25, p50, p75, p95, pace: inUnit(simulated.pace[place], unit, simulated.start) }
  })
}

/** The day of a place on the chart: now, or the trading day it is; a tick between two is rounded to the nearer. */
export function dayOfPlace(days: ProjectionDay[], from: string, place: number): string {
  const index = Math.min(days.length, Math.max(0, Math.round(place)))
  return index === 0 ? from : days[index - 1].day
}

const MONTH_YEAR = new Intl.DateTimeFormat('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })

/** A tick of the chart's axis: the day over a short horizon, the month and year over a long one. */
export function tickLabel(day: string, months: Horizon): string {
  return months <= 6 ? formatDay(day, false) : MONTH_YEAR.format(new Date(`${day}T12:00:00Z`))
}

/** The places marked across the chart: each new year over a long horizon, and where the holidays become estimated. */
export function markers(days: ProjectionDay[], horizon: HorizonFigures, estimatedFrom: string | null): FanMarker[] {
  const marks: FanMarker[] = []
  let year = days[0]?.day.slice(0, 4)
  days.slice(0, horizon.index).forEach((day, index) => {
    const its = day.day.slice(0, 4)
    if (its !== year && horizon.months >= 12) marks.push({ x: index + 1, label: its })
    year = its
    if (estimatedFrom && day.day === estimatedFrom) marks.push({ x: index + 1, label: 'Holidays estimated from here' })
  })
  return marks
}

/** The days a horizon counts, and those the trader will not trade, in words. */
export function daysSentence(days: ProjectionDay[], skipped: ProjectionSkipped[], horizon: HorizonFigures): string {
  const counted = days.slice(0, horizon.index)
  const early = counted.filter((day) => day.kind === 'early_close').length
  const off = skipped.filter((day) => day.day <= horizon.day).length
  const parts = [`${counted.length} trading day${counted.length === 1 ? '' : 's'} to ${formatDay(horizon.day, true)}`]
  parts.push(early === 0 ? 'none closes early' : `${early} close${early === 1 ? 's' : ''} early, drawn as mornings`)
  parts.push(`${off} weekday${off === 1 ? '' : 's'} off (US holidays and WATA's closed dates)`)
  return `${parts.join(', ')}.`
}

/** The weekdays off within a horizon and the early closes, each with its name, for the list under the chart. */
export function daysOff(days: ProjectionDay[], skipped: ProjectionSkipped[], horizon: HorizonFigures) {
  const off = skipped
    .filter((day) => day.day <= horizon.day)
    .map((day) => ({ day: day.day, name: day.name, what: 'Closed', estimated: day.estimated }))
  const early = days
    .slice(0, horizon.index)
    .filter((day) => day.kind === 'early_close')
    .map((day) => ({ day: day.day, name: day.name ?? 'Early close', what: `Closes at ${day.close_new_york ?? '13:00'} New York`, estimated: day.estimated }))
  return [...off, ...early].sort((a, b) => a.day.localeCompare(b.day))
}

const LEFT_OUT_WHY: Record<ProjectionLeftOut['why'], string> = {
  us_closed: 'a US holiday, traded at nothing',
  closed_date: 'a closed date of the run',
  after_guard: 'after the account guard paused its entries',
  after_empty: 'after the account was emptied',
}

/** The run's days left out of the pace, in words; null when none was. */
export function leftOutSentence(leftOut: ProjectionLeftOut[]): string | null {
  if (leftOut.length === 0) return null
  const groups = new Map<ProjectionLeftOut['why'], ProjectionLeftOut[]>()
  for (const day of leftOut) groups.set(day.why, [...(groups.get(day.why) ?? []), day])
  const said = [...groups].map(([why, days]) => {
    const named = why === 'us_closed' ? ` (${days.map((day) => `${day.name ?? ''}, ${formatDay(day.day, false)}`).join('; ')})` : ''
    return `${days.length} ${days.length === 1 ? 'day' : 'days'} ${LEFT_OUT_WHY[why]}${named}`
  })
  return `Left out of its pace: ${said.join('; ')}.`
}

/** What a start far from the run's own capital changes; null when it is near. */
export function startNote(start: number, capital: number): string | null {
  if (capital <= 0 || start <= 0) return null
  const ratio = start / capital
  if (ratio <= FAR_FROM_CAPITAL && ratio >= 1 / FAR_FROM_CAPITAL) return null
  return `The run started with ${formatMoney(capital, 'EUR')}: at ${formatMoney(start, 'EUR')}, its fixed costs and whole turbos weigh otherwise. Make the run again with that capital for its own pace.`
}

/** The run's average trading day, in words. */
export function paceSentence(projection: BacktestProjection): string | null {
  const pace = projection.pace
  if (!pace || pace.geometric_mean_percent === null) return null
  return `Its average trading day: ${formatPercent(pace.geometric_mean_percent, true, 3)} of the account, over ${pace.days_used.length} day${pace.days_used.length === 1 ? '' : 's'} and ${pace.trades_used} trade${pace.trades_used === 1 ? '' : 's'}.`
}

/** The share of paths, in words. */
export function shareText(share: number): string {
  if (share === 0) return 'none'
  if (share < 0.005) return 'under 1 %'
  return `${Math.round(share * 100)} %`
}
