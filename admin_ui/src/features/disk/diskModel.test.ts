import { describe, expect, it } from 'vitest'

import type { DiskKeptRelease, DiskKind, DiskPoint, DiskProjection, DiskRetentions } from '@/lib/types'

import {
  changedRetentions,
  changeText,
  cleanupGain,
  cleanupSentence,
  keptSentence,
  lastCleanupSentence,
  dailyStacks,
  daysText,
  familyRows,
  freePoints,
  levelReferences,
  paceText,
  projectedRows,
  projectionPoints,
  retentionError,
  retentionText,
} from './diskModel'

const GB = 1_000_000_000
const MB = 1_000_000

const CURRENT: DiskRetentions = {
  saxo_retention_months: 0, capital_retention_months: 0, capital_day_files_days: 0, dukascopy_retain_days: 0,
  signal_cache_mb: 1024, research_keep_days: 30, ta_monitoring_days: 7,
}

const kind = (id: string, family: string, bytes: number | null): DiskKind => ({
  id, label: id, family, model: 'trend', knob: null, link: null, note: '', bytes, share: null, per_day: null,
  pace_from: null, week: null, week_since: null, retention: null,
})

const projection = (available: number[], kinds: Record<string, number>[]): DiskProjection => ({
  at: '2026-11-17T07:07:00Z', today: '2026-11-17', total: 96 * GB, available: available[0], retentions: CURRENT,
  current: CURRENT, asked: {}, thresholds: { warning_free_percent: 15, critical_free_percent: 5 },
  kinds: [
    { id: 'saxo_bars', now: 749 * MB, per_day: 35 * MB, pace_from: 'samples', retention: 0, steady: null },
    { id: 'backtest_runs', now: 39 * MB, per_day: null, pace_from: null, retention: null, steady: null },
    { id: 'price_export', now: 0, per_day: null, pace_from: null, retention: null, steady: null },
  ],
  horizons: kinds.map((byKind, index) => ({ days: [30, 365][index], date: '', available: available[index + 1], free_percent: null, kinds: byKind })),
  series: available.map((value, days) => ({ days, date: `2026-11-${17 + days}`, available: value, free_percent: 0, families: {} })),
  days_to_warning: null, days_to_critical: null, look_days: 1825,
})

describe('the retentions', () => {
  it('are said in their units, 0 as everything where it means it', () => {
    expect(retentionText('saxo_retention_months', 0)).toBe('Every month')
    expect(retentionText('saxo_retention_months', 1)).toBe('1 month')
    expect(retentionText('dukascopy_retain_days', 365)).toBe('365 days')
    expect(retentionText('signal_cache_mb', 1024)).toBe('1.1 GB')
    expect(retentionText('ta_monitoring_days', null)).toBe('-')
  })

  it('are checked against their bounds, and only those changed are asked', () => {
    expect(retentionError(3, [0, 120])).toBeNull()
    expect(retentionError(121, [0, 120])).toBe('Between 0 and 120')
    expect(retentionError(1.5, [0, 120])).toBe('A whole number')
    expect(retentionError(null, [0, 120])).toBe('A whole number')
    expect(changedRetentions({ ...CURRENT, saxo_retention_months: 3, signal_cache_mb: 1024 }, CURRENT)).toEqual({ saxo_retention_months: 3 })
  })
})

describe('the figures', () => {
  it('say a pace and a change signed, and what is not known', () => {
    expect(paceText(35 * MB)).toBe('+35.0 MB a day')
    expect(paceText(-2 * GB)).toBe('−2.0 GB a day')
    expect(paceText(12)).toBe('Steady')
    expect(paceText(null)).toBe('Not known yet')
    expect(changeText(245 * MB)).toBe('+245 MB')
    expect(changeText(null)).toBe('-')
  })

  it('say the days to a level with its date', () => {
    expect(daysText(45, '2026-11-17')).toBe('In 45 days (01 Jan 2027)')
    expect(daysText(0, '2026-11-17')).toBe('Already')
    expect(daysText(null, '2026-11-17')).toBe('Not within 5 years')
  })

  it('put each kind under its family, the largest first, and leave out families with nothing measured', () => {
    const rows = familyRows(
      [kind('dukascopy', 'Prices', 424 * MB), kind('saxo_bars', 'Prices', 749 * MB), kind('docker_images', 'Releases and Docker', null)],
      [{ name: 'Prices', bytes: 1173 * MB }, { name: 'Releases and Docker', bytes: 0 }],
    )
    expect(rows.map((row) => [row.family, row.kinds.map((one) => one.id)])).toEqual([['Prices', ['saxo_bars', 'dukascopy']]])
  })
})

describe('the charts', () => {
  const series: DiskPoint[] = [
    { at: '2026-11-16T06:07:00Z', available: 80 * GB, total: 96 * GB, families: { Prices: GB, Database: GB, Logs: 2 * GB } },
    { at: '2026-11-16T23:07:00Z', available: 79.5 * GB, total: 96 * GB, families: { Prices: 1.5 * GB, Database: GB, Logs: 2 * GB } },
    { at: '2026-11-17T07:07:00Z', available: 79 * GB, total: 96 * GB, families: { Prices: 2 * GB, 'Releases and Docker': 8 * GB } },
  ]

  it('draw the free space in GB with the two levels', () => {
    expect(freePoints(series).map((point) => point.free)).toEqual([80, 79.5, 79])
    expect(levelReferences(96 * GB, { warning_free_percent: 15, critical_free_percent: 5 })).toEqual([
      { value: 14.4, label: 'Low (15 %)', tone: 'warning' },
      { value: 4.8, label: 'Critical (5 %)', tone: 'danger' },
    ])
  })

  it('stack each day’s last sample in four groups', () => {
    expect(dailyStacks(series)).toEqual([
      { label: '2026-11-16', data: 2.5, work: 0, docker: 0, rest: 2 },
      { label: '2026-11-17', data: 2, work: 0, docker: 8, rest: 0 },
    ])
  })

  it('set the projection asked beside the one as set now, kind by kind', () => {
    const asSet = projection([79 * GB, 78 * GB, 66 * GB], [{ saxo_bars: 1.8 * GB, backtest_runs: 39 * MB }, { saxo_bars: 13.5 * GB, backtest_runs: 39 * MB }])
    const asked = projection([79 * GB, 78.5 * GB, 77 * GB], [{ saxo_bars: 1.2 * GB, backtest_runs: 39 * MB }, { saxo_bars: 3.2 * GB, backtest_runs: 39 * MB }])
    expect(projectionPoints(asSet, asked)[1]).toEqual({ t: '2026-11-18', asSet: 78, asked: 78.5 })
    expect(projectionPoints(asSet, null)[2]).toEqual({ t: '2026-11-19', asSet: 66 })
    const rows = projectedRows(asSet, asked, 365)
    expect(rows.map((row) => row.id)).toEqual(['saxo_bars', 'backtest_runs'])
    expect(rows[0]).toMatchObject({ now: 749 * MB, asSet: 13.5 * GB, asked: 3.2 * GB })
  })
})

describe('the cleanups', () => {
  const server = {
    id: 'server' as const, bytes: 929 * MB, up_to: true, available: true, why_not: null, build_cache: 900 * MB,
    releases: [] as { version: string; bytes: number | null }[],
  }

  it('say what goes and what it would give back, Docker’s as a most', () => {
    expect(cleanupGain(server)).toBe('Up to 929 MB')
    expect(cleanupGain({ ...server, id: 'signal_cache', up_to: false, bytes: 1_100 * MB })).toBe('About 1.1 GB')
    expect(cleanupGain({ ...server, bytes: null })).toBe('Not known')
    expect(cleanupSentence(server)).toMatch(/^watadeploy removes on the server no release folder \(the newest three and those in use stay\), Docker’s build cache no image holds, unused for a day \(up to 900 MB of it may go\)/)
  })

  it('say which releases stay and why, grouped by the strongest reason', () => {
    // The server's status on 2026-11-19, as watadeploy now says it.
    const kept = [
      { version: '0.92.1', why: ['newest', 'pinned', 'created'], containers: ['admin1', 'tools'] },
      { version: '0.92.0', why: ['newest', 'pinned', 'created', 'rollback'], containers: ['trader1'] },
      { version: '0.91.0', why: ['newest'], containers: [] },
      { version: '0.90.0', why: ['rollback'], containers: ['admin1'] },
      { version: '0.85.0', why: ['pinned', 'created'], containers: ['service_control1'] },
      { version: '0.61.1', why: ['pinned', 'created'], containers: ['pg_backup1'] },
      { version: '0.59.1', why: ['created'], containers: ['rabbitmq', 'postgres', 'traefik'] },
    ] as DiskKeptRelease[]
    expect(keptSentence({ ...server, kept })).toBe(
      'Kept: 0.92.1, 0.92.0, 0.85.0, 0.61.1 (in use); 0.59.1 (holds the compose files rabbitmq, postgres and traefik were made from); ' +
        '0.91.0 (among the newest three); 0.90.0 (a rollback may go back to it).',
    )
    expect(keptSentence({ ...server, kept: [kept[3], { ...kept[3], version: '0.88.0' }] })).toBe(
      'Kept: 0.90.0, 0.88.0 (a rollback may go back to them).',
    )
    // A watadeploy that does not say it: nothing is made up.
    expect(keptSentence(server)).toBeNull()
    expect(keptSentence({ ...server, kept: null })).toBeNull()
  })

  it('say the last one, or why it was not made', () => {
    const at = (iso: string) => iso.slice(0, 10)
    expect(lastCleanupSentence({ id: 'c', by: 'admin (the Disk page)', removed: [], images_bytes: 5_000, build_cache_bytes: 900 * MB,
      finished_at: '2026-11-17T07:00:00Z', errors: ['docker image prune failed: x'] }, at))
      .toBe('Last cleanup 2026-11-17, by admin (the Disk page): no release removed, 900 MB given back by Docker. Not done: docker image prune failed: x.')
    expect(lastCleanupSentence({ id: 'c', by: 'x', removed: [], images_bytes: null, build_cache_bytes: null,
      finished_at: '2026-11-17T07:00:00Z', error: 'Another apply is under way: one at a time' }, at))
      .toBe('The last cleanup, 2026-11-17, was not made: Another apply is under way: one at a time.')
  })

  it('never say nothing was given back when Docker did not say', () => {
    const at = (iso: string) => iso.slice(0, 10)
    // The cleanup of 2026-11-19: buildx's words were not read, and the page said "0 B given back by Docker".
    const record = { id: 'c', by: 'admin (the Disk page)', removed: ['0.82.0'], images_bytes: 0, build_cache_bytes: null,
      finished_at: '2026-11-19T05:06:40Z' }
    expect(lastCleanupSentence(record, at)).toBe('Last cleanup 2026-11-19, by admin (the Disk page): releases 0.82.0 removed, ' +
      '0 B given back by the images no tag names; what the build cache gave back was not said.')
    expect(lastCleanupSentence({ ...record, images_bytes: null, build_cache_bytes: 1_527 * MB }, at)).toBe('Last cleanup ' +
      '2026-11-19, by admin (the Disk page): releases 0.82.0 removed, 1.5 GB given back by the build cache; what the images gave back was not said.')
    expect(lastCleanupSentence({ ...record, images_bytes: null }, at)).toBe('Last cleanup 2026-11-19, by admin (the Disk page): ' +
      'releases 0.82.0 removed, what Docker gave back was not said.')
  })
})
