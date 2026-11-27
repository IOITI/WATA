// A Market calendar answer (GET /api/market-calendar) for the tests: yesterday's Thanksgiving, today's early close,
// a CPI window and Christmas, today being Friday 27 November 2026 in Paris.

import type { BlockedDay, MarketCalendar } from '@/lib/types'

export const THANKSGIVING: BlockedDay = {
  day: '2026-11-26',
  weekday: 4,
  effect: 'closed',
  blocks: [{ kind: 'us_closed', name: 'Thanksgiving Day' }],
}

export const DAY_AFTER: BlockedDay = {
  day: '2026-11-27',
  weekday: 5,
  effect: 'early_close',
  blocks: [{
    kind: 'us_early_close', name: 'The day after Thanksgiving', close_new_york: '13:00', closes_at: '2026-11-27T18:00:00+00:00',
    entries_end_at: '2026-11-27T17:30:00+00:00', sale_at: '2026-11-27T17:55:00+00:00',
  }],
}

export const CPI: BlockedDay = {
  day: '2026-12-10',
  weekday: 4,
  effect: 'window',
  blocks: [{
    kind: 'release', event: 'CPI', covers: '2026-11', time_new_york: '08:30', released_at: '2026-12-10T13:30:00+00:00',
    start: '2026-12-10T13:25:00+00:00', end: '2026-12-10T13:45:00+00:00',
  }],
}

export const CHRISTMAS_EVE: BlockedDay = {
  day: '2026-12-24',
  weekday: 4,
  effect: 'early_close',
  blocks: [{ kind: 'us_early_close', name: 'Christmas Eve', close_new_york: '13:00', closes_at: '2026-12-24T18:00:00+00:00' }],
}

export const NEW_YEAR: BlockedDay = {
  day: '2027-01-01',
  weekday: 5,
  effect: 'closed',
  blocks: [{ kind: 'us_closed', name: "New Year's Day" }, { kind: 'config_closed', written: '01/01/2027', also_us: true }],
}

export function marketCalendar(changes: Partial<MarketCalendar> = {}): MarketCalendar {
  return {
    today: '2026-11-27',
    timezone: 'Europe/Paris',
    market_timezone: 'Europe/Paris',
    us: { readable: true, until: '2027-12-31', upkeep: null },
    closed_dates: {
      count: 1,
      unreadable: [],
      link: { config: { group: 'hours', path: 'trade.rules[market_closed_dates].rule_config.market_closed_dates', block: 'closed' } },
    },
    news: {
      on: true, readable: true, events: ['FOMC', 'CPI', 'NFP', 'PCE'], known_from: '2024-01-31', known_until: '2027-12-03',
      upkeep: null, link: { config: { group: 'hours', path: 'trade.rules[macro_news].rule_config.minutes_before', block: 'news' } },
    },
    days: [THANKSGIVING, DAY_AFTER, CPI, CHRISTMAS_EVE, NEW_YEAR],
    ...changes,
  }
}
