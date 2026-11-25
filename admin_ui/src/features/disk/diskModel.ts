// What the Disk page says of the server's disk (Operations › Disk): its level, each kind of data, the 14 days of
// hourly samples and the projection under other retentions. The figures are the server's (src/admin/disk.py,
// src/disk/); this module only words them and shapes them for the charts.

import { formatBytes, formatDay, formatNumber } from 'ioiti-ui'
import type { HealthStatus, LinePoint, LineReference } from 'ioiti-ui'

import type {
  DiskCleanupId,
  DiskCleanupOffer,
  DiskCleanupRecord,
  DiskJudged,
  DiskKeptRelease,
  DiskKeptWhy,
  DiskKind,
  DiskLevel,
  DiskPoint,
  DiskProjection,
  DiskRetentionKey,
  DiskRetentions,
} from '@/lib/types'

const GB = 1_000_000_000

/** The level as the health says it: critical is down, a timer that stopped a warning. */
export const LEVEL_STATUS: Record<DiskLevel, HealthStatus> = {
  ok: 'ok',
  warning: 'warning',
  stale: 'warning',
  critical: 'down',
  none: 'unknown',
}

export const LEVEL_LABEL: Record<DiskLevel, string> = {
  ok: 'Fine',
  warning: 'Low',
  stale: 'Not sampled',
  critical: 'Critical',
  none: 'No sample yet',
}

export interface RetentionField {
  key: DiskRetentionKey
  label: string
  unit: 'months' | 'days' | 'MB'
  /** What 0 means, where it is allowed. */
  zero?: string
  /** Where it is set: its page, and the words of the link. */
  href: string
  where: string
}

/** The retentions the projection may change, in the order the page lists them. */
export const RETENTION_FIELDS: RetentionField[] = [
  { key: 'saxo_retention_months', label: "Saxo's prices", unit: 'months', zero: 'every month', href: '/data/providers/saxo', where: "The price recorder's settings" },
  { key: 'capital_retention_months', label: "Capital.com's prices", unit: 'months', zero: 'every month', href: '/data/providers/capital', where: "The Capital.com feed's settings" },
  { key: 'capital_day_files_days', label: "Capital.com's day files", unit: 'days', zero: 'every day', href: '/data/providers/capital', where: "The Capital.com feed's settings" },
  { key: 'dukascopy_retain_days', label: "Dukascopy's history", unit: 'days', zero: 'every day', href: '/data/providers/dukascopy', where: "Dukascopy's settings" },
  { key: 'signal_cache_mb', label: 'Pine answers kept', unit: 'MB', href: '/config', where: 'Configuration › Backtests' },
  { key: 'research_keep_days', label: 'Research export', unit: 'days', href: '/backtests', where: 'The Backtests page' },
  { key: 'ta_monitoring_days', label: 'Engine monitoring', unit: 'days', href: '/config', where: 'Configuration › Technical Analysis' },
]

export const FIELD_OF = Object.fromEntries(RETENTION_FIELDS.map((field) => [field.key, field])) as Record<DiskRetentionKey, RetentionField>

/** A retention in words: "3 months", "Every day". */
export function retentionText(key: DiskRetentionKey, value: number | null | undefined): string {
  const field = FIELD_OF[key]
  if (value === null || value === undefined) return '-'
  if (value === 0 && field.zero) return field.zero.charAt(0).toUpperCase() + field.zero.slice(1)
  if (field.unit === 'MB') return formatBytes(value * 1024 * 1024)
  return `${value} ${value === 1 ? field.unit.replace(/s$/, '') : field.unit}`
}

/** What a retention asked cannot be; null when it can be. */
export function retentionError(value: number | null, bounds: [number, number] | undefined): string | null {
  if (value === null || !Number.isInteger(value)) return 'A whole number'
  if (bounds && (value < bounds[0] || value > bounds[1])) return `Between ${bounds[0]} and ${bounds[1]}`
  return null
}

/** The retentions of the form that differ from those set now: what the projection is asked with. */
export function changedRetentions(draft: Partial<DiskRetentions>, current: DiskRetentions): Partial<DiskRetentions> {
  const changed: Partial<DiskRetentions> = {}
  for (const field of RETENTION_FIELDS) {
    const value = draft[field.key]
    if (value !== undefined && value !== null && value !== current[field.key]) changed[field.key] = value
  }
  return changed
}

/** A pace: "+35.0 MB a day"; what is not known yet said so. */
export function paceText(bytes: number | null | undefined, per = 'a day'): string {
  if (bytes === null || bytes === undefined) return 'Not known yet'
  if (Math.abs(bytes) < 1000) return 'Steady'
  return `${bytes > 0 ? '+' : '−'}${formatBytes(Math.abs(bytes))} ${per}`
}

/** A change over the week, signed; "-" without a sample then. */
export function changeText(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return '-'
  if (bytes === 0) return '0'
  return `${bytes > 0 ? '+' : '−'}${formatBytes(Math.abs(bytes))}`
}

/** Where a kind's pace was read from, in words, for its hint. */
export function paceSource(kind: Pick<DiskKind, 'pace_from'>, samplesOver: number | null): string {
  switch (kind.pace_from) {
    case 'samples':
      return samplesOver ? `Over the ${samplesOver < 2 ? 'last day' : `last ${Math.round(samplesOver)} days`} of samples` : 'Over the samples'
    case 'partitions':
      return 'From its newest monthly partition, until three days of samples'
    case 'days held':
      return 'From its files over the days they cover, until three days of samples'
    default:
      return 'Three days of samples are needed'
  }
}

/** Days until a level, in words: "In 412 days (3 Jan 2028)". */
export function daysText(days: number | null | undefined, today: string | undefined, lookDays = 1825): string {
  if (days === null || days === undefined) return `Not within ${Math.round(lookDays / 365)} years`
  if (days <= 0) return 'Already'
  if (!today) return `In ${days} days`
  const date = new Date(`${today}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return `In ${days} day${days === 1 ? '' : 's'} (${formatDay(date.toISOString().slice(0, 10))})`
}

export interface FamilyRows {
  family: string
  bytes: number
  kinds: DiskKind[]
}

/** The kinds under their families, families in the server's order, each kind's size first. */
export function familyRows(kinds: DiskKind[], families: { name: string; bytes: number }[]): FamilyRows[] {
  return families
    .map((family) => ({
      family: family.name,
      bytes: family.bytes,
      kinds: kinds.filter((kind) => kind.family === family.name).sort((a, b) => (b.bytes ?? -1) - (a.bytes ?? -1)),
    }))
    .filter((row) => row.kinds.some((kind) => kind.bytes !== null))
}

/** The levels as the charts draw them, in GB of free space. */
export function levelReferences(total: number, thresholds: { warning_free_percent: number; critical_free_percent: number }): LineReference[] {
  return [
    { value: (total * thresholds.warning_free_percent) / 100 / GB, label: `Low (${thresholds.warning_free_percent} %)`, tone: 'warning' },
    { value: (total * thresholds.critical_free_percent) / 100 / GB, label: `Critical (${thresholds.critical_free_percent} %)`, tone: 'danger' },
  ]
}

/** The free space of each sample, in GB. */
export function freePoints(series: DiskPoint[]): LinePoint[] {
  return series.map((point) => ({ t: point.at, free: point.available === null ? null : point.available / GB }))
}

/** The four groups the daily bars stack: the design compares four colours, never more. */
export const STACKS = [
  { key: 'data', label: 'Prices and database', families: ['Prices', 'Database'] },
  { key: 'work', label: 'Backtests, backups, admin', families: ['Backtests and research', 'Backups', 'Admin'] },
  { key: 'docker', label: 'Releases and Docker', families: ['Releases and Docker'] },
  { key: 'rest', label: 'Logs, system, other', families: ['Logs', 'Other WATA files', 'System'] },
] as const

/** The last sample of each day, its families in the four stacks, in GB. */
export function dailyStacks(series: DiskPoint[]): ({ label: string } & Record<string, number | string>)[] {
  const byDay = new Map<string, DiskPoint>()
  for (const point of series) byDay.set(point.at.slice(0, 10), point)
  return [...byDay.entries()].map(([day, point]) => {
    const datum: { label: string } & Record<string, number | string> = { label: day }
    for (const stack of STACKS) {
      datum[stack.key] = stack.families.reduce((sum, family) => sum + (point.families[family] ?? 0), 0) / GB
    }
    return datum
  })
}

/** The free space projected day by day: as set now, and with the retentions asked when there are some. */
export function projectionPoints(asSet: DiskProjection, asked: DiskProjection | null): LinePoint[] {
  const askedByDay = new Map((asked?.series ?? []).map((point) => [point.days, point.available]))
  return asSet.series.map((point) => ({
    t: point.date,
    asSet: point.available / GB,
    ...(asked ? { asked: (askedByDay.get(point.days) ?? null) === null ? null : (askedByDay.get(point.days) as number) / GB } : {}),
  }))
}

export interface ProjectedRow {
  id: string
  now: number | null
  asSet: number
  asked: number | null
  steady: [number, number] | null
}

/** Each kind at a horizon, as set now and asked; those that move first, then the largest. */
export function projectedRows(asSet: DiskProjection, asked: DiskProjection | null, days: number): ProjectedRow[] {
  const at = asSet.horizons.find((horizon) => horizon.days === days)
  const atAsked = asked?.horizons.find((horizon) => horizon.days === days)
  if (!at) return []
  return asSet.kinds
    .map((kind) => ({
      id: kind.id,
      now: kind.now,
      asSet: at.kinds[kind.id] ?? 0,
      asked: atAsked ? (atAsked.kinds[kind.id] ?? 0) : null,
      steady: (asked?.kinds.find((other) => other.id === kind.id) ?? kind).steady,
    }))
    .filter((row) => (row.now ?? 0) > 0 || row.asSet > 0)
    .sort((a, b) => Math.abs(b.asSet - (b.now ?? 0)) - Math.abs(a.asSet - (a.now ?? 0)) || b.asSet - a.asSet)
}

/** The level's sentence for the top of the page. */
export function levelSentence(judged: DiskJudged): string {
  return judged.reason
}

/** How many days of samples there are, from the first to the newest. */
export function samplesOver(first: string | undefined, last: string | undefined): number | null {
  if (!first || !last) return null
  return (Date.parse(last) - Date.parse(first)) / 86_400_000
}

// ── the cleanups the page may ask (src/admin/disk_cleanups.py) ──

export const CLEANUP_TITLE: Record<DiskCleanupId, string> = {
  server: 'Older releases and Docker’s leftovers',
  signal_cache: 'Pine answers kept',
  former: 'The former backtester’s runs',
}

export const CLEANUP_BUTTON: Record<DiskCleanupId, string> = {
  server: 'Clean up',
  signal_cache: 'Empty it',
  former: 'Remove them',
}

/** What a cleanup removes, and what it would give back, in words. */
export function cleanupSentence(offer: DiskCleanupOffer): string {
  switch (offer.id) {
    case 'server': {
      const releases = offer.releases ?? []
      const parts = [
        releases.length
          ? `release folder${releases.length === 1 ? '' : 's'} ${releases.map((one) => one.version).join(', ')} and their images`
          : 'no release folder (the newest three and those in use stay)',
        `Docker’s build cache no image holds, unused for a day${offer.build_cache ? ` (up to ${formatBytes(offer.build_cache)} of it may go)` : ''}`,
        'the images no tag names any more',
      ]
      return `watadeploy removes on the server ${parts.join(', ')}. Nothing a container runs or is pinned to; never a volume.`
    }
    case 'signal_cache':
      return `${formatNumber(offer.files ?? 0)} answers of the Pine runner, kept so that a run asked again on the same days costs nothing; such a run computes them anew.${offer.limit ? ` Pruned beyond ${formatBytes(offer.limit)} anyway.` : ''}`
    case 'former':
      return `${formatNumber(offer.runs ?? 0)} runs and the measures of the turbos the former backtester left. Today's backtests never read them.`
  }
}

// Why a release stays, the strongest first: what runs, what the infrastructure was made from, the newest, a rollback.
const KEPT_ORDER: DiskKeptWhy[] = ['pinned', 'created', 'newest', 'rollback']

function andJoined(items: string[]): string {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** The releases watadeploy keeps, grouped by why, in a sentence; null when it does not say. */
export function keptSentence(offer: DiskCleanupOffer): string | null {
  if (!offer.kept?.length) return null
  const groups = new Map<DiskKeptWhy, DiskKeptRelease[]>()
  for (const release of offer.kept) {
    const why = KEPT_ORDER.find((one) => release.why.includes(one))
    if (why) groups.set(why, [...(groups.get(why) ?? []), release])
  }
  const parts = KEPT_ORDER.filter((why) => groups.has(why)).map((why) => {
    const releases = groups.get(why) ?? []
    const versions = releases.map((one) => one.version).join(', ')
    const one = releases.length === 1
    switch (why) {
      case 'pinned':
        return `${versions} (in use)`
      case 'created': {
        const made = [...new Set(releases.flatMap((release) => release.containers))]
        return `${versions} (${one ? 'holds' : 'hold'} the compose files ${made.length ? andJoined(made) : 'containers'} ${made.length === 1 ? 'was' : 'were'} made from)`
      }
      case 'newest':
        return `${versions} (among the newest three)`
      case 'rollback':
        return `${versions} (a rollback may go back to ${one ? 'it' : 'them'})`
    }
  })
  return parts.length ? `Kept: ${parts.join('; ')}.` : null
}

/** What it would give back: "About 929 MB", "Up to 929 MB". */
export function cleanupGain(offer: DiskCleanupOffer): string {
  if (offer.bytes === null || offer.bytes === undefined) return 'Not known'
  return `${offer.up_to ? 'Up to' : 'About'} ${formatBytes(offer.bytes)}`
}

/** What watadeploy's last cleanup did, in a sentence. */
export function lastCleanupSentence(record: DiskCleanupRecord, formatMoment: (iso: string) => string): string {
  const when = record.finished_at ? formatMoment(record.finished_at) : 'at an unknown time'
  if (record.error) return `The last cleanup, ${when}, was not made: ${record.error}.`
  const removed = record.removed.length ? `releases ${record.removed.join(', ')} removed` : 'no release removed'
  const failed = record.errors?.length ? ` Not done: ${record.errors.join('; ')}.` : ''
  // Docker says what a prune gave back, or nothing (until 2026-11-19 watadeploy could not read buildx's words): an
  // amount not said is never shown as nothing given back.
  const images = record.images_bytes ?? null
  const cache = record.build_cache_bytes ?? null
  const given =
    images !== null && cache !== null
      ? `${formatBytes(images + cache)} given back by Docker`
      : images !== null
        ? `${formatBytes(images)} given back by the images no tag names; what the build cache gave back was not said`
        : cache !== null
          ? `${formatBytes(cache)} given back by the build cache; what the images gave back was not said`
          : 'what Docker gave back was not said'
  return `Last cleanup ${when}, by ${record.by}: ${removed}, ${given}.${failed}`
}
