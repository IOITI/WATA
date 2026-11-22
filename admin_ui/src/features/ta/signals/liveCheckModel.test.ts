import { describe, expect, it } from 'vitest'

import type { LiveCheck, LiveCheckCalibration, LiveCheckDayLine, LiveCheckSegment, LiveCheckTrade } from '@/lib/types'

import {
  agreementSaid,
  apartSaid,
  bridgeBars,
  calibratedRunSaid,
  calibratedSaid,
  calibrationRows,
  dayRow,
  fateSaid,
  liveCheckDayHref,
  liveTimesSaid,
  segmentSaid,
  shownTracks,
  slippageSaid,
  strategySaid,
  tradeSaid,
} from './liveCheckModel'

const FIGURES = { trades: 1, sum: 3.04, compounded: 3.04, entry_signals: 1, unkeyed: 0 }

function line(day: string, present: LiveCheckDayLine['present']): LiveCheckDayLine {
  return {
    day, present, agreement: null, runs: {}, bridge: { to_paper: null, to_live: null, calibrated: null },
    figures: Object.fromEntries(present.map((track) => [track, { ...FIGURES, sum: track === 'live' ? 1.39 : 3.04 }])),
    gap: present.includes('backtest') ? { against: present.includes('live') ? 'live' : 'paper', points: -1.65 } : null,
  }
}

const SEGMENT: LiveCheckSegment = {
  from: 'paper', to: 'live', start: { trades: 6, sum: 20.31 }, end: { trades: 5, sum: 11.2 },
  buckets: [
    { id: 'not_traded_live', label: 'Not traded live', points: 7.58, trades: 1 },
    { id: 'entry_price', label: 'Entry price', points: -4.1, trades: 5 },
    { id: 'exit_fill', label: 'Exit fill', points: -12.59, trades: 5 },
  ],
}

const NOW: LiveCheckCalibration = {
  values: { execution: 'measured', capital: 21.82 },
  template: { signal_delay_seconds: 3, execution: 'fixed', capital: 100 },
  measured: {
    signal_delay_seconds: { count: 4, median: 4.6, p90: 9 },
    entry_slippage_percent: { count: 4, median: 0.71, p90: 1.4 },
    exit_slippage_percent: { count: 4, median: 0.28, p90: 0.5 },
    trades: 4,
  },
  said: ['The signal’s delay keeps the backtest’s 3.0 s: 4 of 10 live entries measured'],
  template_run: '20261119-050828-c18c49',
  orders: { from: '2026-08-23', to: '2026-11-22', measures: { order_seconds: 120, slippage_buy: 61, slippage_sell: 58 } },
}

function trade(changes: Partial<LiveCheckTrade> = {}): LiveCheckTrade {
  return {
    key: '1|Enter Long', id: 1, direction: 'long', turbo: { uic: 555, description: 'Turbo' }, decided_at: null,
    filled_at: '2026-11-24T09:00:14Z', entry_price: 5, exit_at: '2026-11-24T09:30:00Z', exit_price: 5.15,
    exit_reason: 'Performance (Daily profit target)', exit_group: 'Daily profit target', result: 3, best: 3, worst: -1,
    units: 3, flags: [], ...changes,
  }
}

describe('the live check of a rule', () => {
  it('lays a day out by track, with the gap to the backtest', () => {
    const row = dayRow(line('2026-11-24', ['backtest', 'paper', 'live']))

    expect(row.live).toBe(true)
    expect(row.cells.live).toEqual({ trades: 1, sum: 1.39 })
    expect(row.cells.calibrated).toBeNull()
    expect(row.gap).toEqual({ against: 'live', points: -1.65 })
    expect(shownTracks([line('2026-11-24', ['backtest', 'live']), line('2026-11-21', ['paper'])])).toEqual(['backtest', 'paper', 'live'])
    expect(liveCheckDayHref('f13986be5e9b', '2026-11-24')).toBe('/ta/signals/f13986be5e9b/live/2026-11-24')
  })

  it('draws the bridge as bars beside the longest and says where most of it went', () => {
    const bars = bridgeBars(SEGMENT)

    expect(bars.map((bar) => [bar.id, Number(bar.share.toFixed(2))])).toEqual([
      ['not_traded_live', 0.6], ['entry_price', 0.33], ['exit_fill', 1],
    ])
    expect(segmentSaid(SEGMENT)).toBe(
      'Paper +20.31 pt in 6 trades, live +11.20 pt in 5 trades (−9.11 pt): most of it exit fill, −12.59 pt over 5 trades.',
    )
    expect(segmentSaid({ ...SEGMENT, buckets: [] })).toContain('nothing moved')
    expect(segmentSaid({ ...SEGMENT, days: 3 })).toMatch(/^Over the 3 days both have: paper \+20.31 pt in 6 trades/)
  })

  it('says how far the backtest raised the engine’s signals', () => {
    expect(agreementSaid({ same_bar: 37, other_bar: 1, not_replayed: 0, not_raised: 2, raised: 38, same_bar_percent: 97.4 })).toBe(
      'The backtest raised 37 of the engine’s 38 entry signals at the same bar (97.4 %), 1 at another bar, and 2 the engine never raised.',
    )
    expect(agreementSaid({ same_bar: 0, other_bar: 0, not_replayed: 0, not_raised: 0 })).toBe('No entry signal on either side.')
    expect(agreementSaid(null)).toBeNull()
  })

  it('says whether the linked configuration plays the rule’s strategy', () => {
    expect(strategySaid({ same: true, checks: [{ id: 'inputs', state: 'note', detail: 'Inputs set on the rule only: in_23 20' }] }))
      .toEqual({ tone: 'success', text: 'The rule’s strategy: Inputs set on the rule only: in_23 20.' })
    expect(strategySaid({ same: false, checks: [{ id: 'chart', state: 'fail', detail: 'Another asset, side or bars' }] }).tone).toBe('danger')
    expect(strategySaid(null).tone).toBe('neutral')
  })

  it('says how much of the gap the calibrated run closes', () => {
    const calibrated = { days: 3, closed_percent: 62.5, backtest: { trades: 4, sum: 12 }, calibrated: { trades: 4, sum: 7 },
      live: { trades: 3, sum: 4 } }

    expect(calibratedSaid(calibrated)).toBe(
      'Over 3 live days: backtest +12.00 pt, calibrated +7.00 pt, live +4.00 pt. The calibrated run closes 63 % of the gap between the backtest and live.',
    )
    expect(calibratedSaid({ ...calibrated, closed_percent: null })).toContain('No gap')
    expect(calibratedSaid(null)).toBeNull()
  })

  it('lists what the calibrated run changes of its backtest, and what it keeps', () => {
    const rows = calibrationRows(NOW)

    expect(rows.map((row) => [row.name, row.backtest, row.applied])).toEqual([
      ['signal_delay_seconds', '3.0 s', false], ['execution', 'Fixed', true], ['capital', '100.00 €', true],
    ])
    expect(rows[0].measured).toBe('4.6 s (median of 4, 90 % under 9.0 s)')
    expect(rows[1].measured).toBe('58 orders measured at least of each kind')
    expect(rows[2].measured).toBe('21.82 €')
    expect(slippageSaid(NOW)).toBe('Entries paid +0.71 pt at the median of 4; exits gave +0.28 pt at the median of 4 (points of the result).')
  })

  it('says where the calibrated run stands', () => {
    const base = { link: null, calibrated: null } as unknown as LiveCheck
    expect(calibratedRunSaid(base)).toContain('No backtest is linked')
    const linked = { ...base, link: { configuration: '2bb6f8514b16', set_at: '', found: true, nightly: { on: true, from: null } } }
    expect(calibratedRunSaid(linked)).toContain('asked each evening')
    expect(calibratedRunSaid({ ...linked, calibrated: { run: null, template: 'x', values: {}, asked_at: '', error: 'Nothing measured live differs from the backtest yet', settled: true, state: null } }))
      .toBe('Nothing measured live differs from the backtest yet')
    expect(calibratedRunSaid({ ...linked, calibrated: { run: 'r', template: 'x', values: {}, asked_at: '', error: null, settled: false, state: 'running' } }))
      .toBe('Being made.')
  })

  it('says what became of a signal on each track, and a trade in short', () => {
    expect(fateSaid({ fate: 'not_sent', reason: 'Not alone on us100' })).toBe('Not sent: Not alone on us100')
    expect(fateSaid({ fate: 'executed' })).toBe('Traded')
    expect(fateSaid(null)).toBe('—')
    expect(liveTimesSaid({ fate: 'executed', engine: { outcome: 'sent', raised_seconds: 1, sent_seconds: 1.4 },
      trader: { outcome: 'executed', reason: null, decided_seconds: 9.8 } })).toBe('raised 1.0 s, sent 1.4 s, decided 9.8 s after the bar’s close')
    expect(tradeSaid(trade())).toBe('5 → 5.15 → +3.00 %')
    expect(tradeSaid(null)).toBe('—')
    expect(apartSaid(trade({ filled_at: '2026-11-24T09:00:16Z', entry_price: 5.05, exit_group: 'Stop-loss' }), trade()))
      .toBe('bought 2.0 s after paper, at +1.00 % of its price, exit: Stop-loss')
    expect(apartSaid(trade(), null)).toBeNull()
  })
})

describe('a day of the live check drawn', () => {
  it('marks each track’s purchase and sale on the bar it fell in', async () => {
    const { liveCheckLayer } = await import('./liveCheckModel')
    const start = Date.parse('2026-11-24T09:00:00Z')
    // Three bars of ten seconds, then one to 09:40: [open time, o, h, l, c, v, close time].
    const bars = [
      [start, 1, 1, 1, 1, 0, start + 10_000],
      [start + 10_000, 1, 1, 1, 1, 0, start + 20_000],
      [start + 20_000, 1, 1, 1, 1, 0, start + 40 * 60_000],
    ] as unknown as Parameters<ReturnType<typeof liveCheckLayer>>[0]
    const colors = { entry: 'blue', profit: 'green', loss: 'red', neutral: 'grey', warning: 'orange', warmUp: 'w' }
    const row = { key: 'k', at: null, action: 'long', title: 'Enter Long', near: null, same_turbo: true,
      paper: trade(), live: trade({ filled_at: '2026-11-24T09:00:16Z', result: -2 }) }

    const layer = liveCheckLayer([row], ['paper', 'live'])(bars, colors, start)

    expect(layer.labels.map((label) => [label.id, label.text, label.color])).toEqual([
      ['paper-in-k', 'P', 'blue'], ['live-in-k', 'L', 'blue'], ['paper-out-k', 'P', 'green'], ['live-out-k', 'L', 'red'],
    ])
    expect(liveCheckLayer([row], ['paper'])(bars, colors, start).labels.map((label) => label.id)).toEqual(['paper-in-k', 'paper-out-k'])
  })
})
