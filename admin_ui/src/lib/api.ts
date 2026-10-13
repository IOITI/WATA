import type {
  AuditEntry,
  BacktestBreakdowns,
  BacktestCalibration,
  BacktestComparison,
  BacktestDay,
  BacktestDayChart,
  BacktestOptions,
  BacktestRefusal,
  BacktestRequest,
  BacktestRun,
  BacktestRunListed,
  BacktestSignalsRequest,
  BacktestStorage,
  BacktestSweepRequest,
  BacktestTrade,
  BacktestTradePath,
  ConfigBackup,
  ConfigDocument,
  ConfigRestored,
  ConfigSaved,
  ExecutionTimings,
  Exits,
  Health,
  JournalEntry,
  JsonObject,
  LogServices,
  LogTail,
  Order,
  Overview,
  Page,
  Performance,
  PositionDetail,
  Position,
  PriceAvailability,
  PriceBars,
  PriceContext,
  PriceCoverage,
  PriceFetchStatus,
  PriceKeepStatus,
  PriceInstruments,
  PriceRangeBars,
  Report,
  SaxoStatus,
  Session,
  Signal,
  SignalSent,
  Watchlist,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly retryAfter: number | null

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

type UnauthorizedListener = () => void
let onUnauthorized: UnauthorizedListener | null = null

/** Called when an authenticated request comes back 401: the session ended on the server. */
export function setUnauthorizedListener(listener: UnauthorizedListener | null) {
  onUnauthorized = listener
}

/** FastAPI answers `{detail: "..."}`, or a list of field errors for a malformed body. */
export function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item && typeof item === 'object' && 'msg' in item ? String(item.msg) : null))
        .filter(Boolean)
      if (messages.length > 0) return messages.join('; ')
    }
  }
  return fallback
}

interface RequestOptions {
  body?: unknown
  /** The login call answers 401 for a wrong password, which is not an expired session. */
  expectUnauthorized?: boolean
}

async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const hasBody = options.body !== undefined
  let response: Response
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: hasBody ? { 'Content-Type': 'application/json' } : undefined,
      body: hasBody ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'The admin server cannot be reached')
  }

  if (response.ok) {
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Not JSON (a proxy error page): the status is all there is.
  }
  if (response.status === 401 && !options.expectUnauthorized) onUnauthorized?.()

  const retryAfter = Number(response.headers.get('Retry-After'))
  throw new ApiError(
    response.status,
    errorMessage(body, `Request failed (HTTP ${response.status})`),
    Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
  )
}

function query(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export interface DateRangeParams {
  from?: string | null
  to?: string | null
}

export interface PricePeriod {
  from: string
  to: string
}

export const api = {
  session: () => request<Session>('GET', '/api/session', { expectUnauthorized: true }),
  /** Public: whether the login needs a one-time code as well as the password. */
  loginMethods: () => request<{ totp: boolean }>('GET', '/api/healthz'),
  login: (password: string, code?: string) =>
    request<void>('POST', '/api/session', {
      body: code ? { password, code } : { password },
      expectUnauthorized: true,
    }),
  logout: () => request<void>('DELETE', '/api/session'),

  overview: () => request<Overview>('GET', '/api/overview'),
  positions: (params: DateRangeParams & { status?: 'open' | 'closed' } = {}) =>
    request<Page<Position>>('GET', `/api/positions${query({ ...params, limit: 2000 })}`),
  position: (positionId: string) =>
    request<PositionDetail>('GET', `/api/positions/${encodeURIComponent(positionId)}`),
  orders: (params: DateRangeParams = {}) =>
    request<Page<Order>>('GET', `/api/orders${query({ ...params, limit: 2000 })}`),
  performance: (params: DateRangeParams = {}) =>
    request<Performance>('GET', `/api/performance${query({ ...params })}`),
  exits: (params: DateRangeParams = {}) => request<Exits>('GET', `/api/exits${query({ ...params })}`),
  report: () => request<Report>('GET', '/api/report'),
  journal: (params: DateRangeParams = {}) =>
    request<{ items: JournalEntry[] }>('GET', `/api/journal${query({ ...params, limit: 500 })}`),
  watchlist: () => request<Watchlist>('GET', '/api/watchlist'),
  executionTimings: (days: number) => request<ExecutionTimings>('GET', `/api/execution-timing${query({ days })}`),
  priceInstruments: (period: PricePeriod) =>
    request<PriceInstruments>('GET', `/api/prices/instruments${query({ ...period })}`),
  priceBars: (instrumentId: number, period: PricePeriod, interval: number) =>
    request<PriceBars>('GET', `/api/prices/bars${query({ instrument_id: instrumentId, ...period, interval })}`),
  /** Range bars of one side. Without a size the server chooses one; `under` is the index shown under a turbo. */
  priceRangeBars: (
    instrumentId: number,
    period: PricePeriod,
    options: { side: 'bid' | 'ask'; size: number | null; under: number | null },
  ) =>
    request<PriceRangeBars>(
      'GET',
      `/api/prices/range-bars${query({ instrument_id: instrumentId, ...period, side: options.side, size: options.size, under: options.under })}`,
    ),
  /** What was recorded of each instrument of the period, and when Saxo left it without a quote. */
  priceAvailability: (period: PricePeriod) =>
    request<PriceAvailability>('GET', `/api/prices/availability${query({ ...period })}`),
  priceContext: (instrumentId: number, period: PricePeriod) =>
    request<PriceContext>('GET', `/api/prices/context${query({ instrument_id: instrumentId, ...period })}`),
  audit: () => request<{ items: AuditEntry[] }>('GET', '/api/audit?limit=500'),

  signals: () => request<{ items: Signal[] }>('GET', '/api/signals'),
  health: () => request<Health>('GET', '/api/system/health'),
  saxo: () => request<SaxoStatus>('GET', '/api/system/saxo'),
  logServices: () => request<LogServices>('GET', '/api/logs'),
  logTail: (service: string, params: { lines: number; level?: string | null; q?: string | null }) =>
    request<LogTail>('GET', `/api/logs/${encodeURIComponent(service)}${query(params)}`),

  sendSignal: (body: { action: string; indice?: string; confidence?: number }) =>
    request<SignalSent>('POST', '/api/actions/signal', { body }),
  requestDailyStats: () => request<{ status: string }>('POST', '/api/actions/daily-stats'),
  pauseEntries: () => request<{ entries_paused_since: string | null }>('POST', '/api/actions/pause-entries'),
  resumeEntries: () => request<{ entries_paused_since: string | null }>('POST', '/api/actions/resume-entries'),
  closePosition: (positionId: string) =>
    request<{ status: string; position_id: string }>('POST', '/api/actions/close-position', {
      body: { position_id: positionId },
    }),
  submitSaxoAuthCode: (code: string) =>
    request<void>('POST', '/api/system/saxo/auth-code', { body: { code } }),

  backtestOptions: () => request<BacktestOptions>('GET', '/api/backtests/options'),
  backtests: () => request<{ items: BacktestRunListed[] }>('GET', '/api/backtests'),
  backtest: (runId: string) => request<BacktestRun>('GET', `/api/backtests/${encodeURIComponent(runId)}`),
  createBacktest: (body: BacktestRequest) => request<BacktestRun>('POST', '/api/backtests', { body }),
  cancelBacktest: (runId: string) =>
    request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/cancel`),
  deleteBacktest: (runId: string) => request<void>('DELETE', `/api/backtests/${encodeURIComponent(runId)}`),
  backtestTrades: (runId: string, leverage: number) =>
    request<{ items: BacktestTrade[] }>('GET', `/api/backtests/${encodeURIComponent(runId)}/trades${query({ leverage })}`),
  backtestDays: (runId: string, leverage: number) =>
    request<{ items: BacktestDay[] }>('GET', `/api/backtests/${encodeURIComponent(runId)}/days${query({ leverage })}`),
  backtestRefusals: (runId: string, leverage: number) =>
    request<{ items: BacktestRefusal[] }>(
      'GET',
      `/api/backtests/${encodeURIComponent(runId)}/refusals${query({ leverage })}`,
    ),
  backtestDayChart: (runId: string, day: string, leverage: number) =>
    request<BacktestDayChart>(
      'GET',
      `/api/backtests/${encodeURIComponent(runId)}/days/${encodeURIComponent(day)}/chart${query({ leverage })}`,
    ),
  priceCoverage: (period: Partial<PricePeriod> = {}) =>
    request<PriceCoverage>('GET', `/api/backtests/prices${query({ ...period })}`),
  fetchPrices: (period: PricePeriod) => request<PriceFetchStatus>('POST', '/api/backtests/prices/fetch', { body: period }),
  stopFetchingPrices: () => request<PriceFetchStatus>('POST', '/api/backtests/prices/fetch/stop'),
  keepPrices: (body: { enabled: boolean; days: number }) =>
    request<PriceKeepStatus>('PUT', '/api/backtests/prices/keep', { body }),
  backtestCalibration: () => request<BacktestCalibration>('GET', '/api/backtests/calibration'),
  measureTurbo: () => request<BacktestCalibration>('POST', '/api/backtests/calibration'),
  replaySignals: (body: BacktestSignalsRequest) => request<BacktestRun>('POST', '/api/backtests/signals', { body }),
  createSweep: (body: BacktestSweepRequest) => request<BacktestRun>('POST', '/api/backtests/sweeps', { body }),
  runSweepCell: (runId: string, cell: number) =>
    request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/cells/${cell}/run`),
  renameBacktest: (runId: string, body: { note?: string; tags?: string[] }) =>
    request<BacktestRun>('PATCH', `/api/backtests/${encodeURIComponent(runId)}`, { body }),
  removeBacktests: (ids: string[]) =>
    request<{ removed: string[]; kept: { id: string; reason: string }[] }>('POST', '/api/backtests/remove', { body: { ids } }),
  backtestStorage: () => request<BacktestStorage>('GET', '/api/backtests/storage'),
  backtestBreakdowns: (runId: string, leverage: number) =>
    request<BacktestBreakdowns>('GET', `/api/backtests/${encodeURIComponent(runId)}/breakdowns${query({ leverage })}`),
  backtestTradePath: (runId: string, leverage: number, entry: string) =>
    request<BacktestTradePath>(
      'GET',
      `/api/backtests/${encodeURIComponent(runId)}/trades/path${query({ leverage, entry })}`,
    ),
  backtestComparison: (runId: string, leverage: number) =>
    request<BacktestComparison>('GET', `/api/backtests/${encodeURIComponent(runId)}/comparison${query({ leverage })}`),

  config: () => request<ConfigDocument>('GET', '/api/config'),
  validateConfig: (config: JsonObject) =>
    request<{ valid: boolean }>('POST', '/api/config/validate', { body: { config } }),
  saveConfig: (config: JsonObject, revision: string) =>
    request<ConfigSaved>('PUT', '/api/config', { body: { config, revision } }),
  configBackups: () => request<{ items: ConfigBackup[] }>('GET', '/api/config/backups'),
  configBackup: (name: string) =>
    request<{ name: string; config: JsonObject }>('GET', `/api/config/backups/${encodeURIComponent(name)}`),
  restoreConfigBackup: (name: string, revision: string) =>
    request<ConfigRestored>('POST', `/api/config/backups/${encodeURIComponent(name)}/restore`, {
      body: { revision },
    }),
}
