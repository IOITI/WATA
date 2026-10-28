// Shapes of the admin API (src/admin/routers/*.py).

import type { Check } from 'ioiti-ui'

import type { MenuSection } from '@/components/navigation'

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
  /** The services that run without a change saved since they started; null when the control service cannot say. */
  restart_services?: { id: string; name: string }[] | null
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
  /** The worst result reached, 0 when it never fell under its open: recorded since v0.48.0. */
  position_min_performance_percent?: number | null
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
    /** The timezone of these hours and of the close; null or absent: the trading one. */
    market_timezone?: string | null
    /** The day's breakers (0: off): losses in a row, points under the day's best. Absent before v0.48.0. */
    max_consecutive_losses?: number
    max_drawdown_from_day_best_percent?: number
    /** The account guard (0: off): its floor in euros, and its fall from the highest in percent. */
    pause_below_balance_eur?: number
    pause_after_drawdown_percent?: number
  }
  /** Today is a closed date, configured or a US holiday: the trader refuses every signal. */
  market_closed_today: boolean
  /** Today's US market day when it is not an ordinary one; null or absent otherwise. */
  market_day?: OverviewMarketDay | null
  cooldown: {
    last_loss_at: string | null
    ends_at: string | null
    active: boolean
  }
  /** Set while the operator has paused new entries. */
  entries_paused_since: string | null
  /** Set beside it while the pause is the account guard's own; null or absent otherwise. */
  entries_paused_by_account_guard?: string | null
  /** The day's figures the breakers are counted on, as the trader counts them; null or absent while they are off. */
  breakers?: OverviewBreakers | null
  /** No entry around US releases (the macro_news rule); null or absent while the rule is off. */
  news?: OverviewNews | null
  /** Saxo's login as the trader last said it; null or absent while the trader is not heard from. */
  saxo_login?: OverviewSaxoLogin | null
  open_positions: Position[]
  recent_days: DailyPerformance[]
}

export interface OverviewBreakers {
  /** The losing trades at the end of the day's closed ones. */
  losses_in_a_row: number
  /** The best the day's compounded result reached at a close (0 before the first), and where it is now. */
  day_best_percent: number
  day_percent: number
}

export interface OverviewMarketDay {
  kind: 'closed' | 'early_close'
  /** The holiday, or what the early close is for ("The day after Thanksgiving"). */
  name: string
  /** An early close: when the US market closes, when entries end, when what is held is sold. */
  closes_at?: string
  entries_end_at?: string
  sale_at?: string
}

export interface OverviewSaxoLogin {
  /** Since when Saxo refuses to renew WATA's login: the trader takes no new position meanwhile. */
  renewal_failing_since: string | null
  /** What Saxo answered to the last renewal refused. */
  error: string | null
  /** When the login lapses, after which nothing can be bought or sold. */
  lapses_at: string | null
}

export interface OverviewNews {
  /** The window in force, or the next one to start within a day. */
  window: { event: string; released_at: string; start: string; end: string } | null
  /** The calendar's last known day: past it, no window is kept. */
  known_until: string | null
  readable: boolean
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

/** Where an index takes its signals from (trade.config.signal_sources): TradingView's alerts unless set to the engine. */
export type SignalSource = 'tradingview' | 'engine'

/** The webhooks the web server answered without keeping them, for one index, since it started. */
/** What a position is drawn on (GET /api/positions/{id}/chart): its recorded turbo and the index below. */
export interface PositionChartRefs {
  /** The turbo as the price recorder kept it ("saxo/<id>"); null when it was not recorded. */
  turbo: string | null
  index: string | null
  /** The day it opened, in the trading timezone. */
  day: string | null
  signal: { action: string | null; signal_timestamp: string | null; received_timestamp?: string | null; decided_at?: string | null } | null
}

export interface IgnoredWebhooks {
  count: number
  first_at: string
  last_at: string
  last_action?: string | null
}

/** As the web server runs (it read the sources at its start); null from a web server before v0.48.0. */
export interface WebhookState {
  sources: Record<string, SignalSource>
  ignored: Record<string, IgnoredWebhooks>
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

export interface SaxoApp {
  set: boolean
  app_key: string | null
  fingerprint: string | null
}

export interface SaxoStatus {
  has_token: boolean
  saved_at: string | null
  access_token_expires_at: string | null
  refresh_token_expires_at: string | null
  access_token_expired: boolean
  refresh_token_expired: boolean
  /** Since when the services wait for a login; null while they renew it by themselves. */
  needs_authorization_since?: string | null
  /** What Saxo said when it last refused to renew, or a code. */
  last_refusal?: string | null
  environment?: 'live' | 'simulation'
  /** database: the row of PostgreSQL; none: no row yet (the services may still read the token file of before). */
  where?: 'database' | 'none'
  saved_by?: string | null
  /** Where Saxo sends the browser back: to be registered in the app. */
  redirect_url?: string | null
  /** Whether that is the admin's own callback, which takes the code by itself. */
  callback?: boolean
  apps?: Record<'live' | 'simulation', SaxoApp>
  /** Made on request (authorize), with a state of its own. */
  authorization_url: string | null
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
  /** Keys of the file the catalog does not know (a typo, a leftover): nothing reads them. */
  unknown: { path: string; nearest: string | null }[]
  editable: boolean
  restart_required: boolean
  modified_at: string
}

/** One key changed between two versions of config.json, by the catalog's path (values masked). */
export interface CatalogChange {
  path: string
  before: JsonValue | null
  after: JsonValue | null
  added: boolean
  removed: boolean
}

export type ParamRisk = 'trading' | 'service' | 'none'

/** What changes reach (src/admin/config_catalog): the services to start again, what else reads them. */
export interface ConfigImpact {
  services: string[]
  consumers: string[]
  risk: ParamRisk
  unknown: string[]
}

export interface ConfigWarning {
  path: string
  message: string
}

/** A configuration checked as a save would be, and what saving it would change. */
export interface ConfigPreview {
  valid: boolean
  changes: CatalogChange[]
  impact: ConfigImpact
  warnings: ConfigWarning[]
}

export interface ConfigSaved {
  revision: string
  backup: string
  restart_required: boolean
  changes?: CatalogChange[]
  impact?: ConfigImpact
}

/** A save written down in the journal beside config.json. */
export interface ConfigHistoryEntry {
  at: string
  action: string
  by: string | null
  revision_before: string
  revision_after: string
  changes: CatalogChange[]
}

export type ParamKind =
  | 'int' | 'number' | 'bool' | 'text' | 'enum' | 'time' | 'timezone' | 'dates' | 'map' | 'rows' | 'strings'
  | 'numbers' | 'rule' | 'json' | 'secret'

export type ParamAccess = 'editable' | 'server' | 'credential' | 'unused'

export interface CatalogColumn {
  key: string
  label: string
  kind: string
  help: string
  unit: string | null
  minimum: number | null
  maximum: number | null
  choices: string[]
  optional: boolean
}

/** A parameter of config.json, as the catalog of src/admin/config_catalog/ describes it. */
export interface CatalogParam {
  path: string
  group: string
  label: string
  kind: ParamKind
  help: string
  access: ParamAccess
  reason: string | null
  how: string | null
  readers: string[]
  consumers: string[]
  risk: ParamRisk
  effects: string[]
  related: string[]
  caveats: string[]
  also_needs: string[]
  unit: string | null
  minimum: number | null
  maximum: number | null
  choices: string[]
  optional: boolean
  default: JsonValue | null
  columns: CatalogColumn[]
  key_label: string | null
  value_label: string | null
  rule_default: JsonObject | null
  /** The block of its group's page it is shown under. */
  block: string
}

export interface CatalogService {
  id: string
  name: string
  container: string
  profile: string | null
}

export interface CatalogConsumer {
  id: string
  name: string
  /** Reads the file again each time (true), or what the admin read at its start (false). */
  live: boolean
}

export interface CatalogGroup {
  id: string
  title: string
  summary: string
  /** The section of the menu it is listed under (components/navigation.ts). */
  section: MenuSection
}

/** Parameters of a group that go together, under one heading of its page. */
export interface CatalogBlock {
  group: string
  id: string
  title: string
  summary: string
  paths: string[]
}

export interface ConfigCatalog {
  sections: MenuSection[]
  services: CatalogService[]
  consumers: CatalogConsumer[]
  groups: CatalogGroup[]
  blocks: CatalogBlock[]
  params: CatalogParam[]
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

/** The trades opened on a weekday (ISO: 1 is Monday) at an hour, for those that had some (src/admin/weekday_hour.py). */
export interface WeekdayHourStats extends GroupStats {
  weekday: number
  hour: number
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
  by_weekday_hour: WeekdayHourStats[]
  by_holding_time: (GroupStats & { bucket: string })[]
  /** The trades grouped as the trading style asks (src/admin/live_breakdowns.py); absent before v0.49.0. */
  breakdowns?: LiveBreakdowns
}

export type NamedGroupStats = GroupStats & { group: string }

export interface LiveBreakdowns {
  by_confidence: NamedGroupStats[]
  by_source: NamedGroupStats[]
  by_open_price: NamedGroupStats[]
  by_signal_delay: NamedGroupStats[]
  by_worst: NamedGroupStats[]
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
  changes?: CatalogChange[]
  impact?: ConfigImpact
}

/** What the server announces on /api/events. */
export interface LiveEvent {
  type: string
  [key: string]: unknown
}

// ── credentials sealed in PostgreSQL (src/admin/routers/credentials.py) ──

export interface CredentialPart {
  key: string
  label: string
  secret: boolean
  /** What is expected, said when a value is refused. */
  expected: string
}

export interface CredentialCheck {
  role: string
  checked_at: string | null
  ok: boolean
  detail: string | null
}

export interface Credential {
  name: string
  label: string
  /** The services it is sealed for, which apply a change at their next start. */
  readers: string[]
  parts: CredentialPart[]
  /** Made by WATA (a token): rotated, never typed. */
  generated: boolean
  old_place: string
  /** database; old place (read there until it has its row); missing; unknown (in another container's .env, maybe). */
  where: 'database' | 'old place' | 'missing' | 'unknown'
  public: Record<string, string>
  fingerprint: string | null
  set_at: string | null
  set_by: string | null
  checks: CredentialCheck[]
  /** How long the token before a rotation is still accepted. */
  grace_seconds: number | null
}

export interface Credentials {
  /** Why nothing can be sealed here (no keys), or null. */
  keys: string | null
  items: Credential[]
}

export interface CredentialSaved {
  name: string
  fingerprint: string | null
  services: string[]
  previous_accepted_seconds?: number | null
}

// ── WATA's containers and their restarts (src/admin/routers/services.py) ──

/** The saved changes a service does not run with: the keys it reads, or the file changed on the server. */
export interface ServicePending {
  paths: string[]
  outside: boolean
}

export interface ServiceLastRestart {
  requested_at: string
  done_at: string
  ok: boolean
  detail: string | null
}

export interface WataService {
  id: string
  name: string
  trading_path: boolean
  // What Docker says of its container, through the control service; null without it.
  container: string | null
  /** running, exited, restarting... or absent: not created, its compose profile off. */
  state: string | null
  running: boolean | null
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  oom_killed: boolean | null
  restart_count: number | null
  image: string | null
  restarting: boolean | null
  last_restart: ServiceLastRestart | null
  pending: ServicePending
  /** What holds its restart back now; null when nothing does. */
  refusal: string | null
  /** What a restart costs it. */
  cost: string
}

export type RestartStepState = 'waiting' | 'restarting' | 'starting' | 'ready' | 'failed' | 'held' | 'skipped'

export interface RestartStep {
  service: string
  state: RestartStepState
  detail: string | null
  started_at?: string
}

export interface RestartPlan {
  services: string[]
  forced: boolean
  by: string | null
  started_at: string
  steps: RestartStep[]
  done: boolean
  /** Each service's name, by its id. */
  names?: Record<string, string>
}

export interface ServicesState {
  /** Whether the control service answers: without it, nothing is restarted from here. */
  available: boolean
  error: string | null
  /** Whether each service's start is known; otherwise the changes are counted from the admin's own start. */
  starts_known: boolean
  actions_enabled: boolean
  services: WataService[]
  plan: RestartPlan | null
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
  /** Asked for the newest bars: the period's last day alone made more than are given, its newest are. */
  cut?: boolean
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
  /** The account a run starts with (1,000 € unless it says another), and the live one, null when not known. */
  capital: { value: number; balance: number | null }
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
  /** None for a day of the signals received without an entry signal ('no_entry'). */
  run_id: string | null
  state: BacktestState | 'no_entry'
  day_percent: number | null
  trades: number | null
  /** Outside the 5 % to 95 % of its reference run's days; none without a result or a reference. */
  outside: boolean | null
}

/** What a paper run is: one day, or the report of the days since it began. */
export type BacktestPaperRun = { rule: string; day: string; report?: undefined } | { rule: string; report: true; day?: undefined }

/** The newest finished report of a rule (or of the signals received): one run of its days since it began. */
export interface BacktestPaperReport {
  run_id: string
  from: string
  to: string
  made_at: string | null
  signals: number | null
  /** The versions of the rule's script that raised its signals. */
  versions: number[]
  headline: BacktestHeadline | null
}

/** A newer report than the one shown: waiting its turn, running, or one that did not finish. */
export interface BacktestPaperNextReport {
  run_id: string
  from: string
  to: string
  state: BacktestState
  error: string | null
  /** Its place in line, while it waits. */
  position: number | null
}

/** What paper trading keeps of a rule, or of the signals received. */
export interface BacktestPaperEntry {
  days: BacktestPaperDay[]
  reference: BacktestPaperReference | null
  /** Absent before v0.53.0. */
  report?: BacktestPaperReport | null
  next_report?: BacktestPaperNextReport | null
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
  rules: Record<string, BacktestPaperEntry>
  /** The signals WATA received, traded on paper under the live settings; absent before v0.49.0. */
  live?: BacktestPaperEntry | null
}

/** A finished day of the live settings: WATA's real result and the paper one, each against the live reference. */
export interface LivePaperDay {
  day: string
  real_percent: number | null
  real_outside: boolean | null
  paper_percent: number | null
  paper_outside: boolean | null
  /** The paper run's state; 'no_entry' when no entry signal came that day. */
  paper_state: BacktestState | 'no_entry' | null
  paper_run_id: string | null
  paper_trades: number | null
}

export interface LivePaper {
  days: LivePaperDay[]
  reference: BacktestPaperReference | null
  /** Said once on Telegram, and in health, when the last two days with a result were outside the range. */
  warning: string | null
  /** Their report since their first day on paper; absent before v0.53.0. */
  report?: BacktestPaperReport | null
  next_report?: BacktestPaperNextReport | null
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
  /** The signals a rule raised, as the TA engine journaled them: no script run again (paper trading). */
  | { kind: 'ta_signals'; rule: { id: string; name: string } }

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
    /** The versions of the rule's script that raised them; absent before v0.53.0, or when unknown. */
    | { kind: 'ta_signals'; signals: number; rule: { id: string; name: string }; versions?: number[] }
  chart: BacktestChart
  changes: Record<string, BacktestValue>
  baseline: { repetitions: number; seed: number } | null
  sweep: { axes: BacktestSweepAxis[]; held_out: number; walk_forward?: BacktestWindows | null; trials_before?: number } | null
  asked_at: string
  from_sweep?: { id: string; cell: number }
  /** A stress sweep: the run it made again with later and dearer fills. */
  stress_of?: string
  /** A model check: the run on the recorded turbos it made again on made-up ones. */
  model_check_of?: string
  turbos?: BacktestTurbos
}

/** The same signals on recorded and on made-up turbos, their trades paired by signal (compare_trades, src/backtest/turbo_model.py). */
export interface BacktestModelCheck {
  recorded_trades: number
  modelled_trades: number
  paired: number
  /** Made up minus recorded, in points of a trade's result (%). */
  mean_difference_percent: number | null
  median_absolute_difference_percent: number | null
  correlation: number | null
  same_exit_percent: number | null
  median_entry_price: { recorded: number | null; modelled: number | null }
  recorded_total_percent: number | null
  modelled_total_percent: number | null
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
    kind: 'pine' | 'journal' | 'ta_signals'
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
  /** A model check: the run on the recorded turbos it made again on made-up ones. */
  model_check_of?: string | null
  turbos?: BacktestTurbos
  /** A paper run: a day, or the report of the days since it began, of the signals a rule raised or of those WATA received (rule "live"). */
  paper?: BacktestPaperRun | null
  /** Who asked for it through an agent ("claude"), and for which experiment of its journal; absent for the operator's. */
  made_by?: string | null
  /** Through what: an MCP token's name, or "agent:<conversation>" for the AI agent of these pages. */
  made_via?: string | null
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
    /** How its orders were filled: the settings' values, or drawn from WATA's own orders of a period. */
    execution?:
      | { kind: 'fixed' }
      | { kind: 'measured'; from: string; to: string; orders: number; fills: { slippage_buy: number; slippage_sell: number } }
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
  /** A model check's: its trades paired by signal with the recorded run's. */
  model_check?: BacktestModelCheck | null
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
    Partial<Record<BacktestDayKind | 'session_part', BacktestGroupRow[]>> & {
      /** By the weekday and hour bought, those that had trades; absent from an admin older than v0.32.0. */
      weekday_hour?: WeekdayHourStats[]
    }
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
  /** "claude" for a version Claude wrote: by the MCP endpoint, or applied from the AI agent's proposal. */
  made_by?: string | null
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
  /** The last change of what it runs (script version, libraries, inputs, bars...): its week in shadow counts from it. */
  run_changed_at?: string
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
  /** When (UTC, HH:MM) the engine starts every rule again, on its days of bars before. */
  engine_restart_time: string
}

/**
 * How many bars the TA engine would run a rule's script on (src/admin/ta_history.py): those loaded at its
 * last restart, those it would run on now, and those of each of the week's engine days, restart to restart.
 */
export interface TaHistory {
  asset: { provider: 'capital'; key: string }
  side: 'bid' | 'ask'
  bars: TaRuleBars
  /** The days counted: the rule's own, or the engine's. */
  history_days: number
  restart_time: string
  restarted_at: string
  /** Where the rule's bars begin: its days before the last restart. */
  starts_at: string
  at_restart: number
  now: number
  /** The oldest first; each ends at a restart, the last at the last one. */
  engine_days: { start: string; end: string; bars: number }[]
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
  /**
   * Its last run's time; whether its new bars continue its run, or why each runs it whole over all its bars;
   * the checks of its continued run against a whole run that agreed.
   */
  figures: { milliseconds?: number | null; continued?: boolean; whole?: string | null; checks?: number } & Record<string, unknown>
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

/** The Engine monitor (src/admin/ta_monitor.py): what the TA engine measured of itself, kept `days`. All durations in ms. */
export interface TaMonitorSpread {
  p50: number | null
  p95: number | null
  max: number | null
}

export interface TaMonitorRecording {
  /** The days the engine applies; `configured_days` from its next start when they differ. */
  days: number
  configured_days: number
  /** The oldest tick kept: none before the engine measured anything. */
  since: string | null
  written_at: string | null
  waiting: number
  dropped: number
  error: string | null
}

export interface TaMonitorLimits {
  tick_ms: number
  run_timeout_ms: number
  max_signal_age_ms: number
  stale_feed_ms: number
  /** A check fails at this share of a limit. */
  share: number
  memory_share: number
  slow_ticks: number
  runner_memory_mb: number
  shadow_runner_memory_mb: number
  shadow_ms_per_tick: number
  shadow_day_ms: number | null
}

/** The engine's last tick (ta_engine_tick). */
export interface TaMonitorTick {
  tick_at: string
  ended_at: string
  milliseconds: number
  read_ms: number
  rows_read: number
  write_ms: number
  covered_until: string | null
  rules_on: number
  runs: number
  loads: number
  failed: number
  deferred_rules: string[]
  skipped: number
  restarted: boolean
  error: string | null
  runner_starts: number | null
  shadow_runner_starts: number | null
  live_rss_mb: number | null
  live_peak_mb: number | null
  shadow_rss_mb: number | null
  shadow_peak_mb: number | null
  engine_rss_mb: number | null
  memory_used_mb: number | null
  memory_limit_mb: number | null
  oom_kills: number | null
  feed_lag_ms: number | null
}

export interface TaMonitorWindow {
  ticks: number
  tick_p50_ms: number | null
  tick_max_ms: number | null
  /** The longest tick without a load, a check of a continued run or the daily restart. */
  steady_tick_max_ms: number | null
  slow_ticks: number
  feed_lag_max_ms: number | null
  memory_used_max_mb: number | null
  memory_limit_mb: number | null
  oom_kills: number
  deferred: number
  runs: number
  /** The continued runs checked against a whole run, and those a whole run gave otherwise. */
  checks: number
  checks_differed: number
  failed: number
  ended: number
  live_age_max_ms: number | null
  live_delay_max_ms: number | null
  live_signals: number
  live_not_sent: number
}

export interface TaMonitorNow {
  checked_at: string
  window_minutes: number
  recording: TaMonitorRecording
  limits: TaMonitorLimits
  last_tick: TaMonitorTick | null
  window: TaMonitorWindow
  /** The run of the live runner closest to its limit. */
  slowest_run: { rule_id: string; name: string | null; ms: number; limit_ms: number } | null
  checks: Check[]
  notes: string[]
}

export interface TaMonitorRule {
  rule_id: string
  /** None for a rule no longer in the file. */
  name: string | null
  mode: TaRuleMode | null
  made_by: string | null
  bars: TaRuleBars | null
  limit_ms: number
  runs: number
  loads: number
  /** The runs that continued the rule's run where it stopped, instead of running its script over all its bars. */
  continued: number
  /** Its continued runs checked against a whole run, those a whole run gave otherwise, the longest check, the last difference. */
  checks: number
  differed: number
  check_ms_max: number | null
  last_difference: string | null
  failed: number
  ended: number
  deferred: number
  asks: number
  run_ms: TaMonitorSpread
  /** PineTS's own time of a run, on average: the rest is the runner's reading and answering. */
  pinets_ms_avg: number | null
  load_ms_max: number | null
  bars_max: number | null
  delay_ms: TaMonitorSpread
  age_max_ms: number | null
  last_run_at: string | null
  last_failure: string | null
  signals: { raised: number; sent: number; not_sent: number; shadow: number }
}

/** A bar of time of the charts: `t` its start, epoch seconds. */
export interface TaMonitorTickPoint {
  t: number
  ticks: number
  tick_p50_ms: number | null
  tick_max_ms: number | null
  slow: number
  feed_lag_p50_ms: number | null
  feed_lag_max_ms: number | null
  live_rss_mb: number | null
  shadow_rss_mb: number | null
  engine_rss_mb: number | null
  memory_used_mb: number | null
  memory_limit_mb: number | null
  restarted: boolean
  deferred: number
}

export interface TaMonitorRunPoint {
  t: number
  runs: number
  loads: number
  failed: number
  run_p50_ms: number | null
  run_p95_ms: number | null
  run_max_ms: number | null
  delay_p50_ms: number | null
  delay_max_ms: number | null
}

export interface TaMonitorSignalPoint {
  t: number
  sent: number
  not_sent: number
  shadow: number
}

export interface TaMonitorMark {
  t: number
  kind: 'daily_restart' | 'runner_started' | 'engine_started'
  label: string
}

export interface TaMonitorStep {
  step: string
  label: string
  /** From the bar's close to the end: the sum of steps before it. */
  total: boolean
  signals: number
  p50: number | null
  p95: number | null
  max: number | null
}

export interface TaMonitorCount {
  key: string
  label: string
  count: number
}

export interface TaMonitorDelivery {
  rule: string | null
  sent: number
  steps: TaMonitorStep[]
  engine: TaMonitorCount[]
  refusals: (TaMonitorCount & { example: string | null })[]
  /** Shadow signals whose bar was older than a live one may be. */
  shadow_late: number
  trader: (TaMonitorCount & { reason: string | null })[]
}

export interface TaMonitorResult {
  rule_id: string
  positions: number
  open: number
  won: number
  lost: number
  profit_loss: number | null
  average_percent: number | null
}

export interface TaMonitor {
  hours: number
  periods: number[]
  bucket_seconds: number
  start: string
  end: string
  rule: string | null
  recording: TaMonitorRecording
  limits: TaMonitorLimits
  rules: TaMonitorRule[]
  series: {
    ticks: TaMonitorTickPoint[]
    runs: TaMonitorRunPoint[]
    signals: TaMonitorSignalPoint[]
    marks: TaMonitorMark[]
  }
  delivery: TaMonitorDelivery
  results: TaMonitorResult[]
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

/** Configurations tried outside the experiment's runs (a port screened on the Mac, by hand), counted in its trials. */
export interface ExperimentElsewhere {
  trials: number
  /** What was tried, where, and on which days. */
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
  elsewhere: ExperimentElsewhere[]
  /** Its runs' trials and those made elsewhere. */
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

// ── The AI agent of the pages (src/admin/agent/, /api/agent) ──

export interface AgentState {
  enabled: boolean
  model: string
  model_label?: string
  started?: boolean
  /** Why it cannot work: its tools could not be started. */
  broken?: string | null
  provider?: string
  effort?: string
  pause?: { since: string; by: string | null } | null
  month?: { month: string; spent_usd: number; budget_usd: number }
  limits?: { tool_calls: number; backtests: number; request_usd: number; running: number }
  under_way?: string[]
  conversations?: number
  /** The month of the prices the costs are estimated with. */
  prices_of?: string
  /** Whether a Claude API key is set; null when it cannot be told. */
  key?: boolean | null
  /** The models a request may choose, each as the Models API said it for the key, and the efforts. */
  models?: AgentModel[]
  efforts?: AgentEffort[]
  /** What a research task may be given at most. */
  tasks?: { usd: number; backtests: number; hours: number }
  /** The daily debrief: whether it is on, with which model, and the trading days it wrote. */
  debrief?: AgentDebriefState
}

export interface AgentDebriefState {
  enabled: boolean
  model: string
  effort: AgentEffort
  max_usd: number
  telegram: boolean
}

/** A research task: a goal the agent works on alone, request after request, within its budget. */
export interface AgentTask {
  goal?: string
  budget: { usd: number; backtests: number; hours: number }
  /** working; done by the agent's word; ended for another reason, said in ended_because. */
  state: 'working' | 'done' | 'ended'
  started_at?: string
  /** No request of it starts after this. */
  deadline: string
  spent_usd: number
  backtests: number
  requests?: number
  ended_at?: string | null
  ended_because?: string | null
  stopped_by?: string | null
  conclusion?: { conclusion: string; runs_kept: string[]; next_steps: string | null } | null
}

export type AgentEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'

export interface AgentModel {
  id: string
  label: string
  /** Its context window and its longest answer, in tokens. */
  window: number
  max_output: number
  /** Dollars per million tokens. */
  prices: { input: number; output: number; cache_read: number }
  efforts: AgentEffort[]
  /** Whether the key may use it; null until the Models API answered. */
  available: boolean | null
  /** The models whose notes it reads. */
  reads: string[]
}

/** A model and an effort, as a request is asked with them. */
export interface AgentChoice {
  model: string
  effort: AgentEffort
}

/** What the next request of a conversation would put in the model's context window (GET …/context). */
export interface AgentContext {
  model: string
  label: string
  window: number
  /** What an answer may take on top. */
  answer_room: number
  /** Counted by the API, estimated from the last answer, or not known. */
  how: 'counted' | 'estimated' | null
  total: number | null
  parts: { id: AgentContextPart; tokens: number; count?: number; estimated?: boolean }[]
  /** Tokens of old tool results context editing leaves out. */
  cleared: number | null
  clearing: { from_tokens: number; keep_tool_uses: number }
  last: AgentLastCall | null
  /** The models whose notes in the conversation this one cannot read. */
  unread: string[]
}

export type AgentContextPart = 'system' | 'tools' | 'asked' | 'thinking' | 'calls' | 'results' | 'answers' | 'conversation'

export interface AgentLastCall {
  at: string | null
  model: string | null
  usd: number | null
  prompt: number
  uncached: number
  cache_read: number
  cache_write: number
  output: number
  thinking: number | null
  cleared_tokens: number | null
  cleared_tool_uses: number | null
  dropped_notes: Record<string, number> | null
}

export interface AgentSubject {
  kind: 'run' | 'script' | 'position' | 'signal' | 'day' | 'incident'
  id: string
  label: string
  version?: number | null
  /** A signal's day (YYYY-MM-DD). */
  day?: string | null
  /** An incident's moment (YYYY-MM-DD HH:MM, UTC). */
  at?: string | null
}

/** What the agent is doing with a conversation: a request queued, worked on, waiting for a run; or nothing. */
export type AgentStatus = 'queued' | 'running' | 'waiting' | 'idle' | 'failed'

/** How a request ended, or where it stands. */
export type AgentRequestStatus =
  | 'queued' | 'running' | 'waiting' | 'done' | 'stopped' | 'failed' | 'declined' | 'limit' | 'cut'

export interface AgentConversationListed {
  id: string
  title: string
  subject: AgentSubject | null
  status: AgentStatus
  model: string | null
  created_at: string
  updated_at: string
  requests: number | null
  cost_usd: number | null
  outcome: AgentRequestStatus | null
  error: string | null
  /** Who asked it: an operator's address, or "debrief" for a daily debrief. */
  by?: string | null
  task?: AgentTask | null
}

export interface AgentStep {
  kind: 'note' | 'tool'
  id: string
  at?: string
  /** A note: what the model said between its tool calls. */
  text?: string
  tool?: string
  input?: unknown
  started_at?: string
  ended_at?: string | null
  state?: 'running' | 'done' | 'error' | 'cancelled'
  /** The tool's answer, cut for the pages. */
  output?: string | null
  /** A wait's last word: each run's place in line or progress. */
  progress?: string | null
}

export interface AgentRequest {
  index: number
  prompt: string
  shared: AgentSubject | null
  asked_at: string | null
  started_at: string | null
  ended_at: string | null
  status: AgentRequestStatus
  detail: string | null
  steps: AgentStep[]
  answer: { text: string; model: string | null; stop_reason: string | null } | null
  proposals: string[]
  runs: string[]
  cost_usd: number
  feedback: 'like' | 'dislike' | null
  /** The model and effort it was asked with (none for a request asked before they could be chosen). */
  model?: string | null
  effort?: AgentEffort | null
  /** Who asked it: the operator's address, "task" for a research task's next request, "debrief" for a debrief. */
  by?: string | null
}

export interface AgentInputChange {
  id: string
  title: string | null
  before: boolean | number | string | null
  after: boolean | number | string
}

/** A setting of config.json a change of settings proposes, as a backtest names it. */
export interface AgentSettingChange {
  setting: string
  label: string
  unit: string
  group: string
  /** Its place in config.json, as the catalog names it. */
  path: string
  before: number | boolean | string
  after: number | boolean | string
}

/** A finished run a change of settings rests on. */
export interface AgentProposalRun {
  id: string
  name: string | null
  kind?: string | null
  from?: string | null
  to?: string | null
  changes?: Record<string, unknown>
  headline?: BacktestHeadline | null
}

export interface AgentProposal {
  id: string
  kind: 'script' | 'rule' | 'settings' | 'new_script'
  request: number
  created_at: string
  status: 'proposed' | 'applied' | 'dismissed'
  title: string
  rationale: string
  /** A script's: the operator's script and its version the change was made on, Claude's that holds it. */
  script?: { id: string; name: string; base_version: number }
  draft?: { id: string; name: string; version: number; kind?: string }
  lines?: { added: number; removed: number }
  /** A new script's: its name and description once created. */
  new_script?: { name: string; description: string }
  /** A change of settings: each setting, and the runs it rests on; ``same``, those already at the value. */
  settings?: AgentSettingChange[]
  runs?: AgentProposalRun[]
  same?: string[]
  /** A rule's: the rule as it was, and what changes. */
  rule?: { id: string; name: string; mode: string; script_id: string; script_version: number }
  changes?: {
    script_version?: number
    after_proposal?: string
    inputs?: AgentInputChange[]
    history_days?: { before: number | null; after: number | null }
    libraries?: { name: string; before: number | null; after: number }[]
  }
  result?: {
    script_id?: string
    version?: number
    name?: string
    rule_id?: string
    script_version?: number
    libraries?: { name: string; before: number | null; after: number }[]
    /** A change of settings saved: the file's revision, the backup of the one it replaced, the services to restart. */
    revision?: string
    backup?: string
    services?: string[]
  } | null
  settled_at?: string
  settled_by?: string | null
}

export interface AgentPending {
  request: number
  /** The answer's text so far, by block. */
  text: Record<string, string>
  /** The model's note so far, by block. */
  note: Record<string, string>
  /** A tool call being written: its input's length so far. */
  writing: { tool: string; chars: number } | null
}

export interface AgentConversation {
  id: string
  title: string
  subject: AgentSubject | null
  status: AgentStatus
  /** The model and effort it goes on with: its last request's. */
  model: string | null
  effort?: AgentEffort | null
  created_at: string
  updated_at: string
  usage: Record<string, number> | null
  cost_usd: number | null
  error: string | null
  /** Its size, in tokens, as its last answer left it. */
  context?: { tokens: number; model: string | null; at: string } | null
  requests: AgentRequest[]
  proposals: AgentProposal[]
  pending: AgentPending | null
  /** A research task's budget, progress and end. */
  task?: AgentTask | null
}

export interface AgentArtefact {
  at: string
  kind: 'run' | 'script' | 'rule' | 'experiment' | 'proposal' | 'applied' | 'dismissed'
  conversation: string
  /** The conversation's title. */
  title_of: string | null
  id: string
  tool?: string
  name?: string
  version?: number
  /** One of Claude's shadow rules: its mode when saved. */
  mode?: string
  proposal?: AgentProposal['kind']
  title?: string
  result?: AgentProposal['result']
}

