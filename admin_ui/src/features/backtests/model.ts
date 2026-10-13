import type { BadgeTone } from '@/components/ui/badge'
import { formatPercent } from '@/lib/format'
import type {
  BacktestAgainstRandom,
  BacktestCalibrationResult,
  BacktestDay,
  BacktestEditableRule,
  BacktestExecution,
  BacktestFigures,
  BacktestParameter,
  BacktestRun,
  BacktestRunListed,
  BacktestSample,
  BacktestStale,
  BacktestState,
  BacktestSweep,
  BacktestTrade,
  BacktestTradePath,
  BacktestValue,
  DailyPerformance,
  PriceKeepStatus,
  SweepAxis,
  SweepCell,
} from '@/lib/types'

/**
 * What the backtest pages work out from what the server sends. Nothing here
 * draws or asks for anything.
 */

export const STATE_LABEL: Record<BacktestState, string> = {
  queued: 'Waiting',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Stopped',
  interrupted: 'Interrupted',
}

export const STATE_TONE: Record<BacktestState, BadgeTone> = {
  queued: 'neutral',
  running: 'warning',
  done: 'success',
  failed: 'danger',
  cancelled: 'neutral',
  interrupted: 'danger',
}

/** Waiting or running: its state will change by itself. */
export function isActive(state: BacktestState): boolean {
  return state === 'queued' || state === 'running'
}

/** A parameter's name as words: "min_band_width" is "Min band width". */
export function wording(name: string): string {
  const words = name.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function showValue(value: BacktestValue | undefined): string {
  if (value === undefined) return '-'
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return String(value)
}

export interface Change {
  name: string
  label: string
  before: string
  after: string
}

/** The parameters a run changed, against the strategy's defaults. */
export function parameterChanges(run: BacktestRunListed, parameters: readonly BacktestParameter[] = []): Change[] {
  const defaults = new Map(parameters.map((parameter) => [parameter.name, parameter.default]))
  return Object.entries(run.parameters).map(([name, value]) => ({
    name,
    label: wording(name),
    before: showValue(defaults.get(name)),
    after: showValue(value),
  }))
}

/** The rules a run changed, against what the live configuration said when it was asked for. */
export function ruleChanges(run: BacktestRunListed, rules: readonly BacktestEditableRule[] = []): Change[] {
  const labels = new Map(rules.map((rule) => [rule.name, rule.label]))
  return Object.entries(run.rules).map(([name, value]) => ({
    name,
    label: labels.get(name) ?? wording(name),
    before: showValue(run.rules_before?.[name]),
    after: showValue(value),
  }))
}

/** The signals WATA really received, replayed: not a strategy. */
export function isReplay(run: BacktestRunListed): boolean {
  return run.kind === 'signals'
}

/** Several values tried for one or two settings, in one run. */
export function isSweep(run: BacktestRunListed): boolean {
  return run.kind === 'sweep'
}

/** In a few words, what sets a run apart: for a list. */
export function whatDiffers(run: BacktestRunListed): string {
  const parts = [
    ...Object.entries(run.parameters).map(([name, value]) => `${wording(name).toLowerCase()} ${showValue(value)}`),
    ...Object.entries(run.rules).map(([name, value]) => `${wording(name).toLowerCase()} ${showValue(value)}`),
  ]
  if (isSweep(run)) {
    const swept = (run.axes ?? []).map((axis) => wording(axis.name).toLowerCase()).join(' × ')
    return [`Sweep of ${swept}${run.replays === 'signals' ? ' on real signals' : ''}`, ...parts].join(' · ')
  }
  if (isReplay(run)) return ['Real signals', ...parts].join(' · ')
  return parts.length > 0 ? parts.join(' · ') : 'Defaults and live rules'
}

/** What to call a run: its note, or else what it changed. */
export function runName(run: BacktestRunListed): string {
  return run.note || whatDiffers(run)
}

/** The leverage shown first: the middle one, as in the list. */
export function middleLeverage(leverages: readonly number[]): number {
  return leverages[Math.floor(leverages.length / 2)]
}

export function figuresAt(run: BacktestRun, leverage: number): BacktestFigures | null {
  return run.summary?.runs.find((entry) => entry.turbo.points_per_unit === leverage)?.summary ?? null
}

/** Several results in percent as one, each applied on the one before. */
export function compounded(percents: readonly number[]): number {
  const value = percents.reduce((product, percent) => product * (1 + percent / 100), 1)
  return Math.round((value - 1) * 10000) / 100
}

/** The calendar day of a moment in a timezone, as YYYY-MM-DD. */
export function localDay(iso: string, timeZone: string): string {
  // The Canadian form of a date is the ISO one.
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(
    new Date(iso),
  )
}

/**
 * The days of a run as the performance charts take them. The "amounts" are
 * percentages: the result of each day, and all days so far applied on one
 * another. A trade counts on the day it was closed.
 */
export function dailyFromBacktest(
  days: readonly BacktestDay[],
  trades: readonly BacktestTrade[],
  timeZone: string,
): DailyPerformance[] {
  const won = new Map<string, number>()
  const lost = new Map<string, number>()
  for (const trade of trades) {
    const tally = trade.performance_percent > 0 ? won : lost
    const day = localDay(trade.exit_time, timeZone)
    tally.set(day, (tally.get(day) ?? 0) + 1)
  }

  const soFar: number[] = []
  return days.map((day) => {
    soFar.push(day.day_percent)
    return {
      day_date: day.day,
      trade_count: day.closed,
      win_count: won.get(day.day) ?? 0,
      loss_count: lost.get(day.day) ?? 0,
      profit_loss: day.day_percent,
      cumulative_profit_loss: compounded(soFar),
      performance_percent: day.day_percent,
    }
  })
}

/** A tally by reason as rows, the most frequent first, with each one's share. */
export function tally(counts: Record<string, number>): { reason: string; count: number; share: number }[] {
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0)
  return Object.entries(counts)
    .map(([reason, count]) => ({ reason, count, share: total > 0 ? count / total : 0 }))
    .sort((a, b) => b.count - a.count)
}

// ── how far a run can be trusted ──

/** What to say of the number of trades and days a run rests on; nothing when there are enough. */
export function sampleWarning(sample: BacktestSample | undefined, trades: number, days: number): string | null {
  const counted = `${trades} trade${trades === 1 ? '' : 's'} over ${days} day${days === 1 ? '' : 's'}`
  if (sample === 'too_few') {
    return `${counted}: too few to conclude. Under 30 trades or 20 days, these figures describe those days and nothing else.`
  }
  if (sample === 'few') {
    return `${counted}: enough to see a direction, not to rely on a figure. A hundred trades over sixty days is where figures start to hold.`
  }
  return null
}

export type RandomVerdict = 'above' | 'inside' | 'below'

/**
 * Where a run stands among the random entries: above nearly all of them,
 * below nearly all, or among them, which is where it has shown nothing.
 */
export function randomVerdict(beaten: number): RandomVerdict {
  if (beaten >= 95) return 'above'
  return beaten <= 5 ? 'below' : 'inside'
}

export const RANDOM_VERDICT: Record<RandomVerdict, string> = {
  above: 'Better than nearly every run of random entries: the entries count for something here.',
  inside: 'Among the results random entries give: on these days, the entries have shown nothing the exit rules do not explain.',
  below: 'Worse than nearly every run of random entries: the entries cost more than tossing a coin.',
}

export interface Bin {
  /** The bin holds the totals from `from` (included) to `to`. */
  from: number
  to: number
  /** Random repetitions that ended in it. */
  count: number
  /** The run's own total falls in it. */
  marked: boolean
}

/**
 * The totals of the random repetitions as the bars of a distribution: bins of
 * equal width, from the lowest to the highest of them and of the run's own
 * total, which has its place among them even when it is beyond them all.
 */
export function distribution(totals: readonly number[], own: number, bins = 20): Bin[] {
  if (totals.length === 0) return []
  const least = Math.min(own, ...totals)
  const most = Math.max(own, ...totals)
  // Everything ended the same: one bar, of some width to be seen.
  if (most === least) return [{ from: least - 0.5, to: least + 0.5, count: totals.length, marked: true }]
  const width = (most - least) / bins
  const place = (total: number) => Math.min(bins - 1, Math.floor((total - least) / width))
  const counted = Array.from({ length: bins }, (_, index) => ({
    from: least + index * width,
    to: least + (index + 1) * width,
    count: 0,
    marked: index === place(own),
  }))
  for (const total of totals) counted[place(total)].count += 1
  return counted
}

/** The random entries of a run in a sentence. */
export function randomSentence(found: BacktestAgainstRandom): string {
  const entries = `${found.entries} entr${found.entries === 1 ? 'y' : 'ies'}`
  return (
    `The same days traded ${found.repetitions} times with ${entries} at random moments, long or short by a coin, ` +
    `under the same rules: half of them ended above ${signed(found.median)}, ` +
    `${found.level} in 100 between ${signed(found.low)} and ${signed(found.high)}.`
  )
}

function signed(percent: number): string {
  return formatPercent(percent, true)
}

/** What the figures of a run say about where a stop and a target could be. */
export function roomSentences(figures: BacktestFigures): string[] {
  const sentences: string[] = []
  if (figures.winners_lowest) {
    sentences.push(
      `Half of the winning trades never fell below ${signed(figures.winners_lowest.median)}, ` +
        `nine in ten never below ${signed(figures.winners_lowest.tenth)}: a stop tighter than that would have sold winners.`,
    )
  }
  if (figures.losers_highest) {
    sentences.push(
      `Half of the losing trades had been above ${signed(figures.losers_highest.median)} before they lost, ` +
        `one in ten above ${signed(figures.losers_highest.tenth)}.`,
    )
  }
  return sentences
}

// ── how a run was made ──

/** How the fills of a run were taken, in a sentence; nothing when they were as kind as the bars. */
export function executionSentence(execution: BacktestExecution | undefined): string | null {
  if (!execution || (execution.fill_delay === 0 && execution.slippage === 0)) return null
  const parts = []
  if (execution.fill_delay > 0) parts.push(`each decision is filled ${execution.fill_delay} s after the next ten seconds`)
  if (execution.slippage > 0) {
    parts.push(`each fill loses ${execution.slippage} index point${execution.slippage === 1 ? '' : 's'}`)
  }
  const sentence = parts.join(', and ')
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`
}

/** Why a run would come out otherwise today; nothing when it would not, or when it cannot be told. */
export function staleSentence(stale: BacktestStale | null | undefined): string | null {
  if (!stale || (!stale.code && !stale.prices)) return null
  const reasons = []
  if (stale.code) reasons.push(`the code that replays has changed since (it was made with WATA ${stale.version ?? 'of another version'})`)
  if (stale.prices) reasons.push('the prices of its period are not the ones it read')
  return `This run would come out otherwise today: ${reasons.join(', and ')}. Run it again before comparing it with a new one.`
}

/** A size on disk, in the unit that reads best. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-'
  const units = ['B', 'kB', 'MB', 'GB']
  let size = bytes
  let unit = 0
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000
    unit += 1
  }
  return `${unit === 0 || size >= 100 ? Math.round(size) : size.toFixed(1)} ${units[unit]}`
}

// ── one trade followed ──

export interface PathLevel {
  id: 'stop' | 'day_target' | 'take_profit'
  label: string
  value: number
  tone: 'negative' | 'positive'
}

export interface PathView {
  /** The percentages the chart goes from and to. */
  domain: [number, number]
  /** The levels near enough to what the trade did to be drawn with it. */
  shown: PathLevel[]
  /** The others, said in words: drawn, they would flatten the trade into a line. */
  away: PathLevel[]
}

/**
 * What a chart of a trade's path shows: from its worst to its best, and the
 * levels that could have sold it when they are no further from that than
 * the trade itself went.
 */
export function pathView(path: BacktestTradePath): PathView {
  const lowest = Math.min(0, ...path.points.map((point) => point[2]))
  const highest = Math.max(0, ...path.points.map((point) => point[3]))
  // At least a point of percent each way, so that a trade that went nowhere is not a chart of noise.
  const reach = Math.max(highest - lowest, 1)
  const levels: PathLevel[] = [
    { id: 'stop', label: 'Stop', value: path.levels.stop, tone: 'negative' },
    { id: 'day_target', label: 'Day target', value: path.levels.day_target, tone: 'positive' },
    { id: 'take_profit', label: 'Take-profit', value: path.levels.take_profit, tone: 'positive' },
  ]
  const near = (level: PathLevel) => level.value >= lowest - reach && level.value <= highest + reach
  const shown = levels.filter(near)
  const edge = reach * 0.08
  return {
    domain: [
      Math.min(lowest, ...shown.map((level) => level.value)) - edge,
      Math.max(highest, ...shown.map((level) => level.value)) + edge,
    ],
    shown,
    away: levels.filter((level) => !near(level)),
  }
}

// ── a sweep ──

export interface Metric {
  id: string
  label: string
  /** What is read in the figures of a cell. */
  read: (figures: BacktestFigures) => number | null
  /** Above and below zero mean gain and loss; else more is only more. */
  signed: boolean
  percent: boolean
}

export const METRICS: Metric[] = [
  { id: 'total', label: 'All days', read: (figures) => figures.total_percent, signed: true, percent: true },
  { id: 'expectancy', label: 'Per trade', read: (figures) => figures.expectancy ?? null, signed: true, percent: true },
  { id: 'win_rate', label: 'Winning', read: (figures) => figures.win_rate, signed: false, percent: true },
  { id: 'drawdown', label: 'Worst drawdown', read: (figures) => figures.worst_drawdown, signed: true, percent: true },
  { id: 'trades', label: 'Trades', read: (figures) => figures.trades, signed: false, percent: false },
]

export type Part = 'tuning' | 'held_out'

export interface GridCell {
  /** Its place among the cells of the sweep: what a backtest of its own is asked by. */
  index: number
  value: number | null
}

export interface Grid {
  /** The values of the first axis, one per row; of the second, one per column (one column when there is none). */
  rows: BacktestValue[]
  columns: BacktestValue[]
  cells: GridCell[][]
}

/** The cells of a sweep as a table: a row for each value of its first setting, a column for each of its second. */
export function sweepGrid(sweep: BacktestSweep, metric: Metric, part: Part): Grid {
  const [first, second] = sweep.axes
  const columns = second ? second.values : ['']
  const cells = first.values.map((_, row) =>
    columns.map((__, column) => {
      const index = row * columns.length + column
      const figures = sweep.cells[index]?.[part]
      return { index, value: figures ? metric.read(figures) : null }
    }),
  )
  return { rows: first.values, columns, cells }
}

export interface Heat {
  tone: 'positive' | 'negative' | 'neutral'
  /** From 0, nothing, to 1, the most of the grid. */
  strength: number
}

/**
 * How strongly a cell is tinted among the values of its grid. For a figure
 * that gains or loses: by how far it is from zero, on the side it is. For a
 * count or a share: by where it stands between the least and the most.
 */
export function heat(value: number | null, all: readonly (number | null)[], signed: boolean): Heat {
  const known = all.filter((entry): entry is number => entry !== null)
  if (value === null || known.length === 0) return { tone: 'neutral', strength: 0 }
  if (signed) {
    const furthest = Math.max(...known.map(Math.abs))
    if (furthest === 0 || value === 0) return { tone: 'neutral', strength: 0 }
    return { tone: value > 0 ? 'positive' : 'negative', strength: Math.abs(value) / furthest }
  }
  const least = Math.min(...known)
  const most = Math.max(...known)
  return { tone: 'neutral', strength: most === least ? 0 : (value - least) / (most - least) }
}

/** What a cell of a sweep was replayed with: "day profit target 3, kernel bandwidth 8". */
export function cellName(axes: readonly SweepAxis[], cell: SweepCell): string {
  return axes.map((axis, number) => `${wording(axis.name).toLowerCase()} ${showValue(cell.values[number])}`).join(', ')
}

export interface SweepVerdict {
  /** The cell that did best before the days held out. */
  best: number
  sentence: string
  /** Whether the best of the first part was still among the better half on the days held out. */
  held: boolean | null
}

/**
 * What a sweep says about choosing a value: the combination that did best on
 * the days a choice may be made on, and where it then stood on the days that
 * had no say. Null for a sweep without a cell.
 */
export function sweepVerdict(sweep: BacktestSweep): SweepVerdict | null {
  if (sweep.cells.length === 0) return null
  const order = (part: Part) =>
    sweep.cells
      .map((cell, index) => ({ index, total: cell[part]?.total_percent ?? null }))
      .filter((entry): entry is { index: number; total: number } => entry.total !== null)
      .sort((a, b) => b.total - a.total)
  const tuning = order('tuning')
  const best = tuning[0]
  const name = cellName(sweep.axes, sweep.cells[best.index])
  const chosen = `On the ${sweep.held_out.tuning_days} day${sweep.held_out.tuning_days === 1 ? '' : 's'} a value may be chosen on, the best was ${name}: ${formatPercent(best.total, true)}.`
  const heldOut = order('held_out')
  if (heldOut.length === 0) {
    return {
      best: best.index,
      held: null,
      sentence: `${chosen} The period had too few days to hold any out: nothing here says that value would do best again.`,
    }
  }
  const rank = heldOut.findIndex((entry) => entry.index === best.index) + 1
  const then = heldOut[rank - 1].total
  const held = rank <= Math.ceil(heldOut.length / 2)
  const place = rank === 1 ? 'the best again' : `number ${rank} of ${heldOut.length}`
  return {
    best: best.index,
    held,
    sentence:
      `${chosen} On the ${sweep.held_out.days} day${sweep.held_out.days === 1 ? '' : 's'} held out it made ${formatPercent(then, true)}: ${place}. ` +
      (held
        ? 'It held up on days that had no say in the choice.'
        : 'It did not hold up on days that had no say in the choice: it was fitted to the first ones.'),
  }
}

/** The values typed for a setting of a sweep ("8, 12, 15"), or why they cannot be used. */
export function axisValues(
  text: string,
  field: { kind: string; least: number | null; most: number | null; choices?: readonly string[] },
  most: number,
): { values: BacktestValue[] } | { error: string } {
  const parts = text.split(',').map((part) => part.trim()).filter((part) => part !== '')
  if (parts.length < 2) return { error: 'Two values at least, separated by commas.' }
  if (parts.length > most) return { error: `${most} values at most.` }
  if (new Set(parts).size !== parts.length) return { error: 'Each value once.' }
  if (field.kind === 'boolean') {
    const read = parts.map((part) => (['on', 'true', 'yes'].includes(part.toLowerCase()) ? true : ['off', 'false', 'no'].includes(part.toLowerCase()) ? false : null))
    return read.some((value) => value === null) ? { error: 'On or off.' } : { values: read as boolean[] }
  }
  if (field.choices && field.choices.length > 0) {
    const unknown = parts.find((part) => !field.choices?.includes(part))
    return unknown ? { error: `One of ${field.choices.join(', ')}.` } : { values: parts }
  }
  for (const part of parts) {
    const error = fieldError(part, field)
    if (error) return { error: `${part}: ${error}` }
  }
  return { values: parts.map((part) => typed(part, field.kind)) }
}

// ── a replay of real signals beside what WATA did ──

const OUTCOME_ORDER = ['executed', 'refused', 'closed', 'ignored', 'failed']

/** What the trader made of the signals replayed, as its journal has it. */
export function journalSentence(journal: Record<string, number>): string {
  const total = Object.values(journal).reduce((sum, count) => sum + count, 0)
  if (total === 0) return 'No signal was replayed.'
  const known = OUTCOME_ORDER.filter((outcome) => journal[outcome])
  const others = Object.keys(journal).filter((outcome) => !OUTCOME_ORDER.includes(outcome)).sort()
  const parts = [...known, ...others].map((outcome) => `${journal[outcome]} ${outcome}`)
  return `Of the ${total} signal${total === 1 ? '' : 's'} replayed, WATA's journal has ${parts.join(', ')}.`
}

/** How much longer than WATA the model held a trade: "2 min longer", "45 s shorter". */
export function heldLonger(seconds: number): string {
  if (seconds === 0) return 'as long'
  const size = Math.abs(seconds)
  const length = size >= 3600 ? `${(size / 3600).toFixed(1)} h` : size >= 120 ? `${Math.round(size / 60)} min` : `${size} s`
  return `${length} ${seconds > 0 ? 'longer' : 'shorter'}`
}

// ── prices kept up to date, and the turbo measured ──

/** What the service is doing about the prices that are missing, in a sentence. */
export function keepSentence(keep: PriceKeepStatus, formatMoment: (iso: string) => string): string {
  const missing = `${keep.missing} day${keep.missing === 1 ? '' : 's'}`
  switch (keep.state) {
    case 'off':
      return 'Prices are fetched only when asked for below.'
    case 'fetching':
      return `Fetching: ${missing} still to complete.`
    case 'up_to_date':
      return 'Up to date. The day that ends is fetched during the night.'
    case 'waiting_for_close':
      return `${missing} to fetch, once the market has closed: nothing is downloaded while WATA may be trading.`
    case 'backing_off':
      return (
        `${missing} to fetch. The source stopped answering ${keep.failures} time${keep.failures === 1 ? '' : 's'} in a row: ` +
        `left alone until ${keep.next_at ? formatMoment(keep.next_at) : 'later'}.`
      )
    case 'pausing':
      return `${missing} to fetch. Next download ${keep.next_at ? `at ${formatMoment(keep.next_at)}` : 'shortly'}: a few days at a time.`
    case 'due':
      return `${missing} to fetch: the next download starts within a minute.`
  }
}

/** What was measured of the turbo, in sentences. */
export function calibrationSentences(result: BacktestCalibrationResult): string[] {
  const sentences = [
    `${result.trades} closed trade${result.trades === 1 ? '' : 's'}: ${result.used} used` +
      (result.without_prices > 0 ? `, ${result.without_prices} without index prices for their day` : '') +
      (result.set_aside > 0 ? `, ${result.set_aside} set aside as unlike the others` : '') +
      (result.unreadable > 0 ? `, ${result.unreadable} unreadable` : '') +
      '.',
  ]
  if (result.entry_price !== null) {
    sentences.push(
      `WATA paid ${result.entry_price} for a turbo (most between ${result.entry_price_low} and ${result.entry_price_high}).`,
    )
  }
  if (!result.fitted || result.points_per_unit === undefined) {
    sentences.push(`No turbo could be measured: ${result.reason ?? 'not enough trades'}.`)
    return sentences
  }
  const high = result.points_per_unit_high
  sentences.push(
    `1.00 of turbo for ${result.points_per_unit} index points ` +
      `(${result.level} in 100: ${result.points_per_unit_low} to ${high === null || high === undefined ? 'no upper bound' : high}), ` +
      `spread ${result.spread}.`,
  )
  sentences.push(
    `Half of the real trades are within ${result.median_error} point${result.median_error === 1 ? '' : 's'} of percent ` +
      `of what this turbo gives, nine in ten within ${result.ninth_error}.`,
  )
  if (result.sample === 'too_few') {
    sentences.push('Under 30 trades: a first idea, to be measured again when there are more.')
  }
  return sentences
}

/** The parameters and rules that are not the same in every run compared. */
export function differing(runs: readonly BacktestRunListed[]): { parameters: string[]; rules: string[] } {
  const names = (pick: (run: BacktestRunListed) => Record<string, BacktestValue>) => {
    const all = new Set(runs.flatMap((run) => Object.keys(pick(run))))
    return [...all].filter((name) => new Set(runs.map((run) => showValue(pick(run)[name]))).size > 1).sort()
  }
  return { parameters: names((run) => run.parameters), rules: names((run) => run.rules) }
}

/** The leverages every one of the runs was made with. */
export function commonLeverages(runs: readonly BacktestRunListed[]): number[] {
  if (runs.length === 0) return []
  return runs[0].turbo.leverages.filter((leverage) => runs.every((run) => run.turbo.leverages.includes(leverage)))
}

/** A calendar day ("2026-10-12") as a date of the browser's own timezone, which is what a date picker takes. */
export function dayToDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date)
}

// ── the form ──

export type FormValues = Record<string, string>

/** Text typed in a number field, as a number; null while it is not one. */
export function numberOf(text: string): number | null {
  const trimmed = text.trim()
  if (trimmed === '') return null
  const value = Number(trimmed)
  return Number.isFinite(value) ? value : null
}

/** Why what was typed for a parameter or a rule cannot be used, or null. */
export function fieldError(
  text: string,
  field: { kind: string; least: number | null; most: number | null },
): string | null {
  if (field.kind === 'text') return text.trim() === '' ? 'Needed.' : null
  const value = numberOf(text)
  if (value === null) return 'A number is needed here.'
  if (field.kind === 'integer' && !Number.isInteger(value)) return 'A whole number is needed here.'
  if (field.least !== null && field.most !== null && (value < field.least || value > field.most)) {
    return `Between ${field.least} and ${field.most}.`
  }
  return null
}

/** "80, 110, 140" as numbers; null when it is not a list of them. */
export function leveragesOf(text: string): number[] | null {
  const parts = text.split(',').map((part) => part.trim()).filter((part) => part !== '')
  const values = parts.map(Number)
  if (values.length === 0 || values.some((value) => !Number.isFinite(value) || value <= 0)) return null
  return new Set(values).size === values.length ? values : null
}

/** What was typed, as the value the server takes for that kind of field. */
export function typed(text: string, kind: string): BacktestValue {
  return kind === 'text' ? text.trim() : Number(text)
}
