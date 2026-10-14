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

export type DataProviderId = 'saxo' | 'dukascopy'

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
}

/**
 * One range bar, as the server sends it: the time the chart is given for it (epoch
 * seconds, UTC, never twice the same: a place on the axis, not a time to show),
 * open, high, low, close, when it really began and ended (to the ten seconds),
 * whether it reached its range, whether it is the first after a pause in the data,
 * and the index at its end (under a turbo).
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

export interface AssetDetails {
  asset: DataAsset
  timezone: string
  /** The first and last days of the trading timezone with a bar. */
  first_day: string
  last_day: string
  /** Under a turbo, the index of its indice recorded in the period asked. */
  index: PriceInstrument | null
  recording: SaxoRecording | HistoryRecording
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
  /** The Nasdaq history backtests read: it cannot be removed. */
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
  storage: BacktestStorage | null
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

// ── backtests (src/admin/routers/backtests.py) ──

export type BacktestState = 'queued' | 'running' | 'done' | 'failed' | 'cancelled' | 'interrupted'
export type BacktestValue = string | number | boolean

/** A parameter of a strategy, as the form offers it. */
export interface BacktestParameter {
  name: string
  kind: 'integer' | 'number' | 'text'
  default: string | number
  help: string
  least: number | null
  most: number | null
  choices: string[]
  advanced: boolean
}

/** A rule of WATA that one backtest may be run with another value of. */
export interface BacktestEditableRule {
  name: string
  kind: 'integer' | 'number' | 'boolean'
  label: string
  least: number | null
  most: number | null
}

export interface BacktestTurbo {
  entry_price: number
  spread: number
  leverages: number[]
}

/** The turbo a run is made with unless it says otherwise: measured on real trades, or assumed. */
export interface BacktestDefaultTurbo extends BacktestTurbo {
  measured: boolean
}

/** How unkind the fills of a run are taken to be, and the account its money is counted on. */
export interface BacktestExecution {
  /** 0: no money figures. */
  capital: number
  /** Seconds between a decision and its fill, on top of the next ten seconds. */
  fill_delay: number
  /** Index points lost against the trade on each fill. */
  slippage: number
}

export interface BacktestOptions {
  strategies: { name: string; parameters: BacktestParameter[] }[]
  turbo: BacktestDefaultTurbo
  execution: BacktestExecution
  rules: { live: Record<string, BacktestValue | string[] | number[][]>; editable: BacktestEditableRule[] }
  limits: {
    period_days: number
    leverages: number
    queued: number
    fetch_days: number
    note_length: number
    fill_delay: number
    slippage: number
    sweep_cells: number
    sweep_values: number
    tags: number
    tag_length: number
  }
  /** The share of the days a sweep holds out, and between what it can be set. */
  held_out: { share: number; least: number; most: number }
  timezone: string
}

export interface BacktestStatus {
  state: BacktestState
  created_at?: string
  started_at?: string
  finished_at?: string
  error?: string | null
}

/** How much the figures of a run rest on: under 30 trades or 20 days, under 100 trades or 60 days, or more. */
export type BacktestSample = 'too_few' | 'few' | 'fair'

/** The same days traded with entries at random under the same rules, many times over. */
export interface BacktestAgainstRandom {
  repetitions: number
  seed: number
  /** Entries drawn in each repetition: as many as the run asked for. */
  entries: number
  /** Share of the repetitions the run did better than, in percent. */
  beaten: number
  median: number
  /** `level` percent of the repetitions ended between `low` and `high`. */
  low: number
  high: number
  level: number
  worst: number
  best: number
  trades: number
  win_rate: number | null
  /** Every repetition's total, lowest first. */
  totals: number[]
}

/** Where the total falls when the days tested are drawn again, with replacement. */
export interface BacktestTotalRange {
  low: number
  high: number
  level: number
  resamples: number
  /** Share of the draws that end in a loss, in percent. */
  losing_share: number
}

/** What a backtest found for one leverage of the turbo. */
export interface BacktestFigures {
  days: number
  days_with_an_entry: number
  trades: number
  win_rate: number | null
  average_win: number | null
  average_loss: number | null
  total_percent: number
  best_day: number | null
  worst_day: number | null
  worst_drawdown: number
  index_points: number
  exits: Record<string, number>
  entries: Record<string, number>
  refusals: Record<string, number>
  // What follows is not in a run made before it was measured.
  sample?: BacktestSample
  /** What a trade brings on average, in percent. */
  expectancy?: number | null
  /** What was won for 1 lost; null without a loss. */
  profit_factor?: number | null
  longest_losing_streak?: number
  total_range?: BacktestTotalRange | null
  median_day?: number | null
  day_spread?: number | null
  winning_days?: number
  losing_days?: number
  /** Half of the winning trades never fell below `median`, nine in ten never below `tenth`. */
  winners_lowest?: { median: number; tenth: number } | null
  /** Half of the losing trades had been above `median`, one in ten above `tenth`. */
  losers_highest?: { median: number; tenth: number } | null
  against_random?: BacktestAgainstRandom | null
  /** The same trades at the size WATA gives each, on an account of the run's capital. */
  money?: BacktestMoney | null
}

export interface BacktestMoney {
  capital: number
  final: number
  profit_loss: number
  return_percent: number
  worst_drawdown: number
  worst_drawdown_percent: number
  /** What WATA's sizing put on a trade on average, of what an order may use. */
  average_scale_percent: number | null
  /** Entries the account could not buy a single turbo for. */
  unfunded: number
}

/** A setting a sweep gives several values to. */
export interface SweepAxis {
  kind: 'parameter' | 'rule'
  name: string
  values: BacktestValue[]
}

/** One combination of a sweep: its figures before the days held out, on them, and on the whole period. */
export interface SweepCell {
  values: BacktestValue[]
  tuning: BacktestFigures
  /** null when the period had too few days to hold any out. */
  held_out: BacktestFigures | null
  whole: BacktestFigures
}

export interface BacktestSweep {
  axes: SweepAxis[]
  held_out: { share: number; from: string | null; days: number; tuning_days: number }
  cells: SweepCell[]
  days_without_prices?: string[]
  bars?: number
  seconds?: number
}

/** Whether a run would come out otherwise today: made with other code, or with other prices. */
export interface BacktestStale {
  code: boolean
  prices: boolean
  /** The version of WATA it was made with. */
  version: string | null
}

/** The figures of the middle leverage: what a list shows of a run. */
export interface BacktestHeadline {
  leverage: number
  days: number | null
  days_with_an_entry: number | null
  trades: number | null
  win_rate: number | null
  total_percent: number | null
  worst_drawdown: number | null
  sample?: BacktestSample | null
  /** Share of the random entries it did better than. */
  beaten?: number | null
}

export interface BacktestRunListed {
  id: string
  /** A strategy replayed, the signals WATA really received, or a sweep of either. Absent in the first runs: a strategy. */
  kind?: 'strategy' | 'signals' | 'sweep'
  /** For a sweep: what it replays, the settings it gives several values to, and the share of the days it holds out. */
  replays?: 'strategy' | 'signals'
  axes?: SweepAxis[]
  held_out?: number
  execution?: BacktestExecution
  tags?: string[]
  /** null for a run that did not keep what it was made with. */
  stale?: BacktestStale | null
  /** For a run of real signals: how many were replayed, and the real trades to set beside. */
  signals?: number
  real_trades?: number
  from: string
  to: string
  strategy: string
  /** Only what differs from the strategy's defaults. */
  parameters: Record<string, BacktestValue>
  /** Only the rules that differ from the live configuration, and what that said. */
  rules: Record<string, BacktestValue>
  rules_before?: Record<string, BacktestValue>
  turbo: BacktestTurbo
  note: string
  status: BacktestStatus
  headline: BacktestHeadline | null
}

export interface BacktestSummary {
  parameters: Record<string, BacktestValue>
  bars: number
  decisions: number
  seconds: number
  days_without_prices: string[]
  days_incomplete: string[]
  /** For a run of real signals: those replayed, and the moments of those that came without prices. */
  signals?: { replayed: number; without_prices: string[] } | null
  runs: { turbo: { entry_price: number; points_per_unit: number; spread: number }; summary: BacktestFigures }[]
}

export interface BacktestRun extends BacktestRunListed {
  summary: BacktestSummary | null
  progress: { day: string; days_begun: number; bars: number } | null
  /** The whole set of rules it ran with. */
  run_rules: Record<string, BacktestValue | string[]> | null
  /** The last lines the command wrote, when it failed. */
  output: string[]
  /** Runs that will be taken before this one. */
  waiting_before: number
  /** What a sweep found. */
  sweep?: BacktestSweep | null
  /** The disk its folder takes. */
  bytes?: number
}

export interface BacktestTrade {
  entry_time: string
  exit_time: string
  direction: string
  entry_reason: string
  confidence: number | null
  entry_index: number
  exit_index: number
  index_points: number
  performance_percent: number
  max_performance_percent: number
  exit_reason: string
  /** The worst level the trade went through. Not in a run made before it was kept. */
  min_performance_percent?: number | null
  /** The share of what an order may use that WATA's sizing puts on it, and in money what that was and brought. */
  scale_percent?: number | null
  stake?: number | null
  profit_loss?: number | null
}

export interface BacktestDay {
  day: string
  entries: number
  closed: number
  day_percent: number
  /** In money, and the account after the day, when the run was given a capital. */
  profit_loss?: number | null
  equity?: number | null
}

/** The figures of the trades of one group: an hour, a weekday, a reason... */
export interface BacktestGroup {
  group: string | number
  trade_count: number
  win_count: number
  loss_count: number
  win_rate: number | null
  /** In money, when the run was given a capital. */
  profit_loss: number
  average_percent: number | null
  average_best_percent: number | null
  /** The sum of the results, in points of percent. */
  total_percent: number
}

export interface BacktestBreakdowns {
  id: string
  leverage: number
  timezone: string
  summary: Omit<BacktestGroup, 'group'> & {
    average_given_back: number | null
    turned_negative_count: number
    turned_negative_percent: number | null
  }
  groups: Record<'hour' | 'weekday' | 'direction' | 'entry_reason' | 'confidence' | 'holding_time' | 'exit', BacktestGroup[]>
}

/** One trade as the position monitor lived it. */
export interface BacktestTradePath {
  id: string
  leverage: number
  timezone: string
  trade: BacktestTrade
  day_before_percent: number
  /** Each: when its ten seconds began (epoch), the turbo's result at their end, worst and best, and the trailing stop once armed. */
  points: [number, number, number, number, number | null][]
  levels: {
    stop: number
    take_profit: number
    day_target: number
    trailing_activation: number | null
    trailing_drawdown: number | null
  }
}

export interface BacktestStorage {
  runs: { count: number; bytes: number }
  prices: { days: number; bars_bytes: number; fetched_bytes: number }
}

export interface BacktestRefusal {
  time: string
  direction: string
  reason: string
}

export interface BacktestDecision {
  at: number
  target: 'flat' | 'long' | 'short'
  reason: string
  confidence: number | null
}

/** One day of a run as the strategy saw it: a value per range bar in each list. */
export interface BacktestDayChart {
  id: string
  day: string
  timezone: string
  leverage: number
  tick: number
  decimals: number
  size: number
  session: { start: number; end: number }
  bars: PriceRangeRow[]
  kernel: (number | null)[]
  vwap: (number | null)[]
  upper: (number | null)[]
  lower: (number | null)[]
  entries_allowed: boolean[]
  decisions: BacktestDecision[]
  trades: BacktestTrade[]
  refusals: BacktestRefusal[]
}

/** A replay of the signals WATA received in a period. */
export interface BacktestSignalsRequest {
  from: string
  to: string
  rules: Record<string, BacktestValue>
  entry_price: number
  spread: number
  leverages: number[]
  note: string
  capital: number
  fill_delay: number
  slippage: number
}

/** The same backtest for every combination of the values of one or two settings. */
export interface BacktestSweepRequest {
  from: string
  to: string
  replay: 'strategy' | 'signals'
  axes: SweepAxis[]
  held_out: number
  strategy: string
  parameters: Record<string, BacktestValue>
  rules: Record<string, BacktestValue>
  entry_price: number
  spread: number
  leverage: number
  note: string
  fill_delay: number
  slippage: number
}

export interface BacktestRequest extends BacktestSignalsRequest {
  strategy: string
  parameters: Record<string, BacktestValue>
}

/** A trade, real or of the model, in what the two have in common. */
export interface BacktestLeg {
  direction: string
  /** Epoch seconds. */
  entered_at: number
  left_at: number
  performance: number
  exit: string
  /** The position at Saxo, for a real trade. */
  reference: string
}

/** A run of real signals set against what WATA really did with them. */
export interface BacktestComparison {
  id: string
  leverage: number
  timezone: string
  /** What the trader made of the signals, as its journal has it. */
  journal: Record<string, number>
  summary: {
    real_trades: number
    model_trades: number
    paired: number
    /** Model minus real, in points of percent. */
    average_gap: number | null
    median_gap: number | null
    ninth_gap: number | null
    same_exit: number
    real_total: number
    model_total: number
    days: number
    median_day_gap: number | null
  }
  pairs: { real: BacktestLeg; model: BacktestLeg; gap: number; same_exit: boolean; left_later: number }[]
  only_real: BacktestLeg[]
  only_model: BacktestLeg[]
  days: {
    day: string
    real_percent: number
    model_percent: number
    gap: number
    real_trades: number
    model_trades: number
  }[]
}

/** The turbo measured on WATA's closed trades. */
export interface BacktestCalibrationResult {
  trades: number
  used: number
  without_prices: number
  unreadable: number
  set_aside: number
  fitted: boolean
  reason: string | null
  sample: BacktestSample
  entry_price: number | null
  entry_price_low: number | null
  entry_price_high: number | null
  points_per_unit?: number
  points_per_unit_low?: number
  /** null: the trades do not bound it above. */
  points_per_unit_high?: number | null
  level?: number
  spread?: number
  /** Points of percent between this turbo and half of the real trades, and nine in ten of them. */
  median_error?: number
  ninth_error?: number
  measured_at?: string
}

export interface BacktestCalibration {
  status: {
    state: 'idle' | 'running' | 'done' | 'failed' | 'interrupted'
    trades?: number
    started_at?: string
    finished_at?: string
    error?: string | null
  }
  result: BacktestCalibrationResult | null
  turbo: BacktestDefaultTurbo
  lines: string[]
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
  /** The Dukascopy instrument downloaded; absent before downloads said it (the history backtests read). */
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
  symbol: string
  days: PriceCoverageDay[]
  ready: { count: number; first: string | null; last: string | null }
  fetch: PriceFetchStatus
  keep: PriceKeepStatus
}
