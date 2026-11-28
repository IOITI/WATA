// A Market calendar answer (GET /api/market-calendar) for the tests: yesterday's Thanksgiving, today's early close,
// a CPI window, a PPI the rule does not name and Christmas, today being Friday 27 November 2026 in Paris.

import type { BlockedDay, MarketCalendar, ReleaseBlock } from '@/lib/types'

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
    start: '2026-12-10T13:25:00+00:00', end: '2026-12-10T13:45:00+00:00', applies: true,
  }],
}

/** A release the rule does not name: listed, blocking nothing. */
export const PPI: BlockedDay = {
  day: '2026-12-15',
  weekday: 2,
  effect: 'none',
  blocks: [{
    kind: 'release', event: 'PPI', covers: '2026-11', time_new_york: '08:30', released_at: '2026-12-15T13:30:00+00:00',
    start: '2026-12-15T13:25:00+00:00', end: '2026-12-15T13:45:00+00:00', applies: false, not_applied: 'not_named',
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
    us: { readable: true, until: '2028-12-31', upkeep: null },
    closed_dates: {
      count: 1,
      unreadable: [],
      link: { config: { group: 'hours', path: 'trade.rules[market_closed_dates].rule_config.market_closed_dates', block: 'closed' } },
    },
    news: {
      on: true, readable: true, rule_error: null, events: ['FOMC', 'CPI', 'NFP', 'PCE'],
      all_events: ['FOMC', 'CPI', 'NFP', 'PCE', 'PPI', 'RETAIL', 'GDP', 'JOLTS'], default_events: ['FOMC', 'CPI', 'NFP', 'PCE'],
      known_from: '2024-01-31', known_until: '2027-12-03', upkeep: null,
      link: { config: { group: 'hours', path: 'trade.rules[macro_news].rule_config.minutes_before', block: 'news' } },
    },
    days: [THANKSGIVING, DAY_AFTER, CPI, PPI, CHRISTMAS_EVE, NEW_YEAR],
    ...changes,
  }
}

/** The same calendar as the server has it today: no macro_news rule, every release listed, none applied. */
export function ruleOff(): MarketCalendar {
  const calendar = marketCalendar()
  const unapplied = (block: ReleaseBlock): ReleaseBlock => ({ ...block, applies: false, not_applied: 'rule_off' })
  return {
    ...calendar,
    news: { ...calendar.news, on: false, events: [], upkeep: null },
    days: calendar.days.map((day) =>
      day.blocks.some((block) => block.kind === 'release')
        ? { ...day, effect: 'none', blocks: day.blocks.map((block) => (block.kind === 'release' ? unapplied(block) : block)) }
        : day,
    ),
  }
}
