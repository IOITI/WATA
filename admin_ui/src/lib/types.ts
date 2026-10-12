// Shapes of the admin API (src/admin/routers/*.py).

export interface Session {
  authenticated: boolean
  version: string
  timezone: string
  actions_enabled: boolean
  config_edit_enabled: boolean
  totp_enabled: boolean
  restart_required: boolean
}

export interface Position {
  action: string | null
  position_id: string
  position_amount: number | null
  position_open_price: number | null
  position_close_price: number | null
  position_close_reason: string | null
  position_profit_loss: number | null
  position_total_open_price: number | null
  position_total_close_price: number | null
  position_total_performance_percent: number | null
  position_max_performance_percent: number | null
  position_status: string | null
  position_kind: string | null
  execution_time_open: string | null
  execution_time_close: string | null
  order_id: string | null
  related_order_id: string[] | null
  instrument_name: string | null
  instrument_symbol: string | null
  instrument_uic: number | null
  instrument_currency: string | null
  /** Open positions only: what the position monitor last saw. Absent until it has published a value. */
  live_bid?: number | null
  live_performance_percent?: number | null
  live_observed_at?: string | null
}

export interface Order {
  action: string | null
  buy_sell: string | null
  order_id: string
  order_amount: number | null
  order_type: string | null
  order_kind: string | null
  order_time: string | null
  related_order_id: string[] | null
  position_id: string | null
  instrument_name: string | null
  instrument_symbol: string | null
  instrument_uic: number | null
  instrument_price: number | null
  instrument_currency: string | null
  order_cost: number | null
}

export interface Page<T> {
  items: T[]
  total: number
}

export interface PositionDetail {
  position: Position
  orders: Order[]
}

export interface DailyPerformance {
  day_date: string
  trade_count: number
  win_count: number
  loss_count: number
  profit_loss: number
  cumulative_profit_loss: number
  performance_percent: number
}

export interface DayExtreme {
  day_date: string
  profit_loss: number
}

export interface PerformanceSummary {
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  profit_loss: number
  trading_days: number
  winning_days: number
  average_trade_percent: number | null
  best_day: DayExtreme | null
  worst_day: DayExtreme | null
}

export interface ActionPerformance {
  action: string
  trade_count: number
  win_count: number
  loss_count: number
  profit_loss: number
  average_percent: number | null
}

export interface InstrumentPerformance {
  instrument_name: string
  action: string
  trade_count: number
  profit_loss: number
}

export interface Performance {
  from: string | null
  to: string | null
  timezone: string
  summary: PerformanceSummary
  daily: DailyPerformance[]
  by_action: ActionPerformance[]
  by_instrument: InstrumentPerformance[]
}

export interface Overview {
  date: string
  timezone: string
  trading_mode: string | null
  day: {
    trade_count: number
    closed_count: number
    performance_percent: number
    profit_loss: number
    best_percent: number | null
    worst_percent: number | null
  }
  limits: {
    profit_target_percent: number | null
    stop_entries_above_percent: number | null
    max_day_loss_percent: number | null
    close_position_time: string | null
    max_trades_per_day: number
    cooldown_after_loss_minutes: number
    trading_start_hour: number | null
    trading_end_hour: number | null
    risky_trading_start_hour: number | null
    risky_trading_start_minute: number | null
  }
  /** Today is one of the configured closed dates: the trader refuses every signal. */
  market_closed_today: boolean
  cooldown: {
    last_loss_at: string | null
    ends_at: string | null
    active: boolean
  }
  /** Set while the operator has paused new entries. */
  entries_paused_since: string | null
  open_positions: Position[]
  recent_days: DailyPerformance[]
}

export interface Signal {
  action: string
  indice: string
  signal_uuid: string
  signal_timestamp: string
  alert_timestamp: string
  received_timestamp: string
  confidence?: number
  seq: number
}

export type ServiceStatus = 'ok' | 'warning' | 'down' | 'unknown' | 'disabled'

export interface ServiceHealth {
  id: string
  name: string
  status: ServiceStatus
  detail: string
}

export interface QueueStats {
  name: string
  declared: boolean
  messages: number
  consumers: number
}

export interface WatchlistSnapshot {
  ready: boolean
  configured_targets?: number
  warm_entries?: number
  stale_entries?: number
  active_stream_subscriptions?: number
  desired_stream_subscriptions?: number
  last_refresh_completed_at?: string | null
  last_refresh_errors?: unknown[]
}

/** The health entry that is not a service: whether the Saxo token can still be renewed. */
export const SAXO_SERVICE_ID = 'saxo'

export interface Health {
  version: string
  checked_at: string
  status: 'ok' | 'warning' | 'down'
  services: ServiceHealth[]
  queues: QueueStats[]
  watchlist: WatchlistSnapshot | null
  config_modified_at: string
}

export interface SaxoStatus {
  has_token: boolean
  saved_at: string | null
  access_token_expires_at: string | null
  refresh_token_expires_at: string | null
  access_token_expired: boolean
  refresh_token_expired: boolean
  authorization_url: string
}

export interface LogServices {
  services: { id: string; name: string }[]
  levels: string[]
}

export interface LogEntry {
  timestamp: string | null
  level: string | null
  logger: string | null
  message: string
}

export interface LogTail {
  service: string
  exists: boolean
  modified_at: string | null
  entries: LogEntry[]
}

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export type JsonObject = { [key: string]: JsonValue }

export interface ConfigDocument {
  config: JsonObject
  revision: string
  editable_paths: string[]
  /** Inside the editable sections, but only changed on the server (where the services connect). */
  protected_paths: string[]
  editable: boolean
  restart_required: boolean
  modified_at: string
}

export interface ConfigSaved {
  revision: string
  backup: string
  restart_required: boolean
}

export interface SignalSent {
  signal_uuid: string
  signal: { action: string; indice: string; signal_timestamp: string; confidence?: number }
}

export type SignalOutcome = 'executed' | 'closed' | 'refused' | 'failed' | 'ignored'

export interface JournalEntry {
  signal_uuid: string
  action: string | null
  indice: string | null
  confidence: number | null
  signal_timestamp: string | null
  received_timestamp: string | null
  decided_at: string | null
  outcome: SignalOutcome | string
  reason: string | null
  position_id: string | null
  order_id: string | null
}

export interface GroupStats {
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  profit_loss: number
  average_percent: number | null
  average_best_percent: number | null
}

export interface Exits {
  from: string | null
  to: string | null
  timezone: string
  summary: GroupStats & {
    average_given_back: number | null
    turned_negative_count: number
    turned_negative_percent: number | null
  }
  by_reason: (GroupStats & { reason: string })[]
  by_hour: (GroupStats & { hour: number })[]
  by_holding_time: (GroupStats & { bucket: string })[]
}

/** A period of the Telegram report (a day, a week, a month...), as message_helper computes it. */
export interface TradeSummary {
  trade_count: number
  wins: number
  losses: number
  break_even: number
  win_rate: number
  net_profit: number
  /** Null when the period has no losing trade (the factor is infinite). */
  profit_factor: number | null
  max_drawdown: number
  compounded_percent: number
  avg_trade: number
  avg_win: number | null
  avg_loss: number | null
  best_trade: number
  worst_trade: number
}

export interface MilestoneProjection {
  milestone: number
  date: string
  periods_needed: number
  within_horizon: boolean
}

export interface BalancePoint {
  date_day: string
  total_value: number
  recorded_at: string | null
}

export interface Report {
  report_date: string
  has_trade_history: boolean
  streak: { current_streak: number; best_streak: number; last_win_date: string | null }
  daily_summary: TradeSummary
  by_direction: { long: TradeSummary; short: TradeSummary }
  cumulative: Record<string, TradeSummary & { days: number; start_date: string; end_date: string }>
  last_10_weeks: (TradeSummary & { week_start: string; week_label: string })[]
  last_12_months: (TradeSummary & { month_start: string; month_label: string; year: number })[]
  last_5_years: (TradeSummary & { year: number })[]
  averages: { weekly_percent: number; monthly_percent: number; yearly_percent: number }
  milestones: { weekly: MilestoneProjection[]; monthly: MilestoneProjection[] }
  current_balance: number | null
  balance_recorded_on: string | null
  balance_history: BalancePoint[]
}

export interface WatchlistItem {
  indice: string
  direction: string
  available: boolean
  description?: string | null
  symbol?: string | null
  currency?: string | null
  latest_bid?: number | null
  latest_ask?: number | null
  price_updated_at?: string | null
  search_refreshed_at?: string | null
  stale?: boolean | null
}

export interface Watchlist {
  enabled: boolean
  items: WatchlistItem[]
  error?: string
}

export interface TimingEntry {
  timestamp: string | null
  action: string | null
  confidence: number | null
  position_scale: number | null
  steps: Record<string, number>
  total_ms: number | null
}

export interface ExecutionTimings {
  steps: string[]
  entries: TimingEntry[]
  summary: {
    trade_count: number
    median_total_ms: number | null
    p90_total_ms: number | null
    slowest_total_ms: number | null
  }
}

export interface AuditEntry {
  at: string
  ip: string
  action: string
  detail: string
}

export interface ConfigBackup {
  name: string
  created_at: string
  size: number
}

export interface ConfigRestored {
  revision: string
  backup: string
  restored: string
  restart_required: boolean
}

/** What the server announces on /api/events. */
export interface LiveEvent {
  type: string
  [key: string]: unknown
}
