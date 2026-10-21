// Shapes of the admin API (src/admin/routers/*.py).

export interface Session {
  authenticated: boolean
  version: string
  timezone: string
  actions_enabled: boolean
  config_edit_enabled: boolean
  /** Backtests can be asked for (src/admin/backtests.py). */
  backtests_enabled: boolean
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
  /** Open positions only: the sells of it Saxo did not take, null when there were none. */
  sell_refusals?: SellRefusals | null
}

/** How often Saxo did not take the sell of an open position, and its latest answer. */
export interface SellRefusals {
  count: number
  first_at: string | null
  last_at: string | null
  outcome: RequestOutcome | null
  http_status: number | null
  error_code: string | null
  error_message: string | null
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
  /**
   * What became of the order, by the clock of the service that sent it. All
   * empty for an order placed before WATA kept it: only `order_time` is known
   * then, written once the position was confirmed.
   */
  purpose?: 'open' | 'close' | null
  /** Just before the order left. */
  sent_at?: string | null
  /** Saxo answered with the order's id. */
  accepted_at?: string | null
  /** The look at Saxo that showed the position open (a buy) or closed (a sell). */
  confirmed_at?: string | null
  /** When the service stopped looking, the position never having been shown. */
  gave_up_at?: string | null
  /** How many times Saxo was asked. */
  lookups?: number | null
  /** Empty while it is not known, or when the service stopped before knowing. */
  outcome?: 'confirmed' | 'gave_up' | 'error' | null
  /** A buy given up is cancelled: whether Saxo accepted that. */
  cancel_accepted?: boolean | null
  /** What Saxo answered when it refused the cancellation, or the error that stopped the order. */
  detail?: string | null
  recorded_by?: string | null
  /** The position a buy gave, or a sell closed. */
  position?: OrderPosition | null
  /** The signal a buy came from, when the journal knows it. */
  signal?: OrderSignal | null
  /** The requests sent to Saxo for it: the one that placed it, the cancellations about it. Absent before they were kept. */
  requests?: OrderRequest[]
}

export type RequestOutcome = 'accepted' | 'refused' | 'no_answer' | 'error'

/** One request sent to Saxo's order endpoint and what Saxo answered (src/trade/order_tracker.py). */
export interface OrderRequest {
  request_id: string
  /** When it left, and when the answer (or the failure) came back: the server's clock. */
  requested_at: string | null
  answered_at: string | null
  recorded_by: string | null
  operation: 'place' | 'cancel' | null
  purpose: 'open' | 'close' | 'stop' | null
  position_id: string | null
  signal_uuid: string | null
  action: string | null
  /** Why WATA sent it: the exit rule, "Explicit Close (...)"... */
  reason: string | null
  instrument_uic: number | null
  asset_type: string | null
  instrument_name: string | null
  instrument_symbol: string | null
  instrument_currency: string | null
  buy_sell: string | null
  amount: number | null
  order_type: string | null
  /** The bid (a sell) or the ask (a buy) it was decided on. */
  price_seen: number | null
  outcome: RequestOutcome | null
  /** The order Saxo gave, or the orders a cancellation names. */
  order_id: string | null
  http_status: number | null
  error_code: string | null
  error_message: string | null
  /** The JSON sent, without the account's keys, and Saxo's answer as received. */
  request_body: string | null
  response_body: string | null
}

/** One of Saxo's answers within a run, and how many times it was given. */
export interface RequestAnswer {
  outcome: RequestOutcome | null
  http_status: number | null
  error_code: string | null
  error_message: string | null
  count: number
  first_at: string
  last_at: string
}

/**
 * Attempts made with one intent (selling a position, buying for a signal) that
 * Saxo did not take (src/admin/order_requests.py). Its fields that mean what an
 * order's mean carry the order's names, so that a row of the Orders page reads both.
 */
export interface Refusal {
  run_id: string
  purpose: 'open' | 'close' | 'stop' | null
  position_id: string | null
  signal_uuid: string | null
  action: string | null
  reason: string | null
  instrument_name: string | null
  instrument_symbol: string | null
  instrument_uic: number | null
  instrument_currency: string | null
  asset_type: string | null
  buy_sell: string | null
  order_amount: number | null
  order_type: string | null
  instrument_price: number | null
  first_at: string
  last_at: string
  answered_at: string | null
  count: number
  outcome: 'refused' | 'no_answer' | 'error'
  /** Saxo's latest answer. */
  http_status: number | null
  error_code: string | null
  error_message: string | null
  recorded_by: string[]
  answers: RequestAnswer[]
  /** The latest attempts, oldest first, with the cancellations made on the way: `requests_total` attempts in all. */
  requests: OrderRequest[]
  requests_total: number
  /** The order Saxo took after them, if any. */
  followed_by: { order_id: string | null; requested_at: string } | null
  position?: OrderPosition | null
  signal?: (OrderSignal & { outcome: string | null; reason: string | null }) | null
}

export interface OrdersPage extends Page<Order> {
  refusals?: Refusal[]
}

export type OrderPosition = Pick<
  Position,
  | 'position_id'
  | 'position_status'
  | 'position_amount'
  | 'position_open_price'
  | 'position_close_price'
  | 'position_profit_loss'
  | 'position_total_performance_percent'
  | 'execution_time_open'
  | 'execution_time_close'
  | 'position_close_reason'
>

export interface OrderSignal {
  signal_uuid: string | null
  signal_timestamp: string | null
  received_timestamp: string | null
  decided_at: string | null
}

export interface Page<T> {
  items: T[]
  total: number
}

export interface PositionDetail {
  position: Position
  orders: Order[]
  /** Every request about it, oldest first: the latest ones when there are more than `requests_total` shows. */
  requests?: OrderRequest[]
  requests_total?: number
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

// ── stored prices (src/admin/routers/data.py) ──

export type DataProviderId = 'saxo' | 'dukascopy' | 'capital'

/**
 * An instrument whose prices WATA keeps, as every answer about its prices
 * describes it. `ref` ("saxo/12", "dukascopy/us100") is what identifies it;
 * `id` and `uic` are the recorder's and Saxo's, absent from a Dukascopy history.
 */
export interface PriceInstrument {
  provider: DataProviderId
  key: string
  ref: string
  id: number | null
  uic: number | null
  asset_type: string
  symbol: string | null
  description: string | null
  currency: string | null
  role: 'turbo' | 'underlying'
  indice: string | null
  underlying_uic: number | null
  direction: string | null
  /** Knock-out level of a turbo. */
  barrier: number | null
  issuer: string | null
  /**
   * In the list of a period only: the quote of its last bar there (`last_t` is
   * the start of that bar, epoch seconds), and what Saxo counted as traded in
   * the period (null: no volume was recorded for it).
   */
  last_t?: number | null
  last_bid?: number | null
  last_ask?: number | null
  volume?: number | null
}

/**
 * One bar, as the server sends it: start of the bucket (epoch seconds, UTC),
 * open/high/low/close of the bid, the same of the ask (null when the
 * instrument has none), and the number of quote changes received.
 */
export type PriceBarRow = [
  t: number,
  bidOpen: number,
  bidHigh: number,
  bidLow: number,
  bidClose: number,
  askOpen: number | null,
  askHigh: number | null,
  askLow: number | null,
  askClose: number | null,
  updates: number,
]

export interface PriceBars {
  instrument: PriceInstrument
  from: string
  to: string
  timezone: string
  /** Length of a bar, in seconds. */
  interval: number
  columns: string[]
  bars: PriceBarRow[]
  /**
   * The start of the last ten-second bar these bars hold (Capital.com): a live
   * chart adds only what follows it. Null for the other providers.
   */
  through?: number | null
}

/**
 * One range bar, as the server sends it: the time the chart is given for it (epoch
 * seconds, UTC, never twice the same: a place on the axis, not a time to show),
 * open, high, low, close, when it really began and ended (to the ten seconds),
 * whether it reached its range, whether it is the first after a pause in the data,
 * the index at its end (under a turbo), and its quote changes (what stands for volume).
 */
export type PriceRangeRow = [
  t: number,
  open: number,
  high: number,
  low: number,
  close: number,
  began: number,
  ended: number,
  complete: boolean,
  afterPause: boolean,
  index: number | null,
  volume?: number | null,
]

export interface PriceRangeBars {
  instrument: PriceInstrument
  /** The index whose level comes with each bar. */
  under: PriceInstrument | null
  from: string
  to: string
  timezone: string
  side: 'bid' | 'ask'
  /** One step of these prices: what "1R" is. */
  tick: number
  decimals: number
  /** The range of a bar in ticks (TradingView's R) and as a price move. */
  ticks: number
  size: number
  /** The range was chosen by the server for the period. */
  auto: boolean
  columns: string[]
  bars: PriceRangeRow[]
}

export interface PriceGap {
  start: string
  end: string
  seconds: number
  /** "down": the recorder was not running. "silent": it was, and no quote was arriving. */
  reason: 'down' | 'silent'
}

/** A time one instrument had no quote while the recorder was receiving others. */
export interface PriceStretch {
  start: string
  end: string
  seconds: number
  /**
   * "unquoted": between two of its bars of the same day. "stopped": after its
   * last bar of a day, while other turbos of its indice were still quoted.
   */
  reason: 'unquoted' | 'stopped'
}

/** What was recorded of one instrument in a period, and what is missing from it. */
export interface InstrumentAvailability {
  bars: number
  /** Quote changes received over the period. */
  updates: number
  first_bar: string
  last_bar: string
  quoted_seconds: number
  /** All the time it had no quote and could have had one: its holes, and what followed a stop. */
  missing_seconds: number
  /** Holes between two of its bars of a day: how many, and how long in all. */
  holes: number
  hole_seconds: number
  /** When it stopped being quoted for the rest of a day, for each day it did. */
  stops: string[]
  /** The longest twenty stretches of both kinds, in time order, of `stretch_count` in all. */
  stretches: PriceStretch[]
  stretch_count: number
  /** When a turbo stopped being quoted with a bid fallen to almost nothing: taken for a knock-out, not a fault. */
  knocked_out_at: string | null
  without_ask: number
  delayed_minutes: number
  /** What flags the instrument: holes in its day, or no more quote for the rest of one. */
  issues: ('holes' | 'stopped')[]
}

export interface PriceAvailability {
  from: string
  to: string
  timezone: string
  /** The longest period it is worked out for, in days. */
  max_days: number
  /** By instrument id; null when the period is longer than `max_days`. */
  items: Record<string, InstrumentAvailability> | null
}

export interface PriceContext {
  instrument: PriceInstrument
  trades: Position[]
  signals: JournalEntry[]
  /** null when the period is too long for the gaps to be worked out, or nothing recorded it (Dukascopy). */
  gaps: PriceGap[] | null
  /** What Saxo counted as traded in the period; null when it was not recorded. */
  volume: number | null
}

export type AssetStatus = 'recording' | 'ended' | 'downloaded'
export type AssetSeen = 'all' | '30d' | '7d' | 'today'

/** An asset of the Data pages: an instrument, its first and last bar, and what became of it. */
export interface DataAsset extends PriceInstrument {
  first_t: number | null
  last_t: number | null
  last_bid: number | null
  last_ask: number | null
  status: AssetStatus
  /** When the recorder first and last named it (Saxo). */
  first_seen_at?: string | null
  last_seen_at?: string | null
  /** Days of bars fetched (Dukascopy). */
  days?: number
  /**
   * The days of its first and last files (Dukascopy): days of UTC, as they
   * are fetched. East of UTC, the last hours of the last one fall on the next
   * day, which is not the day to open it on.
   */
  first_day?: string
  last_day?: string
  /** Capital.com's name of the instrument (Capital.com). */
  epic?: string
  instrument_type?: string | null
  decimals?: number | null
  /**
   * One step of its price, what a range of R<ticks> is counted in: it is the
   * asset's own (0.1 on Capital.com's US100, 0.001 on Dukascopy's). Null
   * while nothing says it (a Saxo instrument not yet quoted).
   */
  tick?: number | null
}

export interface DataAssets {
  timezone: string
  seen: AssetSeen
  items: DataAsset[]
  /** More than `max` recorded instruments: the oldest are left out. */
  truncated: boolean
  max: number
  /** Why the assets recorded from Saxo are missing (the database is down). */
  saxo_error: string | null
}

export interface SaxoRecording {
  first_seen_at: string | null
  last_seen_at: string | null
  retention_months: number | null
  record_volume: boolean | null
  refresh_interval_seconds: number | null
}

export interface HistoryRecording {
  symbol: string | null
  label: string
  /** Still one of the instruments that can be fetched. */
  fetchable: boolean
  days: number
  first_file: string | null
  last_file: string | null
  bytes: number
  location: string
  source: string
  price_scale: number
}

/** The longest period, in days, each view of an asset is worked out for. */
export interface AssetLimits {
  chart: number
  range: number
  statistics: number
  availability: number
  gaps: number
}

/** How late quotes arrived over the last minute, in milliseconds: Capital.com's time of a quote to the feed's receipt. */
export interface CapitalDelays {
  p50: number | null
  p95: number | null
  max: number | null
  count: number
}

/** The day files written for backtests, of one label. */
export interface CapitalDayFiles {
  label: string
  days: number
  first_day: string | null
  last_day: string | null
  bytes: number
  location: string
}

export interface CapitalRecording {
  epic: string
  label: string
  environment: string | null
  feed_state: string | null
  feed_detail: string | null
  feed_updated_at: string | null
  market: 'open' | 'closed' | 'unknown' | null
  next_opening: string | null
  /** As Capital.com gives them: {mon: ["00:00 - 21:59", ...], zone: "UTC"}. */
  opening_hours: Record<string, string[] | string> | null
  first_seen_at: string | null
  last_seen_at: string | null
  quotes_per_minute: number | null
  delay_ms: CapitalDelays | null
  minute_volume: boolean | null
  retention_months: number | null
  day_files: CapitalDayFiles
  /** The chart of a day still going follows the stream. */
  live: boolean
}

export interface AssetDetails {
  asset: DataAsset
  timezone: string
  /** The first and last days of the trading timezone with a bar. */
  first_day: string
  last_day: string
  /** Under a turbo, the index of its indice recorded in the period asked. */
  index: PriceInstrument | null
  recording: SaxoRecording | HistoryRecording | CapitalRecording
  max_days: AssetLimits
}

export interface AssetDayStatistics {
  day: string
  first: string
  last: string
  bars: number
  updates: number
  open: number
  high: number
  low: number
  close: number
  change: number
  change_percent: number | null
  range: number
  range_percent: number | null
  spread_average: number | null
  spread_widest: number | null
  without_ask: number
  /** How long the day was trading time, and the share of it with a bar. */
  trading_seconds: number
  covered: number | null
  volume: number | null
}

export interface AssetSpreadSlot {
  /** Start of the slot, HH:MM in the trading timezone. */
  slot: string
  bars: number
  spread_average: number | null
  spread_widest: number | null
  without_ask: number
}

export interface AssetStatistics {
  instrument: PriceInstrument
  from: string
  to: string
  timezone: string
  /** False for an instrument quoted with one price, which has no spread to speak of. */
  spread: boolean
  days: AssetDayStatistics[]
  spread_by_time: AssetSpreadSlot[]
}

export interface CatalogEntry {
  id: string
  name: string
  kind: 'live' | 'download'
  available: boolean
  description: string
}

export interface DataProvider extends CatalogEntry {
  id: DataProviderId
  status: ServiceStatus
  detail: string
  enabled: boolean
  /** Switched on or holding data: listed, rather than offered to be added. */
  active: boolean
  /** null: not known (the database is down). */
  assets: number | null
  first_t: number | null
  last_t: number | null
  /**
   * The days its data covers: those of its bars for a stream, those of its
   * files (days of UTC) for a downloaded history.
   */
  first_day: string | null
  last_day: string | null
  /** Its settings can be changed from the interface. */
  configurable: boolean
  /** The recorder's last line of coverage (Saxo). */
  heartbeat?: { ts: string; feed_ok: boolean; instruments: number; bars: number } | null
  /** The downloads (Dukascopy). */
  fetch?: PriceFetchStatus | null
  keep?: PriceKeepStatus | null
  /** What the Capital.com feed last wrote of itself; null when it never ran. */
  feed?: CapitalFeedState | null
}

export interface CapitalFeedInstrument {
  epic: string
  label: string
  name: string | null
  market: 'open' | 'closed' | 'unknown'
  status: string | null
  last_quote_at: string | null
  next_opening: string | null
  subscription: string | null
}

export interface CapitalFeedState {
  updated_at: string
  started_at: string
  version: string | null
  environment: string | null
  /** streaming, market_closed, connecting, starting, refused, database, no_credentials, disabled, invalid_config. */
  state: string
  detail: string | null
  figures: {
    instruments?: CapitalFeedInstrument[]
    quotes_per_minute?: number
    delay_ms?: CapitalDelays
    stream?: { connected: boolean; since: string | null; connections: number; last_error: string | null }
    live?: { sent: number; dropped: number; failing: boolean; last_error: string | null }
    minutes?: { enabled: boolean; last: string | null; error: string | null }
    day_files?: { enabled: boolean; written: number; last: string | null }
    pending_buckets?: number
    dropped_buckets?: number
    last_bar_at?: string | null
  }
}

export interface CapitalInstrumentSetting {
  epic: string
  label: string
}

/** The settings of the Capital.com feed (trade.config.capital_feed). */
export interface CapitalSettings {
  enabled: boolean
  environment: string
  instruments: CapitalInstrumentSetting[]
  minute_volume: boolean
  day_files: boolean
  retention_months: number
  alert_after_seconds: number
}

export interface CapitalProviderDetails extends DataProvider {
  settings: CapitalSettings
  gaps_today: PriceGap[] | null
  day_files: CapitalDayFiles[]
  /** The names of the variables of .env the login is given in: never anything of their values. */
  credential_variables: string[]
  restart_required: boolean
}

export interface CapitalConfig {
  section: JsonObject | null
  defaults: CapitalSettings
  environments: string[]
  indices: string[]
  revision: string
  editable: boolean
}

/** Capital.com's own volume of the minutes of a period, added up by interval. */
export interface CapitalVolume {
  instrument: PriceInstrument
  from: string
  to: string
  timezone: string
  interval: number
  columns: ['t', 'volume']
  volume: [t: number, volume: number | null][]
}

// ── the live stream of an asset (/api/data/assets/capital/<epic>/live) ──

/** Times of a message in milliseconds: quoted by Capital.com (t), received by the feed (rx), sent by the admin (sx). */
export interface LiveQuoteMessage {
  type: 'quote'
  ref: string
  t: number | null
  rx: number
  sx?: number
  bid: number
  ask: number
  /** The ten seconds the quote is part of, as recorded bars are. */
  bar: PriceBarRow | null
}

export interface LiveBarMessage {
  type: 'bar'
  ref: string
  bar: PriceBarRow
  sx?: number
}

export interface LiveStateMessage {
  type: 'state'
  ref: string
  market?: 'open' | 'closed'
  status?: string
  sx?: number
}

export interface LiveSnapshot {
  server_ms: number
  live: boolean
  detail: string | null
  quote: LiveQuoteMessage | null
  state: LiveStateMessage | null
}

export interface DataProviders {
  items: DataProvider[]
  catalog: CatalogEntry[]
  timezone: string
}

export interface RecorderInstrument {
  label: string
  uic: number
  asset_type: string
}

/** The settings of the price recorder (trade.config.price_recorder). */
export interface RecorderSettings {
  enabled: boolean
  record_volume: boolean
  max_instruments: number
  refresh_interval_seconds: number
  refresh_rate_ms: number
  retention_months: number
  alert_after_seconds: number
  instruments: RecorderInstrument[]
}

export interface SaxoProviderDetails extends DataProvider {
  settings: RecorderSettings
  /** What the recorder missed today in trading hours; null when it cannot be read. */
  gaps_today: PriceGap[] | null
  restart_required: boolean
}

export interface HistoryInstrument {
  symbol: string
  label: string
  name: string
  currency: string
  /** The Nasdaq history kept up to date: it cannot be removed. */
  built_in: boolean
  days: number
  first_day: string | null
  last_day: string | null
  bytes: number
}

export interface HistoryOffer {
  symbol: string
  label: string
  name: string
  currency: string
}

export interface DukascopyProviderDetails extends DataProvider {
  instruments: HistoryInstrument[]
  offered: HistoryOffer[]
  storage: HistoryStorage | null
  source: string
}

/** The recorder's section as it is on disk, and what its dialog needs. */
export interface RecorderConfig {
  section: JsonObject
  defaults: RecorderSettings
  /** What an index can be at Saxo, offered for an extra instrument. */
  asset_types: string[]
  /** The indices signals may name: an extra instrument labelled like one replaces its index. */
  indices: string[]
  revision: string
  editable: boolean
}

// ── backtests on the recorded turbos (src/admin/routers/backtests.py, src/backtest) ──

export type BacktestState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
/** What became of a signal: a position bought, refused by a rule, failed as the trader fails, nothing recorded to trade on... */
export type BacktestFate = 'executed' | 'refused' | 'failed' | 'unpriced' | 'closed' | 'ignored' | 'not_a_signal'
export type BacktestDayStatus = 'tested' | 'no_recorded_turbos' | 'no_strategy_bars' | 'closed'
export type BacktestValue = string | number | boolean
export type BacktestProvider = 'capital' | 'dukascopy' | 'saxo'

/** A setting a run may change, with its value in the live configuration. */
export interface BacktestField {
  name: string
  group: string
  kind: 'number' | 'integer' | 'boolean' | 'time' | 'choice'
  label: string
  least: number | null
  most: number | null
  choices: string[]
  unit: string
  /** Where the trading services read it; empty for the backtest's own. */
  source: string
  value: BacktestValue
}

export interface BacktestOptions {
  fields: BacktestField[]
  groups: string[]
  timezone: string
  indices: string[]
  /** The account a run starts with: the last daily report's value when there is one. */
  capital: { value: number; from_balance: boolean }
  signal_delay: { pine: number; journal: number }
  fixed: {
    time_periods: { start_minute: number; end_minute: number; scale_percent: number; label: string }[]
    confidence_bands: { least: number; below: number; scale_percent: number; label: string }[]
    default_confidence: number
    closed_dates: number
  }
  baseline: { repetitions: number; most: number; seed: number }
  sweep: { axes: number; cells: number; values: number; input_sets: number; held_out: number; least: number; most: number }
  limits: { period_days: number; queued: number; name_length: number; tags: number; tag_length: number; modelled_period_days?: number }
  /** Turbos made up from an index, once measured on the recorded ones. */
  turbo_model?: BacktestTurboMeasure | null
}

interface BacktestSpread {
  count: number
  median: number | null
  p90: number | null
}

/** What WATA's own orders took and cost (src/backtest/execution_calibration.py). */
export interface BacktestExecution {
  from: string
  to: string
  signals: number
  orders: number
  requests: number
  signal_delay_seconds: { from_signal: BacktestSpread; from_receipt: BacktestSpread }
  order_seconds: BacktestSpread
  /** Euros a turbo worse than the quote recorded when the order left. */
  slippage: Record<'buy' | 'sell' | 'all', BacktestSpread & { median_ticks: number | null }>
  refusals: { share_percent: number | null; by_hour: { hour: number; requests: number; refused: number }[]; codes: Record<string, number> }
  suggested: Partial<Record<'signal_delay_seconds' | 'order_seconds' | 'slippage', number>>
  /** Measures of a kind needed before a value is suggested. */
  least: number
}

/** A replay of the journal set beside WATA's real positions of the same signals (src/backtest/reconciliation.py). */
export interface BacktestReconciliationSummary {
  paired: number
  backtest_only: number
  live_only: number
  backtest_only_live_outcomes: Record<string, number>
  live_only_backtest_fates: Record<string, number>
  mean_result_difference: number | null
  median_result_difference: number | null
  correlation: number | null
  same_exit_percent: number | null
  same_turbo_percent: number | null
  median_entry_seconds: number | null
  backtest_total_percent: number | null
  real_total_percent: number | null
}

export interface BacktestReconciledPair {
  signal_uuid: string
  trade_id: number
  position_id: string
  day: string
  direction: 'long' | 'short'
  /** Seconds the backtest bought or sold after (positive) or before WATA. */
  entry_seconds: number | null
  exit_seconds: number | null
  same_turbo: boolean | null
  entry_price: { backtest: number | null; real: number | null }
  exit_price: { backtest: number | null; real: number | null }
  result: { backtest: number | null; real: number | null }
  exit: { backtest: string; real: string }
  same_exit: boolean
}

export interface BacktestReconciliation {
  summary: BacktestReconciliationSummary
  pairs: BacktestReconciledPair[]
  backtest_only: { trade_id: number; signal_uuid: string | null; live_outcome: string | null }[]
  live_only: { signal_uuid: string; position_id: string; backtest_fate: string }[]
}

/** A shadow rule's day traded on paper (src/admin/paper.py). */
export interface BacktestPaperDay {
  day: string
  run_id: string
  state: BacktestState
  day_percent: number | null
  trades: number | null
  /** Outside the 5 % to 95 % of its reference run's days; none without a result or a reference. */
  outside: boolean | null
}

export interface BacktestPaperReference {
  run_id: string
  name: string | null
  days: number
  low: number | null
  median: number | null
  high: number | null
}

export interface BacktestPaper {
  rules: Record<string, { days: BacktestPaperDay[]; reference: BacktestPaperReference | null }>
}

/** Recorded by WATA, or made up from an index (src/backtest/turbo_model.py). */
export type BacktestTurbos = 'recorded' | 'modelled'

export interface BacktestTurboDirection {
  /** Euros of turbo per point of index. */
  factor: number
  /** Points between the level its price would be nothing at and its knock-out level. */
  offset: number
  /** Points between two levels. */
  spacing: number
  turbos: number
  bars: number
  r2: number | null
  error_eur: number | null
  error_percent: number | null
}

export interface BacktestTurboMeasure {
  made_at: string
  underlying: { provider: BacktestProvider; key: string; side: 'bid' | 'ask' }
  indice: string
  days: number
  first: string | null
  last: string | null
  few_days: boolean
  directions: Record<'long' | 'short', BacktestTurboDirection>
  quoting: { first_minute: number; last_minute: number } | null
  issuer: string | null
}

export interface BacktestTurboModel {
  state: 'idle' | 'running' | 'done' | 'failed' | 'interrupted'
  error: string | null
  asked: { from: string; to: string } | null
  finished_at: string | null
  measure: BacktestTurboMeasure | null
  /** The index the turbos are made up from. */
  underlying: string
  max_days: number
}

export interface BacktestAsset {
  provider: BacktestProvider
  key: string
}

/** The asset a run's days are drawn on, and its bars. */
export interface BacktestChart {
  asset: BacktestAsset
  side: 'bid' | 'ask'
  bars: TaRuleBars
}

export interface BacktestSweepAxis {
  kind: 'setting' | 'input'
  name: string
  values: BacktestValue[]
}

export type BacktestSourceBody =
  | {
      kind: 'pine'
      script: { id: string; version: number }
      asset: BacktestAsset
      side: 'bid' | 'ask'
      bars: TaRuleBars
      inputs: TaRuleInput[]
      alerts: { title: string; action: TaAlertAction }[]
      alert_calls: 'ignore' | 'message'
      confidence_plot: string | null
      from_rule?: { id: string; name: string } | null
    }
  | { kind: 'journal' }

/** What the form sends. */
export interface BacktestRequestBody {
  name?: string
  tags?: string[]
  from: string
  to: string
  indice: string
  source: BacktestSourceBody
  /** For a replay of the journal: the asset its days are drawn on. */
  chart?: BacktestChart
  changes: Record<string, BacktestValue>
  baseline?: { repetitions: number; seed: number } | null
  sweep?: { axes: BacktestSweepAxis[]; held_out: number; walk_forward?: BacktestWindows | null } | null
  turbos?: BacktestTurbos
}

/** A walk-forward's windows: so many days to choose a combination on, then so many to keep it. */
export interface BacktestWindows {
  train_days: number
  test_days: number
}

export interface BacktestCoverageDay {
  day: string
  /** Turbos of the indice the recorder kept bars of, by direction. */
  turbos: { long: number; short: number }
  first: string | null
  last: string | null
  recorded_minutes: number
  /** Ten-second bars of the asset (1 for a day of Dukascopy fetched); null when no asset was asked of. */
  asset_bars: number | null
  /** Entries WATA received that day; null unless asked of. */
  signals: number | null
}

export interface BacktestCoverage {
  days: BacktestCoverageDay[]
  /** False when the database could not be read: only the files are known. */
  database: boolean
  timezone: string
}

/** The rule a Pine run runs as: the fields of a signal rule. */
export interface BacktestRule {
  id: string
  name: string
  asset: BacktestAsset
  side: 'bid' | 'ask'
  bars: TaRuleBars
  script: TaPin
  libraries: (TaPin & { name: string })[]
  inputs: TaRuleInput[]
  alerts: { title: string; action: TaAlertAction }[]
  alert_calls: 'ignore' | 'message'
  confidence_plot: string | null
  indice: string
}

export interface BacktestStoredRequest {
  kind: 'run' | 'sweep'
  name: string
  tags: string[]
  from: string
  to: string
  indice: string
  source:
    | { kind: 'pine'; rule: BacktestRule; from_rule: { id: string; name: string } | null }
    | { kind: 'journal'; signals: number; entries: number }
  chart: BacktestChart
  changes: Record<string, BacktestValue>
  baseline: { repetitions: number; seed: number } | null
  sweep: { axes: BacktestSweepAxis[]; held_out: number; walk_forward?: BacktestWindows | null; trials_before?: number } | null
  asked_at: string
  from_sweep?: { id: string; cell: number }
  /** A stress sweep: the run it made again with later and dearer fills. */
  stress_of?: string
  turbos?: BacktestTurbos
}

export interface BacktestStatus {
  state: BacktestState
  created_at?: string
  started_at?: string
  finished_at?: string
  error?: string | null
}

export interface BacktestHeadline {
  total_percent: number | null
  trades: number | null
  win_rate: number | null
  worst_drawdown: number | null
  sample: BacktestSample | null
  beaten: number | null
  days_tested: number | null
  profit_loss: number | null
  cells: number | null
}

export type BacktestSample = 'too_few' | 'few' | 'fair'

export interface BacktestRunListed {
  id: string
  kind: 'run' | 'sweep'
  name: string
  tags: string[]
  from: string
  to: string
  indice: string
  source: {
    kind: 'pine' | 'journal'
    script: { id: string; version: number; name: string } | null
    from_rule: { id: string; name: string } | null
    signals: number | null
  }
  chart: BacktestChart
  changes: Record<string, BacktestValue>
  status: BacktestStatus
  headline: BacktestHeadline | null
  /** Its place in the queue, while it waits. */
  position: number | null
  /** Made by other code than the admin's: what it found may differ now. */
  stale?: boolean
  /** A stress sweep: the run it made again with later and dearer fills. */
  stress_of?: string | null
  turbos?: BacktestTurbos
  /** Who asked for it through an agent ("claude"), and for which experiment of its journal; absent for the operator's. */
  made_by?: string | null
  experiment?: string | null
}

export interface BacktestTotalRange {
  low: number
  high: number
  level: number
  resamples: number
  losing_share: number
}

export interface BacktestFigures {
  days: number
  days_with_an_entry: number
  trades: number
  sample: BacktestSample
  win_rate: number | null
  average_win: number | null
  average_loss: number | null
  expectancy: number | null
  profit_factor: number | null
  longest_losing_streak: number
  total_percent: number
  total_range: BacktestTotalRange | null
  best_day: number | null
  worst_day: number | null
  median_day: number | null
  day_spread: number | null
  winning_days: number
  losing_days: number
  worst_drawdown: number
  winners_lowest: { median: number; tenth: number } | null
  losers_highest: { median: number; tenth: number } | null
  exits: Record<string, number>
  /** Absent from runs made before it was. */
  risk?: BacktestRisk
}

/** What the days and the trades say of the risk taken (src/backtest/robustness.py). Annualised from 20 days only. */
export interface BacktestRisk {
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  yearly_percent: number | null
  longest_under_water_days: number
  worst_5_percent_days: number | null
  skew: number | null
  kurtosis: number | null
  /** What a winning trade won for what a losing one lost. */
  payoff: number | null
  /** Of the best a winning trade reached, the share it kept, in percent. */
  exit_efficiency: number | null
  trades_per_day: number | null
  hours_held_per_day: number | null
}

/** The same days in another order, and without the best of them. */
export interface BacktestStress {
  monte_carlo: { draws: number; median: number; worst_5_percent: number; actual: number } | null
  without_best_days: { removed: number; total_percent: number }[]
}

export interface BacktestWalkWindow {
  /** First and last day. */
  train: [string, string]
  test: [string, string]
  cell: number
  values: BacktestValue[] | null
  train_percent: number
  test_percent: number
  /** Where the one chosen stood among all on the days kept: 1 is the best. */
  test_rank: number
  cells: number
}

export interface BacktestWalkForward extends BacktestWindows {
  windows: BacktestWalkWindow[]
  days: number
  total_percent: number
  worst_day: number | null
  worst_drawdown: number
  changes: number
  /** The best single combination over the same days, known only afterwards. */
  best_in_hindsight_percent: number
  /** Day, and the total up to it. */
  curve: [string, number][]
}

/** How the asset moved after a run's entry signals (src/backtest/edge.py), in basis points in each signal's direction. */
export interface BacktestEdgeHorizon {
  minutes: number
  signals: number
  mean_bp: number | null
  median_bp: number | null
  /** The share of signals the asset moved their way after, in percent. */
  hit_rate: number | null
  /** On the way, on average. */
  best_bp: number | null
  worst_bp: number | null
  /** The signals whose windows do not overlap: the ones the t-statistic counts. */
  independent: number
  t: number | null
  random: { mean_bp: number | null; hit_rate: number | null; beaten: number | null; draws: number }
}

export type BacktestEdgeGroup = Record<string, { signals: number; mean_bp: number | null; hit_rate: number | null }> & {
  hour?: number
  direction?: string
  fate?: 'executed' | 'not_taken'
  /** A kind of day's group (`by_day`). */
  group?: string
}

/** The groups a day is put in (src/backtest/regimes.py `label_days`). */
export type BacktestDayKind = 'volatility' | 'gap' | 'day_type' | 'us_open' | 'news'

export interface BacktestEdge {
  horizons: BacktestEdgeHorizon[]
  by_hour: BacktestEdgeGroup[]
  by_direction: BacktestEdgeGroup[]
  by_fate: BacktestEdgeGroup[]
  /** By the kind of day the signals came on; absent from runs made before. */
  by_day?: Partial<Record<BacktestDayKind, BacktestEdgeGroup[]>>
  unpriced: number
  draws: number
  seed: number
}

export interface BacktestDeflatedSharpe {
  probability: number
  sharpe_daily: number
  expected_max_sharpe_daily: number
  trials: number
  days: number
  sentence: string
}

export interface BacktestPbo {
  probability: number
  splits: number
  combinations: number
  sentence: string
}

export interface BacktestMoney {
  capital: number
  final: number
  profit_loss: number
  return_percent: number | null
  worst_drawdown: number
  worst_drawdown_percent: number
  average_stake: number | null
  average_scale_percent: number | null
  commissions: number
  unfunded: number
}

export interface BacktestAgainstRandom {
  repetitions: number
  seed: number
  entries: number
  beaten: number
  median: number
  low: number
  high: number
  level: number
  worst: number
  best: number
}

export interface BacktestCurvePoint {
  day: string
  percent: number
  cumulative: number
  profit_loss: number
  equity: number | null
}

export interface BacktestSignalTallies {
  total: number
  by_fate: Partial<Record<BacktestFate, number>>
  by_source: Record<string, number>
  refusals: Record<string, number>
  failures: Record<string, number>
}

export interface BacktestSummary {
  made_with: {
    format: number
    version: string
    code: string
    engine: { runner: { pinets: string; node: string }; milliseconds: number; history_days: number; restart_time: string } | null
    calendar?: { digest: string; until: string | null }
    turbos?:
      | { kind: 'recorded' }
      | {
          kind: 'modelled'
          made_at: string
          underlying: BacktestTurboMeasure['underlying']
          days: number
          few_days: boolean
          error_percent: Record<'long' | 'short', number | null>
        }
  }
  days: { asked: number; tested: number; by_status: Partial<Record<BacktestDayStatus, number>>; with_an_entry?: number }
  signals?: BacktestSignalTallies
  figures?: BacktestFigures
  money?: BacktestMoney
  curve?: BacktestCurvePoint[]
  against_random?: BacktestAgainstRandom | null
  /** A run's: absent from runs made before it was. */
  stress?: BacktestStress
  /** A replay of the journal's, beside WATA's real positions. */
  reconciliation?: (BacktestReconciliationSummary & { sentence: string }) | null
  edge?: { horizons: (Pick<BacktestEdgeHorizon, 'minutes' | 'signals' | 'mean_bp' | 'hit_rate' | 't' | 'independent'> & { random_mean_bp: number | null; beaten: number | null })[]; unpriced: number }
  /** A sweep's: null when asked for with too few days, absent when not asked for. */
  walk_forward?: BacktestWalkForward | null
  pbo?: BacktestPbo | null
  deflated_sharpe?: BacktestDeflatedSharpe | null
  warnings: string[]
  cells?: number
}

export interface BacktestProgress {
  phase: 'signals' | 'days' | 'report' | 'done'
  done: number
  total: number
  day: string | null
}

export interface BacktestRun extends BacktestRunListed {
  request: BacktestStoredRequest
  settings: { settings: Record<string, unknown>; changed: Record<string, BacktestValue>; live_before: Record<string, BacktestValue> }
  summary: BacktestSummary | null
  progress: BacktestProgress | null
  lines: string[]
}

export interface BacktestDay {
  day: string
  status: BacktestDayStatus
  /** The day's signals by what became of them; a fate none had may be missing. */
  signals: Partial<Record<BacktestFate, number>>
  entries: number
  trades: number
  day_percent: number
  profit_loss: number
  equity: number | null
  recorded: { long: number; short: number; minutes: number; first: string | null; last: string | null } | null
  strategy_bars: number | null
}

/** One of the turbos the trader looked at when it chose. */
export interface BacktestCandidate {
  instrument_id: number
  symbol: string | null
  issuer: string | null
  barrier: number
  bid: number | null
  ask: number | null
  in_range: boolean
  chosen: boolean
}

export interface BacktestSignal {
  id: number
  day: string
  source: 'pine' | 'journal' | 'schedule'
  action: string | null
  indice: string
  confidence: number | null
  arrived_at: string | null
  decided_at: string | null
  fate: BacktestFate
  rule: string | null
  reason: string | null
  trade_id: number | null
  closed_trade_ids: number[]
  candidates: { looked_at: number; quoted: number; in_range: number } | null
  selection?: BacktestCandidate[]
  // A Pine script's: the bar it was raised on and the alert.
  bar_time?: string
  bar_closed_at?: string
  alert?: { type: string; title: string | null; message: string }
  note?: string | null
  // The journal's: what the live trader did of it.
  signal_uuid?: string | null
  signal_time?: string | null
  received_at?: string | null
  live_outcome?: string | null
  live_reason?: string | null
}

export interface BacktestTurbo {
  instrument_id: number
  uic: number | null
  asset_type: string
  symbol: string | null
  description: string | null
  issuer: string | null
  barrier: number
  direction: string
  currency: string | null
}

export interface BacktestTrade {
  id: number
  day: string
  direction: 'long' | 'short'
  signal_id: number | null
  confidence: number | null
  scale_percent: number
  turbo: BacktestTurbo
  entry: {
    decided_at: string
    selected_at: string
    filled_at: string
    ask: number
    bid: number
    selected_ask: number
    selected_bid: number
    price: number
    asset_price: number | null
    candidates: BacktestCandidate[]
  }
  exit: {
    filled_at: string
    bid: number
    reason: string
    group: string
    kind: string
    signal_id: number | null
    asset_price: number | null
  }
  units: number
  cost: number
  proceeds: number
  commission: number
  profit_loss: number
  performance_percent: number
  max_performance_percent: number
  min_performance_percent: number | null
  holding_seconds: number
  day_percent_before: number
  cash_after: number
  notes: string[]
}

export interface BacktestGroupRow {
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  profit_loss: number
  average_percent: number | null
  average_best_percent: number | null
  [name: string]: string | number | null
}

export interface BacktestBreakdowns {
  trades: number
  groups: Record<'hour' | 'weekday' | 'direction' | 'exit' | 'confidence' | 'holding' | 'turbo_price' | 'issuer', BacktestGroupRow[]> &
    /** The kind of day a trade was made on and its place against the US open (src/backtest/regimes.py), under `group`. */
    Partial<Record<BacktestDayKind | 'session_part', BacktestGroupRow[]>>
  given_back: { average_given_back: number | null; turned_negative_count: number; turned_negative_percent: number | null }
}

export interface BacktestBaseline {
  repetitions: number
  seed: number
  entries: number
  hours: [number, number] | null
  totals: number[]
  trades: number[]
  histogram: { from: number; to: number; count: number }[]
}

export interface BacktestSweepCell {
  values: BacktestValue[]
  tuning: BacktestFigures | null
  held_out: BacktestFigures | null
  whole: BacktestFigures | null
  money: BacktestMoney
  signals: BacktestSignalTallies
  /** Day, its result in percent, its trades. Absent from sweeps made before it was. */
  curve?: [string, number, number][]
}

export interface BacktestSweep {
  axes: BacktestSweepAxis[]
  held_out: number
  held_out_from: string | null
  cells: BacktestSweepCell[]
}

/** Each finished day written as files for a research machine (src/admin/research_export.py). */
export interface ResearchFiles {
  enabled: boolean
  keep_days: number
  state: 'off' | 'waiting_for_close' | 'up_to_date' | 'due' | 'exporting'
  /** The day being written, while one is. */
  exporting: string | null
  days: number
  first: string | null
  last: string | null
  bytes: number
  error: string | null
  /** Under the price history folder of the server. */
  location: string
  limits: { keep_days: number }
}

export interface BacktestStorage {
  runs: { count: number; bytes: number }
  /** What the former backtester left on the disk. */
  former: { count: number; bytes: number }
}

// ── Dukascopy's history: what is there and its downloads (src/admin/downloads.py) ──

/** The disk the downloaded prices take, of every instrument. */
export interface HistoryStorage {
  days: number
  bars_bytes: number
  fetched_bytes: number
}

export interface PriceCoverageDay {
  day: string
  /**
   * closed: a weekend day, with nothing to fetch. partial: usable, its last
   * hours were not published yet. unavailable: the source has nothing for it.
   */
  state: 'ready' | 'partial' | 'incomplete' | 'missing' | 'closed' | 'unavailable'
  hours: number
  hours_expected: number
}

/** The fetching of what is missing by the service itself. */
export interface PriceKeepStatus {
  enabled: boolean
  /** How far back it goes. */
  days: number
  state: 'off' | 'fetching' | 'up_to_date' | 'pausing' | 'backing_off' | 'waiting_for_close' | 'due'
  /** Days of that period that are not whole yet. */
  missing: number
  /** Days the source has no more of, which are not asked for again. */
  settled: number
  /** Downloads in a row during which the server stopped answering. */
  failures: number
  next_at: string | null
  limits: { days: number }
}

export interface PriceFetchStatus {
  state: 'idle' | 'running' | 'done' | 'incomplete' | 'stopped' | 'failed' | 'interrupted'
  from?: string
  to?: string
  /** The Dukascopy instrument downloaded; absent before downloads said it (the history kept up to date). */
  label?: string
  symbol?: string
  started_at?: string
  finished_at?: string
  exit_code?: number | null
  /** Started by the service itself, to keep the prices up to date. */
  automatic?: boolean
  /** The last lines the downloader wrote. */
  lines: string[]
}

export interface PriceCoverage {
  from: string
  to: string
  /** The Dukascopy instrument these days are of. */
  label: string
  symbol: string | null
  days: PriceCoverageDay[]
  ready: { count: number; first: string | null; last: string | null }
  fetch: PriceFetchStatus
  keep: PriceKeepStatus
}

// ── Technical Analysis: the Pine scripts kept by the admin (src/admin/ta_scripts.py) ──

export type TaScriptKind = 'indicator' | 'strategy' | 'library'

/** An `import user/Name/1 as alias` line of a script. */
export interface TaImport {
  user: string
  name: string
  version: number
  alias: string
}

/** One saved version: each save of a changed source is a new one, never written again. */
export interface TaVersionEntry {
  number: number
  saved_at: string
  size: number
  sha256: string
}

export interface TaScriptSummary {
  id: string
  name: string
  kind: TaScriptKind
  description: string
  pine_version: number
  /** The name a library declares, library("Name"): what `import user/Name/1` finds. */
  library_name: string | null
  imports: TaImport[]
  /** The current version. */
  version: number
  versions: TaVersionEntry[]
  created_at: string
  updated_at: string
  /** "claude" for a script Claude made through the admin's MCP endpoint. */
  made_by?: string | null
}

export interface TaScript extends TaScriptSummary {
  source: string
}

export interface TaVersion extends TaVersionEntry {
  source: string
}

// ── Technical Analysis: the signal rules (src/admin/ta_rules.py) and what the TA engine did ──

export type TaRuleMode = 'off' | 'shadow' | 'live'
/** What an alertcondition() of the script becomes. */
export type TaAlertAction = 'ignore' | 'long' | 'short' | 'close-position' | 'message'

export type TaRuleBars = { type: 'time'; timeframe: string } | { type: 'range'; ticks: number }

/** A version of a script or library the rule runs, with the sha256 it was saved with. */
export interface TaPin {
  id: string
  version: number
  sha256: string
}

export interface TaRuleInput {
  /** The input's id in the script, as PineTS reads it: in_0, in_1... */
  id: string
  var: string | null
  title: string | null
  value: number | string | boolean
}

/** What a rule saves: everything but its id, mode and times. */
export interface TaRuleFields {
  name: string
  asset: { provider: 'capital'; key: string }
  side: 'bid' | 'ask'
  bars: TaRuleBars
  script: { id: string; version: number }
  inputs: TaRuleInput[]
  alerts: { title: string; action: TaAlertAction }[]
  alert_calls: 'ignore' | 'message'
  confidence_plot: string | null
  indice: string
  /** Its own days of bars before each engine day; absent or null: the engine's history_days. */
  history_days?: number | null
}

export interface TaRule extends Omit<TaRuleFields, 'script'> {
  id: string
  script: TaPin
  libraries: (TaPin & { name: string })[]
  mode: TaRuleMode
  created_at: string
  updated_at: string
  mode_changed_at: string
  /** "claude" for a shadow rule Claude made through the admin's MCP endpoint. */
  made_by?: string | null
  /** Added by the admin: the script's name, and the versions there are now. */
  script_name: string | null
  latest: { script: number | null; libraries: Record<string, number | null> }
}

export interface TaRules {
  revision: string
  rules: TaRule[]
  /** The indices the trader accepts (the allowed_indices rule). */
  indices: string[]
  /** The instruments the Capital.com feed records: what a rule can run on. */
  instruments: { epic: string; label: string }[]
  /** The engine's history_days: what a rule without its own is given. */
  engine_history_days: number
}

export interface TaRuleSaved {
  rule: TaRule
  revision: string
}

/** A rule as the engine runs it (ta_rule_state). */
export interface TaRuleState {
  updated_at: string
  state: 'loading' | 'waiting' | 'running' | 'error' | 'off'
  detail: string | null
  mode: TaRuleMode
  script_version: number | null
  bars: number | null
  last_bar_time: string | null
  last_alert_at: string | null
  figures: { milliseconds?: number | null } & Record<string, unknown>
}

export interface TaEngine {
  enabled: boolean
  status: 'ok' | 'warning' | 'down' | 'disabled' | 'unknown'
  detail: string
  engine: {
    updated_at: string
    started_at: string
    version: string | null
    state: string
    detail: string | null
    figures: Record<string, unknown>
  } | null
  rules: Record<string, TaRuleState>
}

/** What the trader made of a signal the engine sent (signal_journal). */
export interface TaTraderDecision {
  decided_at: string
  outcome: string
  reason: string | null
  position_id: string | null
}

export interface TaSignal {
  id: number
  rule_id: string
  rule_name: string
  script_id: string
  script_version: number
  asset: string
  side: string
  bars: string
  bar_time: string
  bar_closed_at: string
  alert_key: string
  alert_title: string | null
  alert_message: string | null
  action: string | null
  indice: string
  confidence: number | null
  mode: 'shadow' | 'live'
  raised_at: string
  outcome: 'shadow' | 'sending' | 'sent' | 'refused' | 'failed'
  sent_at: string | null
  http_status: number | null
  signal_uuid: string | null
  detail: string | null
  trader: TaTraderDecision | null
}

/** A signal the trader received from elsewhere than the engine (TradingView's). */
export interface TaOtherSignal {
  signal_uuid: string
  action: string
  indice: string
  confidence: number | null
  signal_timestamp: string | null
  received_timestamp: string | null
  decided_at: string | null
  outcome: string | null
  reason: string | null
  position_id: string | null
}

export interface TaComparisonRow {
  at: string
  action: string | null
  wata: TaSignal | null
  tradingview: TaOtherSignal | null
  delta_seconds: number | null
}

export interface TaComparison {
  day: string
  indice: string
  tolerance_seconds: number
  rows: TaComparisonRow[]
  paired: number
  only_wata: number
  only_tradingview: number
}

// ── Claude's research through the admin's MCP endpoint (src/admin/routers/research.py) ──

export type McpTokenState = 'active' | 'expired' | 'revoked'

/** A token the endpoint is opened with, never its secret: made on the server (watamcptoken), printed once. */
export interface McpToken {
  id: string
  name: string
  created_at: string
  expires_at: string
  revoked_at: string | null
  state: McpTokenState
  /** Since the admin started. */
  last_used_at: string | null
}

export type McpCallOutcome = 'ok' | 'refused' | 'failed' | 'cancelled'

/** A call Claude made, its long arguments (a Pine source) as their sha256 and length. */
export interface McpCall {
  at: string
  caller: string | null
  tool: string
  arguments: Record<string, unknown>
  outcome: McpCallOutcome
  milliseconds: number
  detail?: string
}

/** What Claude may take of the server (admin.mcp in config.json). */
export interface McpLimits {
  max_waiting_runs: number
  runs_per_day: number
  max_day_simulations: number
  study_minutes_per_hour: number
  study_memory_mb: number
  study_timeout_seconds: number
  max_scripts: number
  max_shadow_rules: number
  /** What a day of the engine may cost one of Claude's shadow rules, in ms. */
  shadow_day_ms: number
  min_free_disk_gb: number
  shadow_rules: boolean
}

/** What Claude's work holds of the server now; null where the admin has no such service. */
export interface McpShare {
  waiting_runs: number | null
  runs_today: number | null
  scripts: number | null
  /** Claude's rules that are not live, and those the operator took live. */
  rules: number | null
  live_rules: number | null
  study_seconds_last_hour: number | null
}

export interface McpState {
  /** admin.mcp_enabled in config.json: without it there is no endpoint at all. */
  enabled: boolean
  paused: { paused: boolean; changed_at: string | null }
  /** False when the tokens file is damaged: no token opens the endpoint then. */
  tokens_readable: boolean
  tokens: McpToken[]
  limits: McpLimits
  share: McpShare
  calls: McpCall[]
}

export interface ExperimentRun {
  run: string
  kind: string
  /** The configurations it tried: a run 1, a sweep its combinations, a stress 0. */
  trials: number
  lockbox: boolean
  note: string
  at: string
}

export type ExperimentVerdict = 'kept' | 'rejected' | 'inconclusive'

/** An experiment of the journal Claude keeps on the server, its runs and its conclusion. */
export interface Experiment {
  id: string
  title: string
  hypothesis: string
  family: string
  plan: string | null
  made_by: string | null
  opened_at: string
  runs: ExperimentRun[]
  trials: number
  verdict: ExperimentVerdict | null
  notes: string | null
  concluded_at: string | null
}

/** The days kept out of the research until a candidate's final check; ``to`` null: every day after ``from``. */
export interface ResearchLockbox {
  from: string
  to: string | null
  set_at: string | null
  set_by: string | null
}

export interface Experiments {
  /** The last opened first. */
  experiments: Experiment[]
  lockbox: ResearchLockbox | null
}
