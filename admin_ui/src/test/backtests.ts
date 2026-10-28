/**
 * Backtests as the admin's routes give them, for the tests of the backtest
 * pages: the shapes of a real run of the EMA cross over three days.
 */

import type {
  BacktestDay,
  BacktestField,
  BacktestOptions,
  BacktestRun,
  BacktestRunListed,
  BacktestSignal,
  BacktestSummary,
  BacktestTrade,
} from '@/lib/types'

export const RUN_ID = '20261019-203349-b48b51'

function field(name: string, group: string, kind: BacktestField['kind'], label: string, value: BacktestField['value'], extra: Partial<BacktestField> = {}): BacktestField {
  return { name, group, kind, label, least: null, most: null, choices: [], unit: '', source: `trade.${name}`, value, ...extra }
}

export const FIELDS: BacktestField[] = [
  field('max_trades_per_day', 'entries', 'integer', 'Entries per day at most (0: no limit)', 8, { least: 0, most: 1000 }),
  field('day_profit_target', 'day', 'number', 'Sell once the day would reach', 1.7, { least: 0.01, most: 10000, unit: '%' }),
  field('close_position_time', 'day', 'time', 'Everything is sold at', '21:55', { unit: 'local time' }),
  field('stoploss_percent', 'exits', 'number', 'Stop-loss', -15, { least: -100, most: -0.1, unit: '%' }),
  field('max_profit_percent', 'exits', 'number', 'Take-profit', 60, { least: 0.1, most: 10000, unit: '%' }),
  field('trailing_enabled', 'exits', 'boolean', 'Trailing stop', true),
  field('trailing_activation_percent', 'exits', 'number', 'Gain that arms the trailing stop', 5, { least: 0, most: 1000, unit: '%' }),
  field('trailing_drawdown_percent', 'exits', 'number', 'Fall from the best that sells', 8, { least: 0.1, most: 1000, unit: '%' }),
  field('capital', 'execution', 'number', 'Account at the start', 1000, { least: 1, most: 1e9, unit: '€', source: '' }),
  field('signal_delay_seconds', 'execution', 'number', 'From a signal to the trader', 3, { least: 0, most: 600, unit: 's', source: '' }),
  field('order_seconds', 'execution', 'number', 'From an order to its fill', 1, { least: 0, most: 600, unit: 's', source: '' }),
  field('slippage', 'execution', 'number', 'Worse price per turbo on each fill', 0, { least: 0, most: 1000, source: '' }),
  field('stop_fill', 'execution', 'choice', 'A stop crossed inside ten seconds sells at', 'worst', { choices: ['worst', 'level'], source: '' }),
]

export const OPTIONS: BacktestOptions = {
  fields: FIELDS,
  groups: ['entries', 'day', 'exits', 'turbo', 'sizing', 'execution', 'engine'],
  timezone: 'Europe/Paris',
  indices: ['us100'],
  capital: { value: 1000, balance: 21.82 },
  signal_delay: { pine: 3, journal: 0.5 },
  fixed: { time_periods: [], confidence_bands: [], default_confidence: 1, closed_dates: 22 },
  baseline: { repetitions: 100, most: 500, seed: 1 },
  sweep: { axes: 2, cells: 64, values: 12, input_sets: 12, held_out: 0.3, least: 0.1, most: 0.5 },
  limits: { period_days: 92, queued: 10, name_length: 80, tags: 8, tag_length: 24 },
}

export const SETTINGS: Record<string, unknown> = {
  timezone: 'Europe/Paris',
  max_trades_per_day: 8,
  day_profit_target: 1.7,
  close_position_time: '21:55',
  stoploss_percent: -15,
  max_profit_percent: 60,
  trailing_enabled: true,
  trailing_activation_percent: 5,
  trailing_drawdown_percent: 8,
  capital: 10250,
  stop_fill: 'worst',
}

export const SUMMARY: BacktestSummary = {
  made_with: {
    format: 1,
    version: '0.18.0',
    code: 'f8f819770facb384',
    engine: { runner: { pinets: '0.11.1', node: 'v24.21.0' }, milliseconds: 108, history_days: 1, restart_time: '21:15' },
  },
  days: { asked: 3, tested: 3, by_status: { tested: 3 }, with_an_entry: 3 },
  signals: {
    total: 116,
    by_fate: { refused: 110, executed: 3, ignored: 3 },
    by_source: { pine: 113, schedule: 3 },
    refusals: { day_profit: 59, market_hours: 51 },
    failures: {},
  },
  figures: {
    days: 3, days_with_an_entry: 3, trades: 3, sample: 'too_few', win_rate: 100, average_win: 1.71, average_loss: null,
    expectancy: 1.71, profit_factor: null, longest_losing_streak: 0, total_percent: 5.21, total_range: null, best_day: 1.72,
    worst_day: 1.7, median_day: 1.7, day_spread: 0.01, winning_days: 3, losing_days: 0, worst_drawdown: 0,
    winners_lowest: { median: -3.66, tenth: -5.79 }, losers_highest: null, exits: { 'Daily profit target': 3 },
    risk: {
      sharpe: null, sortino: null, calmar: null, yearly_percent: null, longest_under_water_days: 0, worst_5_percent_days: 1.7,
      skew: 0.71, kurtosis: 1.5, payoff: null, exit_efficiency: 61.2, trades_per_day: 1, hours_held_per_day: 0.42,
    },
  },
  money: {
    capital: 10250, final: 10782.93, profit_loss: 532.93, return_percent: 5.2, worst_drawdown: 0, worst_drawdown_percent: 0,
    average_stake: 10414.34, average_scale_percent: 100, commissions: 0, unfunded: 0,
  },
  curve: [
    { day: '2026-10-14', percent: 1.72, cumulative: 1.72, profit_loss: 175.86, equity: 10425.86 },
    { day: '2026-10-15', percent: 1.7, cumulative: 3.45, profit_loss: 177.12, equity: 10602.98 },
    { day: '2026-10-16', percent: 1.7, cumulative: 5.21, profit_loss: 179.95, equity: 10782.93 },
  ],
  against_random: { repetitions: 20, seed: 1, entries: 113, beaten: 85, median: 5.19, low: -26.15, high: 5.25, level: 90, worst: -26.21, best: 5.34 },
  stress: { monte_carlo: null, without_best_days: [{ removed: 1, total_percent: 3.45 }, { removed: 2, total_percent: 1.7 }] },
  warnings: [],
}

export function listedRun(changes: Partial<BacktestRunListed> = {}): BacktestRunListed {
  return {
    id: RUN_ID,
    kind: 'run',
    name: 'Smoke EMA cross',
    tags: [],
    from: '2026-10-14',
    to: '2026-10-16',
    indice: 'us100',
    source: { kind: 'pine', script: { id: '8c2df05570d5', version: 1, name: 'EMA cross' }, from_rule: null, signals: null },
    chart: { asset: { provider: 'capital', key: 'US100' }, side: 'bid', bars: { type: 'time', timeframe: '1' } },
    changes: {},
    status: { state: 'done', created_at: '2026-10-19T20:33:49Z', started_at: '2026-10-19T20:33:49Z', error: null, finished_at: '2026-10-19T20:33:54Z' },
    headline: {
      total_percent: 5.21, trades: 3, win_rate: 100, worst_drawdown: 0, sample: 'too_few', beaten: 85, days_tested: 3,
      profit_loss: 532.93, cells: null,
    },
    position: null,
    stale: false,
    ...changes,
  }
}

export function run(changes: Partial<BacktestRun> = {}): BacktestRun {
  const listed = listedRun(changes)
  return {
    ...listed,
    request: {
      kind: 'run',
      name: listed.name,
      tags: listed.tags,
      from: listed.from,
      to: listed.to,
      indice: 'us100',
      source: {
        kind: 'pine',
        rule: {
          id: 'replay00000a', name: 'EMA cross', asset: { provider: 'capital', key: 'US100' }, side: 'bid',
          bars: { type: 'time', timeframe: '1' }, script: { id: '8c2df05570d5', version: 1, sha256: 'a7c9aa5a' }, libraries: [],
          inputs: [{ id: 'in_0', var: 'fastLength', title: 'Fast length', value: 12 }],
          alerts: [{ title: 'Cross up', action: 'long' }, { title: 'Cross down', action: 'short' }],
          alert_calls: 'ignore', confidence_plot: null, indice: 'us100',
        },
        from_rule: null,
      },
      chart: listed.chart,
      changes: listed.changes,
      baseline: { repetitions: 20, seed: 1 },
      sweep: null,
      asked_at: '2026-10-19T20:33:49Z',
    },
    settings: { settings: SETTINGS, changed: {}, live_before: {} },
    summary: SUMMARY,
    progress: null,
    lines: [],
    ...changes,
  }
}

/** The shadow rule whose signals the paper runs below trade. */
export const PAPER_RULE = { id: 'a00000000001', name: 'Kernel VWAP' }
export const PAPER_REPORT_ID = '20261020-213500-aaaaaa'

/** A rule's paper report: one run of the signals it raised since it was made, as the TA engine journaled them. */
export function paperReport(changes: Partial<BacktestRun> = {}): BacktestRun {
  const found = run({
    id: PAPER_REPORT_ID,
    name: 'Paper: Kernel VWAP, since 2026-10-14',
    tags: ['paper'],
    source: { kind: 'ta_signals', script: null, from_rule: PAPER_RULE, signals: 12 },
    paper: { rule: PAPER_RULE.id, report: true },
    ...changes,
  })
  return {
    ...found,
    request: {
      ...found.request,
      name: found.name,
      tags: found.tags,
      source: { kind: 'ta_signals', signals: 12, rule: PAPER_RULE, versions: [3, 4] },
      baseline: { repetitions: 0, seed: 1 },
    },
    ...changes,
  }
}

export function trade(changes: Partial<BacktestTrade> = {}): BacktestTrade {
  return {
    id: 1,
    day: '2026-10-14',
    direction: 'short',
    signal_id: 12,
    confidence: null,
    scale_percent: 100,
    turbo: {
      instrument_id: 5, uic: 40000005, asset_type: 'WarrantOpenEndKnockOut', symbol: 'SG-NDX-S5',
      description: 'NASDAQ 100 Turbo Short 25900 SG', issuer: 'SG', barrier: 25900, direction: 'short', currency: null,
    },
    entry: {
      decided_at: '2026-10-14T06:51:03Z',
      selected_at: '2026-10-14T06:51:03Z',
      filled_at: '2026-10-14T06:51:04Z',
      ask: 10.48,
      bid: 10.43,
      selected_ask: 10.48,
      selected_bid: 10.43,
      price: 10.48,
      asset_price: 24855.2,
      candidates: [
        { instrument_id: 5, symbol: 'SG-NDX-S5', issuer: 'SG', barrier: 25900, bid: 10.43, ask: 10.48, in_range: true, chosen: true },
        { instrument_id: 6, symbol: 'BNP-NDX-S6', issuer: 'BNP', barrier: 26050, bid: 11.93, ask: 11.98, in_range: true, chosen: false },
        { instrument_id: 8, symbol: 'SG-NDX-S8', issuer: 'SG', barrier: 27500, bid: 26.43, ask: 26.48, in_range: false, chosen: false },
      ],
    },
    exit: {
      filled_at: '2026-10-14T07:07:10Z',
      bid: 10.66,
      reason: 'Performance (Daily profit target (1.7%) potentially met (1.72%))',
      group: 'Daily profit target',
      kind: 'day_target',
      signal_id: null,
      asset_price: 24832.5,
    },
    units: 977,
    cost: 10238.96,
    proceeds: 10414.82,
    commission: 0,
    profit_loss: 175.86,
    performance_percent: 1.72,
    max_performance_percent: 1.72,
    min_performance_percent: -2.77,
    holding_seconds: 966,
    day_percent_before: 0,
    cash_after: 10425.86,
    notes: [],
    ...changes,
  }
}

export function signal(changes: Partial<BacktestSignal> = {}): BacktestSignal {
  return {
    id: 12,
    day: '2026-10-14',
    source: 'pine',
    action: 'short',
    indice: 'us100',
    confidence: null,
    arrived_at: '2026-10-14T06:51:03Z',
    decided_at: '2026-10-14T06:51:03Z',
    fate: 'executed',
    rule: null,
    reason: null,
    trade_id: 1,
    closed_trade_ids: [],
    candidates: { looked_at: 3, quoted: 3, in_range: 2 },
    bar_time: '2026-10-14T06:50:00Z',
    bar_closed_at: '2026-10-14T06:51:00Z',
    alert: { type: 'alertcondition', title: 'Cross down', message: '{"action": "short"}' },
    note: null,
    ...changes,
  }
}

export function day(changes: Partial<BacktestDay> = {}): BacktestDay {
  return {
    day: '2026-10-14',
    status: 'tested',
    signals: { executed: 1, refused: 36, failed: 0, unpriced: 0, closed: 0, ignored: 1 },
    entries: 1,
    trades: 1,
    day_percent: 1.72,
    profit_loss: 175.86,
    equity: 10425.86,
    recorded: { long: 3, short: 3, minutes: 831, first: '2026-10-14T06:00:00Z', last: '2026-10-14T20:00:00Z' },
    strategy_bars: 1375,
    ...changes,
  }
}
