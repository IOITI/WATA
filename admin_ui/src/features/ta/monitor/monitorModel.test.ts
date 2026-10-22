import { describe, expect, it } from 'vitest'

import type { BacktestPaper, TaMonitorStep } from '@/lib/types'
import { monitorRule, NOW } from '@/test/monitor'

import {
  addressParams,
  axisClock,
  axisTicks,
  barLength,
  bucketTitle,
  checkHref,
  coarserCounts,
  dayIn,
  filledBuckets,
  limitInView,
  limitText,
  memoryLines,
  monitorHref,
  periodLabel,
  readAddress,
  recordingLine,
  resultRows,
  ruleName,
  stepShares,
  tilesOf,
  toneOf,
  zoneOffset,
} from './monitorModel'

describe('the address of the Engine monitor', () => {
  it('reads a period and a rule, and takes the defaults for what it leaves out or says wrong', () => {
    expect(readAddress(new URLSearchParams('hours=6&rule=0123456789ab'))).toEqual({ hours: 6, rule: '0123456789ab' })
    expect(readAddress(new URLSearchParams('hours=-3&rule=../x'))).toEqual({ hours: 24, rule: null })
    expect(readAddress(new URLSearchParams(''))).toEqual({ hours: 24, rule: null })
  })

  it('leaves the defaults out of the links it makes, so that the menu’s is the plain one', () => {
    expect(monitorHref({})).toBe('/ta/monitor')
    expect(monitorHref({ hours: 24, rule: null })).toBe('/ta/monitor')
    expect(monitorHref({ hours: 6, rule: '0123456789ab' })).toBe('/ta/monitor?hours=6&rule=0123456789ab')
    expect(addressParams({ rule: '0123456789ab' })).toEqual({ rule: '0123456789ab' })
  })

  it('names its periods, and its limits in whole seconds', () => {
    expect([1, 6, 24, 72, 168, 240, 720].map(periodLabel)).toEqual(['1 h', '6 h', '24 h', '3 days', '7 days', '10 days', '30 days'])
    expect([500, 10_000, 30_000, 60_000, 90_000].map(limitText)).toEqual(['500 ms', '10 s', '30 s', '1 min', '1 min 30 s'])
  })
})

describe('a figure against its limit', () => {
  it('is within the share a check fails past, past it, or past the limit itself', () => {
    expect([toneOf(10, 100, 0.5), toneOf(50, 100, 0.5), toneOf(51, 100, 0.5), toneOf(100, 100, 0.5)]).toEqual([
      'positive', 'positive', 'warning', 'negative',
    ])
    expect([toneOf(null, 100, 0.5), toneOf(10, null, 0.5)]).toEqual(['neutral', 'neutral'])
  })
})

describe('the tiles of the last minutes', () => {
  it('say each figure beside the limit a signal is lost past', () => {
    const tiles = tilesOf(NOW)

    expect(tiles.map((tile) => [tile.key, tile.value, tile.tone])).toEqual([
      ['delay', '1.6 s', 'positive'],
      ['run', '312 ms', 'positive'],
      ['tick', '820 ms', 'positive'],
      ['feed', '12.5 s', 'positive'],
      ['memory', '522 MB', 'positive'],
    ])
    expect(tiles[0].hint).toBe('The slowest of 15 min; a signal is refused past 1 min')
    expect(tiles[1].hint).toBe('EMA cross, on the live runner; its limit 30 s')
    expect(tiles[3].hint).toBe('13.4 s at most in 15 min; nothing is sent past 1 min 30 s')
    expect(tiles[4].hint).toBe("Of the container's 1,024 MB; the runners hold 214 and 150 MB")
    expect(tiles[4].scale).toEqual(['0', '1,024 MB'])
  })

  it('say what cannot be known off the server, or without a live rule', () => {
    const tiles = tilesOf({
      ...NOW,
      last_tick: { ...NOW.last_tick!, memory_used_mb: null, memory_limit_mb: null, live_rss_mb: null, shadow_rss_mb: null },
      window: { ...NOW.window, live_delay_max_ms: null, memory_limit_mb: null },
      slowest_run: null,
    })

    expect(tiles.map((tile) => [tile.key, tile.value, tile.ratio])).toEqual([
      ['delay', '—', null],
      ['run', '—', null],
      ['tick', '820 ms', 0.082],
      ['feed', '12.5 s', 13_400 / 90_000],
      ['memory', '—', null],
    ])
    expect(tiles[4].hint).toBe('Read on the server alone (Linux, cgroup v2)')
  })

  it('lead from each check to the part of the page that says more', () => {
    expect(['sent', 'age', 'runs', 'ticks', 'feed', 'memory', 'other'].map((id) => checkHref({ id }))).toEqual([
      '#delivery', '#charts', '#rules', '#charts', '#charts', '#charts', null,
    ])
  })
})

describe('how the engine keeps its measures', () => {
  it('says since when, its last write, and the days kept from its next start when they changed', () => {
    expect(recordingLine(NOW.recording, 'Europe/Paris')).toEqual({
      text: 'Kept 7 days, since 15 Oct, 10:00, last written 22 Oct, 11:59:57.',
      warning: null,
    })
    expect(recordingLine({ ...NOW.recording, configured_days: 14, since: null, written_at: null }, 'Europe/Paris').text).toBe(
      "Measures are kept 7 days (14 days from the engine's next start): none yet.",
    )
  })

  it('warns of the measures that could not be written', () => {
    expect(recordingLine({ ...NOW.recording, dropped: 3, error: 'ConnectionError: refused' }, 'Europe/Paris').warning).toBe(
      '3 ticks of measures could not be written since the engine started: ConnectionError: refused',
    )
  })
})

describe('the charts', () => {
  it('give every bar of time of the period, those without figures as gaps', () => {
    const points = [{ t: 600, value: 1 }, { t: 1800, value: 3 }]
    const filled = filledBuckets(points, new Date(0).toISOString(), new Date(2400_000).toISOString(), 600, (t) => ({ t, value: null as number | null }))

    expect(filled).toEqual([{ t: 0, value: null }, { t: 600, value: 1 }, { t: 1200, value: null }, { t: 1800, value: 3 }, { t: 2400, value: null }])
  })

  it('draw a limit once the figures come near it, never one that would flatten them', () => {
    expect(limitInView([1600, 2100, null], 60_000)).toBeNull()
    expect(limitInView([1600, 16_000], 60_000)).toBe(60_000)
  })

  it('draw the memory that was read: none off Linux, no shadow runner without shadow rules', () => {
    expect(memoryLines([{ memory_used_mb: 500, live_rss_mb: 200, shadow_rss_mb: null, engine_rss_mb: 90 }]).map((line) => line.label)).toEqual([
      'Container', 'Live runner', 'Engine',
    ])
    expect(memoryLines([{ memory_used_mb: null }])).toEqual([])
  })

  it('mark the time axis at round moments of the trading time zone', () => {
    const at = (iso: string) => Date.parse(iso) / 1000

    expect(zoneOffset(at('2026-10-22T12:00:00Z'), 'Europe/Paris')).toBe(7200)
    expect(zoneOffset(at('2026-11-02T12:00:00Z'), 'Europe/Paris')).toBe(3600)
    // A day: every three hours of Paris, from the first after the period's start.
    expect(axisTicks('2026-10-21T10:00:00Z', '2026-10-22T10:00:00Z', 24, 'Europe/Paris').slice(0, 3)).toEqual([
      at('2026-10-21T10:00:00Z'), at('2026-10-21T13:00:00Z'), at('2026-10-21T16:00:00Z'),
    ])
    // A week: each midnight of Paris, 22:00 UTC in its summer time.
    expect(axisTicks('2026-10-15T10:00:00Z', '2026-10-22T10:00:00Z', 168, 'Europe/Paris')).toEqual([
      at('2026-10-15T22:00:00Z'), at('2026-10-16T22:00:00Z'), at('2026-10-17T22:00:00Z'), at('2026-10-18T22:00:00Z'),
      at('2026-10-19T22:00:00Z'), at('2026-10-20T22:00:00Z'), at('2026-10-21T22:00:00Z'),
    ])
  })

  it('count in bars long enough to be seen, 50 at most, from round moments', () => {
    const start = Date.parse('2026-10-21T10:00:00Z') / 1000
    // A day and the bar before it, as a period of 24 hours gives them.
    const points = Array.from({ length: 145 }, (_, index) => ({ t: start + index * 600, sent: index === 4 ? 1 : 0, shadow: 1 }))

    const counts = coarserCounts(points, ['sent', 'shadow'] as const, 600, 'Europe/Paris')

    // A day in bars of ten minutes becomes 49 of half an hour, each the sum of its three.
    expect([counts.bucket, counts.points.length]).toEqual([1800, 49])
    expect([60, 600, 1800, 3600, 21_600, 86_400, 172_800].map(barLength)).toEqual(['1 min', '10 min', '30 min', '1 h', '6 h', '1 day', '2 days'])
    expect(counts.points[0]).toEqual({ t: start, sent: 0, shadow: 3 })
    expect(counts.points[1]).toEqual({ t: start + 1800, sent: 1, shadow: 3 })
    expect(coarserCounts([], ['sent'] as const, 600, 'Europe/Paris')).toEqual({ bucket: 600, points: [] })
  })

  it('say a bar of time in the trading time zone', () => {
    const t = Date.parse('2026-10-22T12:00:00Z') / 1000

    expect(axisClock(24, 'Europe/Paris')(t)).toBe('14:00')
    expect(axisClock(168, 'Europe/Paris')(t)).toBe('Thu 14:00')
    expect(bucketTitle(600, 'Europe/Paris')(t)).toBe('Thu 22 Oct, 14:00 to 14:10')
    expect(dayIn('2026-10-21T22:30:00Z', 'Europe/Paris')).toBe('2026-10-22')
  })
})

describe('the delivery and the results', () => {
  it('draw each step beside the longest, the totals apart', () => {
    const step = (key: string, p50: number | null, total = false): TaMonitorStep => ({
      step: key, label: key, total, signals: 3, p50, p95: p50, max: p50,
    })

    const shares = stepShares([step('close_to_tick', 1500), step('run', 375), step('order_to_saxo', null), step('close_to_sent', 2000, true)])

    expect(Object.fromEntries(shares)).toEqual({ close_to_tick: 1, run: 0.25 })
  })

  it('set the positions of a live rule beside the days on paper of a shadow one, from the period’s first day', () => {
    const paper: BacktestPaper = {
      rules: {
        bbbbbbbbbbbb: {
          days: [
            { day: '2026-10-21', run_id: '20261022-010000-aaaaaa', state: 'done', day_percent: 0.4, trades: 2, outside: false },
            { day: '2026-10-20', run_id: '20261021-010000-bbbbbb', state: 'done', day_percent: -0.1, trades: 1, outside: false },
            { day: '2026-10-14', run_id: '20261015-010000-cccccc', state: 'done', day_percent: 3, trades: 4, outside: false },
          ],
          reference: null,
        },
      },
    }
    const rules = [monitorRule(), monitorRule({ rule_id: 'bbbbbbbbbbbb', name: 'Claude · cross', mode: 'shadow', made_by: 'claude' }), monitorRule({ rule_id: 'cccccccccccc' })]
    const results = [{ rule_id: 'aaaaaaaaaaaa', positions: 3, open: 1, won: 1, lost: 1, profit_loss: 12.46, average_percent: 0.812 }]

    const rows = resultRows(rules, results, paper, '2026-10-19T22:00:00Z', 'Europe/Paris')

    expect(rows.map((row) => [row.rule_id, row.live?.positions ?? null, row.paper])).toEqual([
      ['aaaaaaaaaaaa', 3, null],
      ['bbbbbbbbbbbb', null, { days: 2, total: 0.30000000000000004, last: { day: '2026-10-21', percent: 0.4, run_id: '20261022-010000-aaaaaa' } }],
    ])
    expect(ruleName({ name: null, rule_id: 'cccccccccccc' })).toBe('A rule no longer there (cccccccccccc)')
  })
})
