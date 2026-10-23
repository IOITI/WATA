import type { NamedCurve } from '@/components/charts/PerformanceCharts'
import type { BadgeTone } from '@/components/ui/badge'
import { madeByClaude } from '@/features/system/claude'
import { barsLabel } from '@/features/ta/signals/ruleModel'
import { formatBytes, formatDay, formatPercent } from '@/lib/format'
import type {
  BacktestAgainstRandom,
  BacktestAsset,
  BacktestCurvePoint,
  BacktestDayStatus,
  BacktestFate,
  BacktestField,
  BacktestFigures,
  BacktestProgress,
  BacktestRun,
  BacktestRunListed,
  BacktestSample,
  BacktestState,
  BacktestSummary,
  BacktestTurbos,
  BacktestValue,
  DailyPerformance,
  ResearchFiles,
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

export const FATE_LABEL: Record<BacktestFate, string> = {
  executed: 'Bought',
  refused: 'Refused',
  failed: 'Failed',
  unpriced: 'No recorded turbo',
  closed: 'Closed positions',
  ignored: 'Nothing to close',
  not_a_signal: 'Not a signal',
}

export const FATE_TONE: Record<BacktestFate, BadgeTone> = {
  executed: 'success',
  refused: 'neutral',
  failed: 'danger',
  unpriced: 'warning',
  closed: 'success',
  ignored: 'neutral',
  not_a_signal: 'neutral',
}

/** What refused or failed an entry, by the name the run gives it, in words. */
export const RULE_LABEL: Record<string, string> = {
  signal_age: 'Signal too old',
  closed_date: 'Market closed that day',
  market_hours: 'Outside the trading hours',
  risky_hours: 'Risky hour of the day',
  indice: 'Indice not traded',
  duplicate: 'Same direction already open',
  day_profit: 'The day made enough',
  day_loss: 'The day lost too much',
  cooldown: 'Cooldown after a loss',
  max_trades: 'Most entries of the day',
  confidence: 'Confidence too low',
  no_recorded_turbo: 'No turbo recorded',
  recording_gap: 'Recorder not listening',
  no_market: 'No turbo quoted',
  no_turbo_in_range: 'No turbo in the price range',
  no_ask: 'No ask quoted',
  insufficient_funds: 'Account too small',
  order_rejected: 'Order not filled',
  not_a_signal: 'Not a signal',
  unknown_action: 'Unknown action',
}

export function ruleLabel(rule: string | null | undefined): string {
  if (!rule) return '-'
  return RULE_LABEL[rule] ?? rule.replace(/_/g, ' ')
}

export const DAY_STATUS_LABEL: Record<BacktestDayStatus, string> = {
  tested: 'Tested',
  no_recorded_turbos: 'No turbo recorded',
  no_strategy_bars: 'No bars of the asset',
  closed: 'Market closed',
}

/** A day's status as a run says it: on made-up turbos, a day without turbos is one without the index's prices. */
export function dayStatusLabel(status: BacktestDayStatus, turbos: BacktestTurbos = 'recorded'): string {
  return turbos === 'modelled' && status === 'no_recorded_turbos' ? 'No price of the index' : DAY_STATUS_LABEL[status]
}

/** What a run's page and its day's say when the address names no run. */
export const RUN_NOT_FOUND = {
  title: 'No such backtest',
  description: 'No run has this address: it may have been removed, or the address is mistyped.',
  suggestions: [
    { label: 'Backtests', href: '/backtests', description: 'Every run kept' },
    { label: 'New backtest', href: '/backtests/new', description: 'A strategy on the recorded turbos' },
  ],
}

export const PROVIDER_LABEL: Record<BacktestAsset['provider'], string> = {
  capital: 'Capital.com',
  dukascopy: 'Dukascopy',
  saxo: 'Saxo',
}

export function assetLabel(asset: BacktestAsset): string {
  return `${PROVIDER_LABEL[asset.provider]} ${asset.key}`
}

/** What a run trades on, in a few words. */
export function sourceLabel(run: Pick<BacktestRunListed, 'source' | 'chart'>): string {
  if (run.source.kind === 'journal') return 'Signals WATA received'
  const script = run.source.script
  return `${script ? `${script.name} v${script.version}` : 'Pine script'} · ${assetLabel(run.chart.asset)} · ${barsLabel(run.chart.bars)}`
}

export function progressSentence(progress: BacktestProgress | null | undefined): string {
  if (!progress) return 'Starting.'
  if (progress.phase === 'signals') return 'Running the script over the days of the period.'
  if (progress.phase === 'days') {
    return `Trading the recorded turbos: day ${Math.min(progress.done + 1, progress.total)} of ${progress.total}${progress.day ? ` (${progress.day})` : ''}.`
  }
  return 'Writing what it found.'
}

/** A setting's value as the form and the review show it. */
export function showValue(value: BacktestValue | null | undefined, field?: Pick<BacktestField, 'kind' | 'unit'>): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'boolean') return value ? 'On' : 'Off'
  const unit = field?.unit && field.unit !== 'local time' && field.unit !== 'UTC' ? ` ${field.unit}` : ''
  return `${value}${unit}`
}

export interface Change {
  name: string
  label: string
  before: string
  after: string
}

/** What a run changed of the live settings, with the value it had. */
export function settingChanges(
  changed: Record<string, BacktestValue>,
  before: Record<string, BacktestValue>,
  fields: readonly BacktestField[],
): Change[] {
  return Object.entries(changed).map(([name, value]) => {
    const field = fields.find((entry) => entry.name === name)
    return {
      name,
      label: field?.label ?? name.replace(/_/g, ' '),
      before: showValue(before[name] ?? field?.value, field),
      after: showValue(value, field),
    }
  })
}

// ── the days as the performance charts take them ──

/** The days of a run as the charts of the Performance page take them, in percent or in money. */
export function curveDays(curve: readonly BacktestCurvePoint[], unit: 'percent' | 'money', capital: number): DailyPerformance[] {
  return curve.map((point) => ({
    day_date: point.day,
    trade_count: 0,
    win_count: 0,
    loss_count: 0,
    profit_loss: unit === 'percent' ? point.percent : point.profit_loss,
    cumulative_profit_loss: unit === 'percent' ? point.cumulative : (point.equity ?? capital) - capital,
    performance_percent: point.percent,
  }))
}

/** A tally by name as rows, the most frequent first, with each one's share. */
export function tally(counts: Record<string, number> | undefined): { name: string; count: number; share: number }[] {
  const entries = Object.entries(counts ?? {})
  const total = entries.reduce((sum, [, count]) => sum + count, 0)
  return entries
    .map(([name, count]) => ({ name, count, share: total > 0 ? count / total : 0 }))
    .sort((first, second) => second.count - first.count)
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

/** Where a run stands among the random entries: above nearly all, below nearly all, or among them. */
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
  from: number
  to: number
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

function signed(percent: number): string {
  return formatPercent(percent, true)
}

/** The random entries of a run in a sentence. */
export function randomSentence(found: BacktestAgainstRandom): string {
  const entries = `${found.entries} signal${found.entries === 1 ? '' : 's'} of entry`
  return (
    `The same days traded ${found.repetitions} times with its ${entries} put at random moments of the hours they came at, long or short by a coin, ` +
    `on the same recorded turbos and under the same rules: half of them ended above ${signed(found.median)}, ` +
    `${found.level} in 100 between ${signed(found.low)} and ${signed(found.high)}.`
  )
}

/** What the trades of a run say about where a stop and a target could be. */
export function roomSentences(figures: Pick<BacktestFigures, 'winners_lowest' | 'losers_highest'>): string[] {
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

/** What the days of a run were: how many tested, and why the others were not. */
export function daysSentence(days: BacktestSummary['days']): string {
  const parts = [`${days.tested} of the ${days.asked} day${days.asked === 1 ? '' : 's'} asked had turbos recorded and were traded`]
  const statuses = days.by_status ?? {}
  if (statuses.no_recorded_turbos) parts.push(`${statuses.no_recorded_turbos} had no turbo recorded`)
  if (statuses.no_strategy_bars) parts.push(`${statuses.no_strategy_bars} had no bars of the strategy's asset`)
  if (statuses.closed) parts.push(`${statuses.closed} the market was closed`)
  return `${parts.join('; ')}.`
}

/** How long a trade was held, in a few words. */
export function heldFor(seconds: number): string {
  if (seconds < 120) return `${seconds} s`
  if (seconds < 7200) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

/** The local day of a moment in a timezone, YYYY-MM-DD. */
export function localDay(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso))
}

/** The runs a page compares: those selected that are done, four at most, in the order they were made. */
export function comparable(runs: readonly BacktestRunListed[], selected: ReadonlySet<string>): BacktestRunListed[] {
  return runs
    .filter((run) => selected.has(run.id) && run.status.state === 'done' && run.kind === 'run')
    .sort((first, second) => first.id.localeCompare(second.id))
    .slice(0, 4)
}

// ── runs side by side ──

export interface DifferingRow {
  name: string
  label: string
  /** Each run's value, in the order of the runs. */
  values: string[]
}

/** The settings runs did not share, in the order of the form, with each run's value. */
export function differingSettings(
  runs: readonly Pick<BacktestRun, 'settings'>[],
  fields: readonly BacktestField[],
): DifferingRow[] {
  const rows: DifferingRow[] = []
  for (const field of fields) {
    const values = runs.map((run) => run.settings.settings[field.name] as BacktestValue | undefined)
    if (new Set(values.map((value) => JSON.stringify(value ?? null))).size > 1) {
      rows.push({ name: field.name, label: field.label, values: values.map((value) => showValue(value, field)) })
    }
  }
  return rows
}

/** The script inputs runs did not share: a run that read no such input (or no script) shows a dash. */
export function differingInputs(runs: readonly Pick<BacktestRun, 'request'>[]): DifferingRow[] {
  const inputs = runs.map((run) => (run.request.source.kind === 'pine' ? run.request.source.rule.inputs : []))
  const names = new Map<string, string>()
  for (const list of inputs) for (const input of list) if (!names.has(input.id)) names.set(input.id, input.title ?? input.id)
  const rows: DifferingRow[] = []
  for (const [id, label] of names) {
    const values = inputs.map((list) => list.find((input) => input.id === id)?.value)
    if (new Set(values.map((value) => JSON.stringify(value ?? null))).size > 1) {
      rows.push({ name: id, label: `Input: ${label}`, values: values.map((value) => (value === undefined ? '-' : String(value))) })
    }
  }
  return rows
}

/** Each run's days, one applied on the other, as the comparison chart takes them. */
export function compareCurves(runs: readonly Pick<BacktestRun, 'id' | 'name' | 'summary'>[]): NamedCurve[] {
  return runs.map((run) => ({
    id: run.id,
    name: run.name,
    points: (run.summary?.curve ?? []).map((point) => ({ day: point.day, value: point.cumulative })),
  }))
}

/** What to know before reading runs side by side; nothing when they are alike. */
export function comparisonWarnings(runs: readonly Pick<BacktestRun, 'from' | 'to' | 'indice' | 'stale'>[]): string[] {
  const warnings: string[] = []
  if (new Set(runs.map((run) => `${run.from}/${run.to}`)).size > 1) {
    warnings.push('These runs did not cover the same days: part of what differs between them is the days.')
  }
  if (new Set(runs.map((run) => run.indice)).size > 1) {
    warnings.push('These runs traded the turbos of different indices.')
  }
  if (runs.some((run) => run.stale)) {
    warnings.push('One of them was made by other code than this admin’s: it could come out otherwise today.')
  }
  return warnings
}

// ── the research files ──

const STATE_SENTENCE: Record<ResearchFiles['state'], string> = {
  off: '',
  waiting_for_close: 'Days not written yet wait for the market to close.',
  up_to_date: 'Every finished day is written.',
  due: 'Writing the days not written yet.',
  exporting: 'Writing a day now.',
}

/** What the server writes each finished day for a research machine to pull, and its switch. */
export function researchSentence(status: ResearchFiles): string {
  if (!status.enabled) return 'Each finished day can be written as files for a research machine to pull (rsync), beside the price history.'
  const days =
    status.days > 0
      ? `${status.days} day${status.days === 1 ? '' : 's'} written (${formatDay(status.first)} to ${formatDay(status.last)}), ${formatBytes(status.bytes)}`
      : 'No day written yet'
  const writing = status.exporting ? ` Writing ${formatDay(status.exporting)}.` : ` ${STATE_SENTENCE[status.state]}`
  return `Research files on: ${days}; the last ${status.keep_days} are kept.${writing}`
}

// ── the list of runs ──

/** What the list of runs is narrowed to, as its address says it (?state=failed&by=claude…). */
export interface RunFilter {
  state: 'active' | 'done' | 'failed' | 'stopped' | null
  by: 'me' | 'claude' | null
  experiment: string | null
  turbos: BacktestTurbos | null
  code: 'current' | 'older' | null
}

export const RUN_FILTER_NAMES = ['state', 'by', 'experiment', 'turbos', 'code'] as const

const STATE_GROUP: Record<BacktestState, NonNullable<RunFilter['state']>> = {
  queued: 'active',
  running: 'active',
  done: 'done',
  failed: 'failed',
  interrupted: 'failed',
  cancelled: 'stopped',
}

/** The filter an address names; what it does not know is left out. */
export function runFilterOf(params: URLSearchParams): RunFilter {
  const one = <T extends string>(name: string, known: readonly T[]): T | null => {
    const value = params.get(name)
    return known.includes(value as T) ? (value as T) : null
  }
  return {
    state: one('state', ['active', 'done', 'failed', 'stopped']),
    by: one('by', ['me', 'claude']),
    experiment: params.get('experiment') || null,
    turbos: one('turbos', ['recorded', 'modelled']),
    code: one('code', ['current', 'older']),
  }
}

export function filterRuns(runs: readonly BacktestRunListed[], filter: RunFilter): BacktestRunListed[] {
  return runs.filter(
    (run) =>
      (!filter.state || STATE_GROUP[run.status.state] === filter.state) &&
      (!filter.by || (filter.by === 'claude') === madeByClaude(run)) &&
      (!filter.experiment || run.experiment === filter.experiment) &&
      (!filter.turbos || (run.turbos ?? 'recorded') === filter.turbos) &&
      (!filter.code || (filter.code === 'older') === Boolean(run.stale)),
  )
}

/** What makes two runs the same request, asked again after one failed: everything but when. */
function requestKey(run: BacktestRunListed): string {
  return JSON.stringify([
    run.kind, run.name, run.from, run.to, run.indice, run.source.kind, run.source.script?.id ?? null, run.source.script?.version ?? null,
    run.chart, run.changes, run.turbos ?? 'recorded', run.stress_of ?? null, run.model_check_of ?? null,
  ])
}

/**
 * The failed runs a later run of the same request stands for, listed under it
 * rather than as rows of their own: a run asked again and again until it
 * finished (13 of 32 on the server on 22 Oct 2026). The runs come newest first.
 */
export function collapseAttempts(runs: readonly BacktestRunListed[]): { shown: BacktestRunListed[]; earlier: Map<string, BacktestRunListed[]> } {
  const keeper = new Map<string, string>()
  const earlier = new Map<string, BacktestRunListed[]>()
  const shown: BacktestRunListed[] = []
  for (const run of runs) {
    const key = requestKey(run)
    const kept = keeper.get(key)
    if (kept && (run.status.state === 'failed' || run.status.state === 'interrupted')) {
      earlier.set(kept, [...(earlier.get(kept) ?? []), run])
      continue
    }
    shown.push(run)
    if (!kept) keeper.set(key, run.id)
  }
  return { shown, earlier }
}

/** What a failed run said last: the sentence its error ends with. */
export function failureLine(run: Pick<BacktestRunListed, 'status'>): string {
  const lines = (run.status.error ?? '').split('\n').map((line) => line.trim()).filter(Boolean)
  return lines[lines.length - 1] ?? ''
}
