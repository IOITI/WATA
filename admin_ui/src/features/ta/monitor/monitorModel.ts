// The Engine monitor as the page says it, in plain functions: its address, the tiles of the last
// minutes, the charts' lines and gaps, the bars of the delivery steps, the results beside the days on
// paper. The figures and the checks are the server's (src/admin/ta_monitor.py): this only reads them.

import { formatDateTime, formatNumber, formatSpan, formatWait } from 'ioiti-ui'

import type {
  BacktestPaper,
  TaMonitorNow,
  TaMonitorRecording,
  TaMonitorResult,
  TaMonitorRule,
  TaMonitorStep,
} from '@/lib/types'

export const MONITOR_PATH = '/ta/monitor'
export const DEFAULT_HOURS = 24
const RULE_ID = /^[0-9a-f]{12}$/
// The most bars of time a chart is given: the server's periods have 170 at most.
const MAX_BUCKETS = 400
// A limit is drawn on a chart once its figures come within this share of it: below, it would flatten them.
const LIMIT_IN_VIEW = 0.25

export interface MonitorAddress {
  hours: number
  rule: string | null
}

/** What the address asks for; the defaults for what it leaves out or says wrong. */
export function readAddress(params: URLSearchParams): MonitorAddress {
  const hours = Number(params.get('hours'))
  const rule = params.get('rule')
  return {
    hours: Number.isInteger(hours) && hours > 0 ? hours : DEFAULT_HOURS,
    rule: rule && RULE_ID.test(rule) ? rule : null,
  }
}

/** The search of a view of the monitor, without the defaults. */
export function addressParams({ hours, rule }: Partial<MonitorAddress>): Record<string, string> {
  const params: Record<string, string> = {}
  if (hours !== undefined && hours !== DEFAULT_HOURS) params.hours = String(hours)
  if (rule) params.rule = rule
  return params
}

/** The address of a view of the monitor: the menu's plain one for the defaults. */
export function monitorHref(address: Partial<MonitorAddress>): string {
  const search = new URLSearchParams(addressParams(address)).toString()
  return search ? `${MONITOR_PATH}?${search}` : MONITOR_PATH
}

/** A period as its tab says it, short enough for five tabs on a phone: 1 h, 24 h, 7 days. */
export function periodLabel(hours: number): string {
  if (hours <= 24) return `${hours} h`
  return `${formatNumber(hours / 24)} days`
}

/** A limit as the page names it, in whole seconds or minutes: 10 s, 30 s, 1 min 30 s. */
export function limitText(milliseconds: number): string {
  return milliseconds < 1000 ? formatWait(milliseconds) : formatSpan(milliseconds / 1000)
}

export type MonitorTone = 'neutral' | 'positive' | 'warning' | 'negative'

/** A figure against its limit: within the share the checks fail past, past it, past the limit itself. */
export function toneOf(value: number | null | undefined, limit: number | null | undefined, share: number): MonitorTone {
  if (value === null || value === undefined || !limit) return 'neutral'
  const ratio = value / limit
  return ratio >= 1 ? 'negative' : ratio > share ? 'warning' : 'positive'
}

function ratioOf(value: number | null | undefined, limit: number | null | undefined): number | null {
  return value === null || value === undefined || !limit ? null : value / limit
}

export interface MonitorTile {
  key: string
  label: string
  value: string
  hint: string
  /** Against its limit, 0 to 1 and beyond; none without a figure or a limit. */
  ratio: number | null
  tone: MonitorTone
  scale: [string, string] | null
}

const NONE = '—'

/** The figures of the last minutes, each against the limit it would cost a signal past. */
export function tilesOf(now: TaMonitorNow): MonitorTile[] {
  const { window, limits, last_tick: tick, slowest_run: slowest } = now
  const minutes = now.window_minutes
  const memoryUsed = tick?.memory_used_mb ?? null
  const memoryLimit = tick?.memory_limit_mb ?? window.memory_limit_mb
  const runners = [tick?.live_rss_mb, tick?.shadow_rss_mb].filter((value): value is number => typeof value === 'number')
  return [
    {
      key: 'delay',
      label: 'Live bar to its signals',
      value: window.live_delay_max_ms === null ? NONE : formatWait(window.live_delay_max_ms),
      hint:
        window.live_delay_max_ms === null
          ? `No live rule ran on a new bar in ${minutes} min`
          : `The slowest of ${minutes} min; a signal is refused past ${limitText(limits.max_signal_age_ms)}`,
      ratio: ratioOf(window.live_delay_max_ms, limits.max_signal_age_ms),
      tone: toneOf(window.live_delay_max_ms, limits.max_signal_age_ms, limits.share),
      scale: ['0', limitText(limits.max_signal_age_ms)],
    },
    {
      key: 'run',
      label: 'Slowest run',
      value: slowest ? formatWait(slowest.ms) : NONE,
      hint: slowest
        ? `${slowest.name ?? 'A rule no longer there'}, on the live runner; its limit ${limitText(slowest.limit_ms)}`
        : `Nothing ran on the live runner in ${minutes} min`,
      ratio: slowest ? slowest.ms / slowest.limit_ms : null,
      tone: slowest ? toneOf(slowest.ms, slowest.limit_ms, limits.share) : 'neutral',
      scale: slowest ? ['0', limitText(slowest.limit_ms)] : null,
    },
    {
      key: 'tick',
      label: 'Longest tick',
      value: window.steady_tick_max_ms === null ? NONE : formatWait(window.steady_tick_max_ms),
      hint: `One every ${limitText(limits.tick_ms)}; the daily restart, loads and checks aside`,
      ratio: ratioOf(window.steady_tick_max_ms, limits.tick_ms),
      tone: toneOf(window.steady_tick_max_ms, limits.tick_ms, limits.share),
      scale: ['0', limitText(limits.tick_ms)],
    },
    {
      key: 'feed',
      label: 'Feed behind',
      value: tick?.feed_lag_ms === null || tick?.feed_lag_ms === undefined ? NONE : formatWait(tick.feed_lag_ms),
      hint:
        window.feed_lag_max_ms === null
          ? `Nothing sent past ${limitText(limits.stale_feed_ms)}`
          : `${formatWait(window.feed_lag_max_ms)} at most in ${minutes} min; nothing is sent past ${limitText(limits.stale_feed_ms)}`,
      ratio: ratioOf(window.feed_lag_max_ms, limits.stale_feed_ms),
      tone: toneOf(window.feed_lag_max_ms, limits.stale_feed_ms, limits.share),
      scale: ['0', limitText(limits.stale_feed_ms)],
    },
    {
      key: 'memory',
      label: 'Memory',
      value: memoryUsed === null ? NONE : `${formatNumber(memoryUsed)} MB`,
      hint:
        memoryUsed === null || !memoryLimit
          ? 'Read on the server alone (Linux, cgroup v2)'
          : `Of the container's ${formatNumber(memoryLimit)} MB` +
            (runners.length ? `; the runners hold ${runners.map((value) => formatNumber(value)).join(' and ')} MB` : ''),
      ratio: ratioOf(memoryUsed, memoryLimit),
      tone: toneOf(memoryUsed, memoryLimit, limits.memory_share),
      scale: memoryLimit ? ['0', `${formatNumber(memoryLimit)} MB`] : null,
    },
  ]
}

const CHECK_ANCHORS: Record<string, string> = {
  sent: 'delivery',
  age: 'charts',
  runs: 'rules',
  ticks: 'charts',
  feed: 'charts',
  memory: 'charts',
}

/** Where a check leads, on the page. */
export function checkHref(check: { id: string }): string | null {
  const anchor = CHECK_ANCHORS[check.id]
  return anchor ? `#${anchor}` : null
}

/** How the engine keeps its measures, in a line; a warning when some could not be written. */
export function recordingLine(recording: TaMonitorRecording, timeZone: string): { text: string; warning: string | null } {
  const days = `${recording.days} day${recording.days === 1 ? '' : 's'}`
  const later =
    recording.configured_days !== recording.days
      ? ` (${recording.configured_days} day${recording.configured_days === 1 ? '' : 's'} from the engine's next start)`
      : ''
  const warning = recording.dropped
    ? `${formatNumber(recording.dropped)} tick${recording.dropped === 1 ? '' : 's'} of measures could not be written since the engine started` +
      (recording.error ? `: ${recording.error}` : '.')
    : null
  if (!recording.since) return { text: `Measures are kept ${days}${later}: none yet.`, warning }
  const written = recording.written_at ? `, last written ${formatDateTime(recording.written_at, timeZone)}` : ''
  return { text: `Kept ${days}${later}, since ${formatDateTime(recording.since, timeZone, false)}${written}.`, warning }
}

/**
 * Every bar of time of a period, those without figures as gaps: a pause of the engine shows as one, and
 * the bars of a count are evenly spread.
 */
export function filledBuckets<T extends { t: number }>(
  points: readonly T[],
  start: string,
  end: string,
  bucket: number,
  empty: (t: number) => T,
): T[] {
  const byTime = new Map(points.map((point) => [point.t, point]))
  const first = Math.floor(Date.parse(start) / 1000 / bucket) * bucket
  const last = Math.floor(Date.parse(end) / 1000 / bucket) * bucket
  if (!Number.isFinite(first) || !Number.isFinite(last) || bucket <= 0) return [...points]
  const filled: T[] = []
  for (let t = first; t <= last && filled.length < MAX_BUCKETS; t += bucket) filled.push(byTime.get(t) ?? empty(t))
  return filled
}

/** A bar of time's start on a chart's axis: its time within a day, its day and hour over longer. */
export function axisClock(hours: number, timeZone: string): (t: number) => string {
  const format = new Intl.DateTimeFormat(
    'en-GB',
    hours <= 24
      ? { timeZone, hour: '2-digit', minute: '2-digit', hour12: false }
      : { timeZone, weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false },
  )
  return (t) => format.format(new Date(t * 1000))
}

/** A bar of time as a tooltip names it: from its start to its end. */
export function bucketTitle(bucket: number, timeZone: string): (t: number) => string {
  const day = new Intl.DateTimeFormat('en-GB', { timeZone, weekday: 'short', day: '2-digit', month: 'short' })
  const time = new Intl.DateTimeFormat('en-GB', { timeZone, hour: '2-digit', minute: '2-digit', hour12: false })
  return (t) => `${day.format(new Date(t * 1000))}, ${time.format(new Date(t * 1000))} to ${time.format(new Date((t + bucket) * 1000))}`
}

// The steps of a period's axis, by its length in hours: every 10 min of an hour, every 3 h of a day, each midnight of a week.
const TICK_STEPS: [number, number][] = [
  [1, 600],
  [6, 3600],
  [24, 3 * 3600],
  [72, 12 * 3600],
  [168, 86_400],
  [336, 2 * 86_400],
  [720, 5 * 86_400],
]
// The lengths a bar of counts may take, each a multiple of the one before it it is made of.
const COUNT_BARS = [60, 120, 300, 600, 1800, 3600, 7200, 10_800, 21_600, 43_200, 86_400, 172_800]
// A day's 145 bars of ten minutes (the period starts at the bar before) fit in bars of half an hour.
const MAX_COUNT_BARS = 50

/** The length of a bar of time as a sentence says it: 2 min, 30 min, 1 h, 6 h, 1 day. */
export function barLength(seconds: number): string {
  if (seconds < 3600) return `${seconds / 60} min`
  if (seconds < 86_400) return `${seconds / 3600} h`
  return seconds === 86_400 ? '1 day' : `${seconds / 86_400} days`
}

/** How far ahead of UTC a time zone's clocks are at a moment, in seconds. */
export function zoneOffset(t: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(t * 1000))
  const part = (type: string) => Number(parts.find((entry) => entry.type === type)?.value)
  return Date.UTC(part('year'), part('month') - 1, part('day'), part('hour'), part('minute'), part('second')) / 1000 - t
}

/** The round moments of the trading time zone a period's axis is marked at (epoch seconds). */
export function axisTicks(start: string, end: string, hours: number, timeZone: string): number[] {
  const step = (TICK_STEPS.find(([upto]) => hours <= upto) ?? TICK_STEPS[TICK_STEPS.length - 1])[1]
  const first = Date.parse(start) / 1000
  const last = Date.parse(end) / 1000
  // Read at the period's start: past a change of the clocks, the ticks fall an hour off the round one.
  const offset = zoneOffset(first, timeZone)
  const ticks: number[] = []
  for (let t = Math.ceil((first + offset) / step) * step - offset; t <= last && ticks.length < 60; t += step) ticks.push(t)
  return ticks
}

/** Counts of a period in bars long enough to be seen, 50 at most, each starting at a round moment of the time zone. */
export function coarserCounts<K extends string>(
  points: readonly ({ t: number } & Record<K, number>)[],
  keys: readonly K[],
  bucket: number,
  timeZone: string,
): { bucket: number; points: ({ t: number } & Record<K, number>)[] } {
  if (points.length === 0) return { bucket, points: [] }
  const span = points[points.length - 1].t + bucket - points[0].t
  const size = COUNT_BARS.find((candidate) => candidate >= bucket && candidate % bucket === 0 && span / candidate <= MAX_COUNT_BARS) ?? bucket
  const offset = zoneOffset(points[0].t, timeZone)
  const merged = new Map<number, { t: number } & Record<K, number>>()
  for (const point of points) {
    const t = Math.floor((point.t + offset) / size) * size - offset
    const bar = merged.get(t) ?? ({ t, ...Object.fromEntries(keys.map((key) => [key, 0])) } as { t: number } & Record<K, number>)
    for (const key of keys) (bar as Record<K, number>)[key] += point[key] ?? 0
    merged.set(t, bar)
  }
  return { bucket: size, points: [...merged.values()] }
}

/** A limit worth drawing over figures: once they come near it. */
export function limitInView(values: readonly (number | null | undefined)[], limit: number): number | null {
  const highest = Math.max(0, ...values.filter((value): value is number => typeof value === 'number'))
  return highest >= limit * LIMIT_IN_VIEW ? limit : null
}

export interface MemoryLine {
  key: 'memory_used_mb' | 'live_rss_mb' | 'shadow_rss_mb' | 'engine_rss_mb'
  label: string
}

const MEMORY_LINES: MemoryLine[] = [
  { key: 'memory_used_mb', label: 'Container' },
  { key: 'live_rss_mb', label: 'Live runner' },
  { key: 'shadow_rss_mb', label: 'Shadow runner' },
  { key: 'engine_rss_mb', label: 'Engine' },
]

/** The memory lines that have figures: none read off Linux, no shadow runner without shadow rules. */
export function memoryLines(points: readonly Partial<Record<MemoryLine['key'], number | null>>[]): MemoryLine[] {
  return MEMORY_LINES.filter((line) => points.some((point) => typeof point[line.key] === 'number'))
}

/** Each step's median as a share of the longest step's, the totals apart: the bars beside the steps. */
export function stepShares(steps: readonly TaMonitorStep[]): Map<string, number> {
  const parts = steps.filter((step) => !step.total && step.p50 !== null)
  const longest = Math.max(0, ...parts.map((step) => step.p50 ?? 0))
  return new Map(parts.map((step) => [step.step, longest > 0 ? (step.p50 ?? 0) / longest : 0]))
}

/** A day of the trading time zone, as the paper days are named (YYYY-MM-DD). */
export function dayIn(value: string, timeZone: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value))
}

export interface ResultRow {
  rule_id: string
  name: string | null
  mode: TaMonitorRule['mode']
  made_by: string | null
  /** What its live signals traded. */
  live: TaMonitorResult | null
  /** Its finished days traded on paper in the period, newest first. */
  paper: { days: number; total: number; last: { day: string; percent: number; run_id: string } | null } | null
}

/** Each rule's results in the period: the positions of its live signals, its days on paper in shadow. */
export function resultRows(
  rules: readonly TaMonitorRule[],
  results: readonly TaMonitorResult[],
  paper: BacktestPaper | undefined,
  start: string,
  timeZone: string,
): ResultRow[] {
  const byRule = new Map(results.map((result) => [result.rule_id, result]))
  const from = dayIn(start, timeZone)
  const rows: ResultRow[] = []
  for (const rule of rules) {
    const days = (paper?.rules[rule.rule_id]?.days ?? []).filter((day) => day.day >= from && day.day_percent !== null)
    const live = byRule.get(rule.rule_id) ?? null
    if (!live && days.length === 0) continue
    const last = days[0]
    rows.push({
      rule_id: rule.rule_id,
      name: rule.name,
      mode: rule.mode,
      made_by: rule.made_by,
      live,
      paper: days.length
        ? {
            days: days.length,
            total: days.reduce((sum, day) => sum + (day.day_percent ?? 0), 0),
            last: last ? { day: last.day, percent: last.day_percent ?? 0, run_id: last.run_id } : null,
          }
        : null,
    })
  }
  return rows
}

/** A rule as the page names it: by its name, or as gone from the file. */
export function ruleName(rule: { name: string | null; rule_id: string }): string {
  return rule.name ?? `A rule no longer there (${rule.rule_id})`
}

/** How a rule's new bars were run in the period: continuing its run where it stopped, or each a whole run over all its bars. */
export function continuedLabel(rule: Pick<TaMonitorRule, 'runs' | 'continued'>): string | null {
  if (rule.runs === 0) return null
  if (rule.continued === 0) return 'each a whole run'
  return rule.continued === rule.runs ? 'all continued' : `${formatNumber(rule.continued)} continued`
}

/** Its continued runs checked against a whole run in the period: how many, the longest. Null for a rule never continued. */
export function checksLabel(rule: Pick<TaMonitorRule, 'continued' | 'checks' | 'check_ms_max'>): string | null {
  if (rule.checks === 0) return rule.continued === 0 ? null : 'Not checked against a whole run yet'
  const longest = rule.check_ms_max === null ? '' : `, ${formatWait(rule.check_ms_max)} at most`
  return `${formatNumber(rule.checks)} check${rule.checks === 1 ? '' : 's'} against a whole run${longest}`
}

/** That a whole run gave otherwise than a rule's continued run, and what it gave the last time; null when they always agreed. */
export function differedLabel(rule: Pick<TaMonitorRule, 'differed' | 'last_difference'>): { summary: string; detail: string } | null {
  if (rule.differed === 0) return null
  return {
    summary: `A whole run gave otherwise ${rule.differed === 1 ? 'once' : `${formatNumber(rule.differed)} times`}`,
    detail: `${rule.last_difference ? `The last time: ${rule.last_difference}. ` : ''}Each new bar runs it whole since.`,
  }
}
