import { describe, expect, it } from 'vitest'

import type { Overview, Position } from '@/lib/types'

import { entryBlockers, openDirections, upcomingNews } from './entryState'

// 12 Oct 2026, 15:20 in Paris (UTC+2).
const NOW = Date.parse('2026-10-12T13:20:00Z')

function overview(changes: {
  day?: Partial<Overview['day']>
  limits?: Partial<Overview['limits']>
  cooldown?: Partial<Overview['cooldown']>
  paused?: string | null
  closedToday?: boolean
  open?: string[]
  news?: Overview['news']
  saxoLogin?: Overview['saxo_login']
} = {}): Overview {
  return {
    date: '2026-10-12',
    timezone: 'Europe/Paris',
    trading_mode: 'day_trading',
    day: {
      trade_count: 2,
      closed_count: 2,
      performance_percent: 0.4,
      profit_loss: 12,
      best_percent: 1,
      worst_percent: -0.5,
      ...changes.day,
    },
    limits: {
      profit_target_percent: 1.7,
      stop_entries_above_percent: 1.25,
      max_day_loss_percent: -25,
      close_position_time: '21:55',
      max_trades_per_day: 8,
      cooldown_after_loss_minutes: 10,
      trading_start_hour: 8,
      trading_end_hour: 22,
      risky_trading_start_hour: 21,
      risky_trading_start_minute: 54,
      ...changes.limits,
    },
    market_closed_today: changes.closedToday ?? false,
    cooldown: { last_loss_at: null, ends_at: null, active: false, ...changes.cooldown },
    entries_paused_since: changes.paused ?? null,
    news: changes.news ?? null,
    saxo_login: changes.saxoLogin ?? null,
    open_positions: (changes.open ?? []).map((action, index) => ({ action, position_id: String(index) }) as Position),
    recent_days: [],
  }
}

const codes = (state: Overview, now = NOW) => entryBlockers(state, now).map((blocker) => blocker.code)

describe('what holds a new entry back', () => {
  it('is nothing on an ordinary trading afternoon', () => {
    expect(entryBlockers(overview(), NOW)).toEqual([])
  })

  it('is the pause, said first', () => {
    const blockers = entryBlockers(overview({ paused: '2026-10-12T07:09:22Z', day: { trade_count: 8 } }), NOW)

    expect(blockers.map((blocker) => blocker.code)).toEqual(['paused', 'trade_limit'])
    expect(blockers[0].reason).toBe('Paused by the operator')
  })

  it('is Saxo refusing to renew the login, as the trader refuses then', () => {
    const blockers = entryBlockers(
      overview({ saxoLogin: { renewal_failing_since: '2026-10-12T13:05:00Z', error: 'Saxo answered 401', lapses_at: null } }),
      NOW,
    )

    expect(blockers).toEqual([
      { code: 'saxo_login', reason: "Saxo refuses to renew WATA's login since 15:05: no new position until it is renewed" },
    ])
    expect(codes(overview({ saxoLogin: { renewal_failing_since: null, error: null, lapses_at: null } }))).toEqual([])
  })

  it('is a closed date, whatever the hour', () => {
    expect(codes(overview({ closedToday: true }))).toEqual(['closed_date'])
  })

  it('is the hour, in the trading timezone and not the browser one', () => {
    // 07:59 and 22:00 in Paris.
    expect(codes(overview(), Date.parse('2026-10-12T05:59:00Z'))).toEqual(['hours'])
    expect(codes(overview(), Date.parse('2026-10-12T20:00:00Z'))).toEqual(['hours'])
    // 08:00 and 21:53 in Paris: inside.
    expect(codes(overview(), Date.parse('2026-10-12T06:00:00Z'))).toEqual([])
    expect(codes(overview(), Date.parse('2026-10-12T19:53:00Z'))).toEqual([])
    expect(entryBlockers(overview(), Date.parse('2026-10-12T05:59:00Z'))[0].reason).toBe(
      'Outside trading hours (8h to 22h)',
    )
  })

  it('is the last minutes of the day, as the trader counts them', () => {
    // 21:54 in Paris.
    expect(codes(overview(), Date.parse('2026-10-12T19:54:00Z'))).toEqual(['risky_hours'])
    // From the risky hour on, the minute decides: 21:30 passes. 20:58 is before that hour.
    expect(codes(overview(), Date.parse('2026-10-12T19:30:00Z'))).toEqual([])
    expect(codes(overview(), Date.parse('2026-10-12T18:58:00Z'))).toEqual([])
  })

  it('is the day target once it is reached, not before', () => {
    expect(codes(overview({ day: { performance_percent: 1.24 } }))).toEqual([])
    const blockers = entryBlockers(overview({ day: { performance_percent: 1.25 } }), NOW)
    expect(blockers.map((blocker) => blocker.code)).toEqual(['day_target'])
    expect(blockers[0].reason).toBe('Day performance reached +1.25%: no new entry today')
  })

  it('is the day stop once it is reached', () => {
    expect(codes(overview({ day: { performance_percent: -24.9 } }))).toEqual([])
    const blockers = entryBlockers(overview({ day: { performance_percent: -25 } }), NOW)
    expect(blockers.map((blocker) => blocker.code)).toEqual(['day_stop'])
    expect(blockers[0].reason).toBe('Day loss reached −25.00%: no new entry today')
  })

  it('is the number of trades, unless there is no limit', () => {
    expect(codes(overview({ day: { trade_count: 7 } }))).toEqual([])
    expect(codes(overview({ day: { trade_count: 8 } }))).toEqual(['trade_limit'])
    expect(codes(overview({ day: { trade_count: 50 }, limits: { max_trades_per_day: 0 } }))).toEqual([])
  })

  it('is the cooldown while it runs, with what is left of it', () => {
    const running = overview({ cooldown: { ends_at: '2026-10-12T13:26:00Z', active: true } })
    expect(entryBlockers(running, NOW)).toEqual([{ code: 'cooldown', reason: 'Cooldown after a loss: 6 min left' }])

    const over = overview({ cooldown: { ends_at: '2026-10-12T13:19:59Z' } })
    expect(codes(over)).toEqual([])
    const switchedOff = overview({
      cooldown: { ends_at: '2026-10-12T13:26:00Z' },
      limits: { cooldown_after_loss_minutes: 0 },
    })
    expect(codes(switchedOff)).toEqual([])
  })

  it('is the window around a US release while it runs, as the server reads it from the calendar', () => {
    // CPI at 08:30 in New York, 14:30 in Paris: from 14:25 to 14:45.
    const news = {
      window: { event: 'CPI', released_at: '2026-10-12T12:30:00Z', start: '2026-10-12T12:25:00Z', end: '2026-10-12T12:45:00Z' },
      known_until: '2026-12-23',
      readable: true,
    }
    const state = overview({ news })

    expect(entryBlockers(state, Date.parse('2026-10-12T12:25:00Z'))).toEqual([
      { code: 'macro_news', reason: 'CPI at 14:30: no entry until 14:45' },
    ])
    expect(codes(state, Date.parse('2026-10-12T12:24:59Z'))).toEqual([])
    expect(codes(state, Date.parse('2026-10-12T12:45:00Z'))).toEqual([])
    // Before it starts, the tile says what is coming.
    expect(upcomingNews(state, Date.parse('2026-10-12T09:00:00Z'))).toBe('No entry around CPI (14:30) from 14:25 to 14:45')
    expect(upcomingNews(state, Date.parse('2026-10-12T12:30:00Z'))).toBeNull()
    expect(upcomingNews(overview(), NOW)).toBeNull()
  })

  it('leaves out the rules that are not configured', () => {
    const bare = overview({
      day: { performance_percent: -80, trade_count: 99 },
      limits: {
        stop_entries_above_percent: null,
        max_day_loss_percent: null,
        max_trades_per_day: 0,
        trading_start_hour: null,
        trading_end_hour: null,
      },
    })
    expect(entryBlockers(bare, Date.parse('2026-10-12T01:00:00Z'))).toEqual([])
  })
})

describe('directions already open', () => {
  it('lists each once', () => {
    expect(openDirections(overview({ open: ['long', 'long', 'short'] }))).toEqual(['long', 'short'])
    expect(openDirections(overview())).toEqual([])
  })
})
