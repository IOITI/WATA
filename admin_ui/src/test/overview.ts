import type { DailyPerformance, Overview, Position } from '@/lib/types'

/** An open position as the overview gives it, its live value just read. */
export function openPosition(id: string, name: string, changes: Partial<Position> = {}): Position {
  return {
    position_id: id,
    action: 'long',
    instrument_name: name,
    instrument_currency: 'EUR',
    position_amount: 260,
    position_open_price: 8.42,
    position_max_performance_percent: 1.8,
    position_status: 'Open',
    execution_time_open: '2026-10-23T12:30:00Z',
    live_bid: 8.5,
    live_performance_percent: 0.95,
    live_observed_at: new Date().toISOString(),
    ...changes,
  } as Position
}

export function closedDay(date: string, profit: number): DailyPerformance {
  return {
    day_date: date,
    trade_count: 2,
    win_count: profit > 0 ? 2 : 0,
    loss_count: profit > 0 ? 0 : 2,
    profit_loss: profit,
    cumulative_profit_loss: profit,
    performance_percent: profit / 10,
  }
}

/** The dashboard's overview of a quiet day, with no limit reached and no position open unless given. */
export function overview(changes: Partial<Overview> = {}): Overview {
  return {
    date: '2026-10-23',
    timezone: 'Europe/Paris',
    trading_mode: 'day_trading',
    day: { trade_count: 1, closed_count: 1, performance_percent: 0.5, profit_loss: 4.2, best_percent: 0.5, worst_percent: 0.5 },
    limits: {
      profit_target_percent: 2,
      stop_entries_above_percent: null,
      max_day_loss_percent: -3,
      close_position_time: '21:55',
      max_trades_per_day: 5,
      cooldown_after_loss_minutes: 0,
      trading_start_hour: null,
      trading_end_hour: null,
      risky_trading_start_hour: null,
      risky_trading_start_minute: null,
    },
    market_closed_today: false,
    cooldown: { last_loss_at: null, ends_at: null, active: false },
    entries_paused_since: null,
    news: null,
    open_positions: [],
    recent_days: [closedDay('2026-10-21', 12.4), closedDay('2026-10-22', -5)],
    ...changes,
  }
}
