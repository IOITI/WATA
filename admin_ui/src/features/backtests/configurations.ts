// The configurations of the Backtests page: the runs whose result is decided by the same things but their days,
// grouped by the server (src/admin/backtest_configurations.py), each with its latest result and its nightly re-run
// (src/admin/backtest_nightly.py). What can be wrong without a browser is here, with its tests.

import { formatDay, formatSpan } from 'ioiti-ui'
import type { CheckState } from 'ioiti-ui'

import type {
  BacktestConfiguration,
  BacktestConfigurationRun,
  BacktestKeptReason,
  BacktestNightly,
  BacktestNightlyLine,
  BacktestState,
  BacktestValue,
} from '@/lib/types'

import { runFilterOf, type RunFilter } from './model'
import { listedChecks, verdictOf, type Judged } from './verdict'

export const configurationHref = (id: string) => `/backtests/configurations/${id}`

/** What a configuration's address that names none shows (the server's 404). */
export const CONFIGURATION_NOT_FOUND = {
  title: 'No such configuration',
  description: 'No run of the backtests is of this configuration: its runs may have been removed, or the address is mistyped.',
  suggestions: [
    { label: 'Backtests', href: '/backtests', description: 'Every configuration and its runs' },
    { label: 'New backtest', href: '/backtests/new', description: 'A strategy on the recorded turbos' },
  ],
}

/** Where a configuration's page is kept: under the runs' key, so that a run's end refreshes it too. */
export const configurationKey = (id: string) => ['backtests', 'configurations', id]

/** What a nightly run's old address says once the next replaced it: the configuration it was of (the server's 404). */
export function replacedOf(body: unknown): { configuration: string; name: string | null; to: string | null } | null {
  if (!body || typeof body !== 'object' || !('replaced' in body)) return null
  const replaced = (body as { replaced: unknown }).replaced
  if (!replaced || typeof replaced !== 'object' || typeof (replaced as { configuration?: unknown }).configuration !== 'string') return null
  const found = replaced as { configuration: string; name?: string | null; to?: string | null }
  return { configuration: found.configuration, name: found.name ?? null, to: found.to ?? null }
}

/** What the list of configurations is narrowed to: what narrows the runs, and whether it is made again each evening. */
export interface ConfigurationFilter extends RunFilter {
  nightly: 'on' | 'off' | null
}

export const CONFIGURATION_FILTER_NAMES = ['favourite', 'state', 'by', 'experiment', 'turbos', 'code', 'tag', 'verdict', 'nightly'] as const

export function configurationFilterOf(params: URLSearchParams): ConfigurationFilter {
  const nightly = params.get('nightly')
  return { ...runFilterOf(params), nightly: nightly === 'on' || nightly === 'off' ? nightly : null }
}

const STATE_GROUP: Record<BacktestState, NonNullable<RunFilter['state']>> = {
  queued: 'active',
  running: 'active',
  done: 'done',
  failed: 'failed',
  interrupted: 'failed',
  cancelled: 'stopped',
}

/**
 * The configurations narrowed by ``filter``: by any of their runs for the favourites, who made them, their
 * experiment and their tags; by their latest result for its verdict (``verdicts``) and its code; by their newest
 * run for its state.
 */
export function filterConfigurations(
  items: readonly BacktestConfiguration[],
  filter: ConfigurationFilter,
  verdicts: ReadonlyMap<string, CheckState> = new Map(),
): BacktestConfiguration[] {
  return items.filter((item) => {
    const newest = item.runs[0]
    return (
      (!filter.favourite || item.favourite) &&
      (!filter.state || (newest !== undefined && STATE_GROUP[newest.state] === filter.state)) &&
      (!filter.by || (filter.by === 'claude' ? item.made_by.includes('claude') : item.mine)) &&
      (!filter.experiment || item.experiments.includes(filter.experiment)) &&
      (!filter.turbos || (item.turbos ?? 'recorded') === filter.turbos) &&
      (!filter.code || (filter.code === 'older') === Boolean(item.latest?.stale)) &&
      (!filter.tag || item.tags.includes(filter.tag)) &&
      (!filter.verdict || verdicts.get(item.id) === filter.verdict) &&
      (!filter.nightly || (filter.nightly === 'on') === Boolean(item.nightly?.on))
    )
  })
}

/** Each configuration's verdict, that of its latest result with the runs that judge it: the list's (verdict.ts). */
export function configurationJudged(items: readonly BacktestConfiguration[]): Map<string, Judged> {
  const found = new Map<string, Judged>()
  for (const item of items) {
    if (!item.latest) continue
    const checks = listedChecks(item.latest, [item.latest, ...item.judging])
    if (checks.length > 0) found.set(item.id, { checks, verdict: verdictOf(checks) })
  }
  return found
}

/** The days from one day to another, both counted. */
export function daysBetween(from: string, to: string): number {
  const [first, last] = [from, to].map((day) => Date.UTC(Number(day.slice(0, 4)), Number(day.slice(5, 7)) - 1, Number(day.slice(8, 10))))
  return Math.round((last - first) / 86_400_000) + 1
}

/** A nightly run's days: how many, and the most it grows to before its first day moves on. */
export function windowSentence(from: string, to: string, most: number): string {
  const days = daysBetween(from, to)
  return `${formatDay(from)} to ${formatDay(to)}, ${days} day${days === 1 ? '' : 's'} (${most} at most, then its first day moves on)`
}

/** What its nightly re-run does, in a sentence; empty when it cannot have one. */
export function nightlySentence(nightly: BacktestNightly | null): string {
  if (!nightly) return ''
  if (!nightly.on) return 'Not made again by itself: “Bring it up to date” asks it once.'
  return `Made again after each trading day, from ${formatDay(nightly.from)}: ${nightly.most_days} days at most, then its first day moves on.`
}

const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']

function weekday(day: string): string {
  return WEEKDAYS[new Date(`${day}T12:00:00Z`).getUTCDay()]
}

/** When its next nightly run comes, and over which days; what it waits for. */
export function nextSentence(nightly: BacktestNightly | null): string {
  if (!nightly?.on) return ''
  const { next, last } = nightly
  const days = `${formatDay(next.from, false)} to ${formatDay(next.to, false)}`
  switch (next.waits) {
    case 'under_way':
      return `Its run to ${formatDay(last?.day ?? next.to, false)} is being made.`
    case 'market':
      return `Next: ${days}, once ${weekday(next.day)}’s trading hours are over.`
    case 'failed':
      return `The run to ${formatDay(last?.day ?? next.to, false)} did not finish: the next comes after the next trading day, or bring it up to date now.`
    case 'line':
      return `Next: ${days}, once the runs you asked for are done.`
    default:
      return `Next: ${days}, in a few minutes.`
  }
}

/** Its last nightly run: when it was asked, how long it took, how it ended. */
export function lastSentence(nightly: BacktestNightly | null): string {
  const last = nightly?.last
  if (!last) return ''
  const by = last.by_hand ? ', asked by hand' : ''
  if (!last.run) return `The run to ${formatDay(last.day, false)} was not asked${by}: ${last.error ?? 'it was refused'}.`
  if (last.state === 'done') return `Its run to ${formatDay(last.day, false)} took ${formatSpan(last.seconds ?? 0)}${by}.`
  if (last.state === 'failed' || last.state === 'interrupted') {
    return `Its run to ${formatDay(last.day, false)} did not finish${by}: ${firstLine(last.error) || 'no reason given'}.`
  }
  if (last.state === 'cancelled') return `Its run to ${formatDay(last.day, false)} was stopped${by}.`
  return ''
}

function firstLine(text: string | null | undefined): string {
  return (text ?? '').split('\n').map((line) => line.trim()).find(Boolean) ?? ''
}

export const KEPT_LABEL: Record<BacktestKeptReason, string> = {
  favourite: 'a favourite',
  reference: 'a reference',
  judged: 'stressed or checked',
  verdict: 'the AI agent’s verdict',
}

/** Why a nightly run stayed beside the newer one, said once; empty for one replaced as usual. */
export function keptSentence(run: Pick<BacktestConfigurationRun, 'nightly' | 'kept'>): string {
  if (!run.nightly || run.kept.length === 0) return ''
  return `kept: ${run.kept.map((reason) => KEPT_LABEL[reason]).join(', ')}`
}

export const DERIVED_LABEL: Record<NonNullable<BacktestConfigurationRun['derived']>, string> = {
  stress: 'Stress',
  noise: 'Fill noise',
  model_check: 'Model check',
}

function valueSaid(value: BacktestValue): string {
  if (typeof value === 'boolean') return value ? 'on' : 'off'
  return String(value)
}

/** What sets it apart from the configurations of its strategy: "capital 100". */
export function distinctSaid(distinct: BacktestConfiguration['distinct']): string {
  return distinct
    .slice(0, 3)
    .map(({ name, value }) => `${name.replace(/_/g, ' ')} ${valueSaid(value)}`)
    .join(' · ')
}

/** What its nightly runs said, oldest first: the totals of a sparkline. */
export function historyTotals(history: readonly BacktestNightlyLine[]): number[] {
  return [...history]
    .reverse()
    .map((line) => line.total_percent)
    .filter((total): total is number => total !== null && total !== undefined)
}

/** Its newest stress of an earlier run, when the latest has none: said beside its verdict, never counted. */
export function earlierStress(item: Pick<BacktestConfiguration, 'runs' | 'judging'>): BacktestConfigurationRun | null {
  if (item.judging.some((run) => run.stress_of)) return null
  return item.runs.find((run) => run.derived === 'stress' && run.state === 'done') ?? null
}

/** Whether "Bring it up to date" can ask: what stops it otherwise, said. */
export function bringUpToDate(item: Pick<BacktestConfiguration, 'nightly' | 'why_no_nightly' | 'latest'>, day: string): { can: boolean; why: string } {
  if (item.why_no_nightly) return { can: false, why: item.why_no_nightly }
  if (item.nightly?.next.waits === 'under_way') return { can: false, why: 'Its nightly run is being made' }
  if (item.nightly?.up_to_date) {
    return { can: false, why: `A run of it covers the days up to ${formatDay(day, false)} already: the next comes once a trading day is over` }
  }
  return { can: true, why: `Asks its run to ${formatDay(day, false)} now, in line with your runs` }
}
