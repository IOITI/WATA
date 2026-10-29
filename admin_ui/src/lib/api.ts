import type {
  AgentArtefact,
  AgentMemoryChange,
  AgentMemoryDraft,
  AgentMemoryEntry,
  AgentMemoryState,
  AgentChoice,
  AgentContext,
  AgentSubject,
  AgentConversation,
  AgentConversationListed,
  AgentProposal,
  AgentState,
  AssetDetails,
  BacktestBaseline,
  BacktestBreakdowns,
  BacktestCoverage,
  BacktestDay,
  BacktestEdge,
  BacktestExecution,
  BacktestFate,
  BacktestOptions,
  BacktestPaper,
  BacktestReconciliation,
  BacktestRequestBody,
  BacktestRun,
  BacktestRunListed,
  BacktestSignal,
  BacktestStorage,
  BacktestSweep,
  BacktestTrade,
  BacktestTurboModel,
  AssetSeen,
  AssetStatistics,
  AuditEntry,
  CapitalConfig,
  CapitalProviderDetails,
  CapitalVolume,
  ConfigBackup,
  CredentialSaved,
  Credentials,
  ConfigCatalog,
  ConfigDocument,
  ConfigHistoryEntry,
  ConfigImpact,
  ConfigPreview,
  ConfigRestored,
  ConfigSaved,
  DataAssets,
  DataProviders,
  DukascopyProviderDetails,
  ExecutionTimings,
  Exits,
  Experiments,
  Health,
  HistoryInstrument,
  HistoryOffer,
  JournalEntry,
  JsonObject,
  LogServices,
  LogTail,
  McpState,
  McpToken,
  OrdersPage,
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
  PriceRangeBars,
  RecorderConfig,
  Report,
  ResearchFiles,
  ResearchLockbox,
  RestartPlan,
  SaxoProviderDetails,
  SaxoStatus,
  ServicesState,
  Session,
  Signal,
  SignalSent,
  TaComparison,
  TaEngine,
  TaHistory,
  TaMonitor,
  TaMonitorNow,
  TaRuleFields,
  TaRuleMode,
  TaRules,
  TaRuleSaved,
  TaScript,
  TaScriptSummary,
  TaSignal,
  TaVersion,
  LivePaper,
  PositionChartRefs,
  Watchlist,
  WebhookState,
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

/** The server has nothing of that name: a run, a script, an asset… named by the address. */
export function isNotFound(error: unknown): error is ApiError & { status: 404 } {
  return error instanceof ApiError && error.status === 404
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

/** An asset's address under /api/data/assets: its provider and key, each part encoded. */
function assetPath(ref: string): string {
  return ref.split('/').map(encodeURIComponent).join('/')
}

/** Where the live prices of an asset are streamed (server-sent events): read with an EventSource, not fetch. */
export function assetLiveUrl(ref: string): string {
  return `/api/data/assets/${assetPath(ref)}/live`
}

/** Where a script's current source is downloaded as a .pine file: a link, not fetch. */
export function taScriptSourceUrl(id: string): string {
  return `/api/ta/scripts/${encodeURIComponent(id)}/source.pine`
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
  /** What a position is drawn on: its recorded turbo, the index below, its day and the signal that opened it. */
  positionChart: (positionId: string) =>
    request<PositionChartRefs>('GET', `/api/positions/${encodeURIComponent(positionId)}/chart`),
  orders: (params: DateRangeParams = {}) =>
    request<OrdersPage>('GET', `/api/orders${query({ ...params, limit: 2000 })}`),
  performance: (params: DateRangeParams = {}) =>
    request<Performance>('GET', `/api/performance${query({ ...params })}`),
  exits: (params: DateRangeParams = {}) => request<Exits>('GET', `/api/exits${query({ ...params })}`),
  report: () => request<Report>('GET', '/api/report'),
  journal: (params: DateRangeParams = {}) =>
    request<{ items: JournalEntry[] }>('GET', `/api/journal${query({ ...params, limit: 500 })}`),
  watchlist: () => request<Watchlist>('GET', '/api/watchlist'),
  executionTimings: (days: number) => request<ExecutionTimings>('GET', `/api/execution-timing${query({ days })}`),

  // ── the Data pages: providers, and the assets whose prices WATA keeps ──
  dataProviders: () => request<DataProviders>('GET', '/api/data/providers'),
  saxoProvider: () => request<SaxoProviderDetails>('GET', '/api/data/providers/saxo'),
  dukascopyProvider: () => request<DukascopyProviderDetails>('GET', '/api/data/providers/dukascopy'),
  capitalProvider: () => request<CapitalProviderDetails>('GET', '/api/data/providers/capital'),
  capitalConfig: () => request<CapitalConfig>('GET', '/api/data/providers/capital/config'),
  saveCapitalConfig: (section: JsonObject, revision: string) =>
    request<ConfigSaved>('PUT', '/api/data/providers/capital/config', { body: { section, revision } }),
  recorderConfig: () => request<RecorderConfig>('GET', '/api/data/providers/saxo/config'),
  saveRecorderConfig: (section: JsonObject, revision: string) =>
    request<ConfigSaved>('PUT', '/api/data/providers/saxo/config', { body: { section, revision } }),
  /** What was recorded of each instrument of the period, and when Saxo left it without a quote. */
  priceAvailability: (period: PricePeriod) =>
    request<PriceAvailability>('GET', `/api/data/providers/saxo/availability${query({ ...period })}`),
  historyInstruments: () =>
    request<{ items: HistoryInstrument[]; offered: HistoryOffer[] }>('GET', '/api/data/providers/dukascopy/instruments'),
  addHistoryInstrument: (body: { symbol: string; label: string }) =>
    request<HistoryInstrument>('POST', '/api/data/providers/dukascopy/instruments', { body }),
  removeHistoryInstrument: (label: string) =>
    request<void>('DELETE', `/api/data/providers/dukascopy/instruments/${encodeURIComponent(label)}`),
  dataAssets: (seen: AssetSeen = 'all') => request<DataAssets>('GET', `/api/data/assets${query({ seen })}`),
  /** One asset; `period` chooses the index returned under a turbo (its last day by default). */
  asset: (ref: string, period: Partial<PricePeriod> = {}) =>
    request<AssetDetails>('GET', `/api/data/assets/${assetPath(ref)}${query({ ...period })}`),
  assetBars: (ref: string, period: PricePeriod, interval: number) =>
    request<PriceBars>('GET', `/api/data/assets/${assetPath(ref)}/bars${query({ ...period, interval })}`),
  /**
   * Range bars of one side: of `size` (a price), or of `ticks` steps of the instrument's
   * price (TradingView's R); without either the server chooses. `under` is the recorded
   * index shown under a turbo. `after` (epoch seconds) gives only the bars placed from it
   * on, built from the period's first day all the same; `newest` gives the bars of the
   * first day of the period from which they fit, rather than a refusal.
   */
  assetRangeBars: (
    ref: string,
    period: PricePeriod,
    options: {
      side: 'bid' | 'ask'
      size: number | null
      under: number | null
      ticks?: number | null
      after?: number | null
      newest?: boolean
    },
  ) =>
    request<PriceRangeBars>(
      'GET',
      `/api/data/assets/${assetPath(ref)}/range-bars${query({
        ...period,
        side: options.side,
        size: options.size,
        ticks: options.ticks,
        under: options.under,
        after: options.after,
        newest: options.newest ? 'true' : null,
      })}`,
    ),
  assetContext: (ref: string, period: PricePeriod) =>
    request<PriceContext>('GET', `/api/data/assets/${assetPath(ref)}/context${query({ ...period })}`),
  /** Capital.com's own volume of the minutes of a period, added up by `interval` (a minute or longer). */
  assetVolume: (ref: string, period: PricePeriod, interval: number) =>
    request<CapitalVolume>('GET', `/api/data/assets/${assetPath(ref)}/volume${query({ ...period, interval })}`),
  assetStatistics: (ref: string, period: PricePeriod) =>
    request<AssetStatistics>('GET', `/api/data/assets/${assetPath(ref)}/statistics${query({ ...period })}`),
  audit: () => request<{ items: AuditEntry[] }>('GET', '/api/audit?limit=500'),

  signals: () => request<{ items: Signal[]; webhook?: WebhookState | null }>('GET', '/api/signals'),
  health: () => request<Health>('GET', '/api/system/health'),
  services: () => request<ServicesState>('GET', '/api/system/services'),
  credentials: () => request<Credentials>('GET', '/api/credentials'),
  setCredential: (name: string, parts: Record<string, string>) =>
    request<CredentialSaved>('PUT', `/api/credentials/${encodeURIComponent(name)}`, { body: { parts } }),
  rotateCredential: (name: string) =>
    request<CredentialSaved>('POST', `/api/credentials/${encodeURIComponent(name)}/rotate`),
  removeCredential: (name: string) =>
    request<{ name: string; services: string[] }>('DELETE', `/api/credentials/${encodeURIComponent(name)}`),
  showWebhook: () => request<{ token: string; url: string | null; path: string }>('POST', '/api/credentials/webhook.token/show'),
  testTelegram: () => request<{ ok: boolean; detail: string }>('POST', '/api/credentials/telegram.bot/test'),
  /** The Claude API asked for its model with the key as stored: a read, no token spent. */
  testAnthropicKey: () => request<{ ok: boolean; detail: string }>('POST', '/api/credentials/anthropic.api/test'),
  restartServices: (services: string[], force = false) =>
    request<{ plan: RestartPlan }>('POST', '/api/system/services/restart', { body: { services, force } }),
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
  submitSaxoAuthCode: (code: string, state: string | null = null) =>
    request<void>('POST', '/api/system/saxo/auth-code', { body: { code, state } }),
  authorizeSaxo: (environment?: string) =>
    request<{ url: string }>('POST', '/api/system/saxo/authorize', { body: { environment: environment ?? null } }),

  // ── backtests on the recorded turbos ──
  backtestOptions: () => request<BacktestOptions>('GET', '/api/backtests/options'),
  backtestCoverage: (params: { from: string; to: string; indice: string; asset?: string; source?: 'pine' | 'journal' | 'ta_signals'; rule?: string }) =>
    request<BacktestCoverage>('GET', `/api/backtests/coverage${query(params)}`),
  backtests: () => request<{ items: BacktestRunListed[]; storage: BacktestStorage }>('GET', '/api/backtests'),
  backtest: (runId: string) => request<BacktestRun>('GET', `/api/backtests/${encodeURIComponent(runId)}`),
  createBacktest: (body: BacktestRequestBody) => request<BacktestRun>('POST', '/api/backtests', { body }),
  renameBacktest: (runId: string, body: { name?: string; tags?: string[] }) =>
    request<BacktestRun>('PATCH', `/api/backtests/${encodeURIComponent(runId)}`, { body }),
  cancelBacktest: (runId: string) => request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/cancel`),
  deleteBacktest: (runId: string) => request<void>('DELETE', `/api/backtests/${encodeURIComponent(runId)}`),
  removeBacktests: (ids: string[]) =>
    request<{ removed: string[]; kept: { id: string; reason: string }[] }>('POST', '/api/backtests/remove', { body: { ids } }),
  removeFormerBacktests: () => request<{ removed: number }>('DELETE', '/api/backtests/former'),
  researchFiles: () => request<ResearchFiles>('GET', '/api/backtests/research'),
  setResearchFiles: (body: { enabled?: boolean; keep_days?: number }) =>
    request<ResearchFiles>('PUT', '/api/backtests/research', { body }),
  backtestDays: (runId: string) => request<{ items: BacktestDay[] }>('GET', `/api/backtests/${encodeURIComponent(runId)}/days`),
  backtestSignals: (runId: string, params: { day?: string; fate?: BacktestFate } = {}) =>
    request<{ items: BacktestSignal[] }>('GET', `/api/backtests/${encodeURIComponent(runId)}/signals${query(params)}`),
  backtestTrades: (runId: string, params: { day?: string } = {}) =>
    request<{ items: BacktestTrade[] }>('GET', `/api/backtests/${encodeURIComponent(runId)}/trades${query(params)}`),
  backtestBreakdowns: (runId: string) =>
    request<BacktestBreakdowns>('GET', `/api/backtests/${encodeURIComponent(runId)}/breakdowns`),
  backtestBaseline: (runId: string) =>
    request<BacktestBaseline | Record<string, never>>('GET', `/api/backtests/${encodeURIComponent(runId)}/baseline`),
  backtestSweep: (runId: string) => request<BacktestSweep>('GET', `/api/backtests/${encodeURIComponent(runId)}/sweep`),
  runSweepCell: (runId: string, cell: number) =>
    request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/cells/${cell}/run`),
  /** How the asset moved after the run's entry signals; empty for a run made before it was measured. */
  backtestEdge: (runId: string) => request<BacktestEdge | Record<string, never>>('GET', `/api/backtests/${encodeURIComponent(runId)}/edge`),
  /** What WATA's own orders took and cost, the three months before today by default. */
  backtestExecution: () => request<BacktestExecution>('GET', '/api/backtests/execution'),
  /** A replay of the journal beside WATA's real positions; empty for any other run. */
  backtestReconciliation: (runId: string) =>
    request<BacktestReconciliation | Record<string, never>>('GET', `/api/backtests/${encodeURIComponent(runId)}/reconciliation`),
  /** Each shadow rule's days traded on paper, beside its reference run, and its report since it began. */
  backtestPaper: () => request<BacktestPaper>('GET', '/api/backtests/paper'),
  /** A rule's report (or the signals received's, owner "live") made now rather than after the next finished day. */
  askPaperReport: (owner: string) => request<BacktestPaper>('POST', `/api/backtests/paper/${encodeURIComponent(owner)}/report`),
  /** The signals WATA received traded on paper under the live settings, beside the real days and the live reference. */
  livePaper: () => request<LivePaper>('GET', '/api/backtests/paper/live'),
  /** A run made from a rule becomes the one its paper days are set against. */
  setBacktestReference: (runId: string) => request<BacktestPaper>('POST', `/api/backtests/${encodeURIComponent(runId)}/reference`),
  /** The measure of the turbos made up from the index, and whether one is being made. */
  turboModel: () => request<BacktestTurboModel>('GET', '/api/backtests/turbo-model'),
  measureTurbos: (period: { from: string; to: string }) => request<BacktestTurboModel>('POST', '/api/backtests/turbo-model', { body: period }),
  /** The run made again as a sweep, with each order filled later and at a worse price. */
  stressBacktest: (runId: string) => request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/stress`),
  modelCheckBacktest: (runId: string) => request<BacktestRun>('POST', `/api/backtests/${encodeURIComponent(runId)}/model-check`),

  // ── Dukascopy's history: what is there, its downloads, keeping it up to date ──
  // `label`: another Dukascopy instrument than the history kept up to date, which is meant when it is left out.
  historyCoverage: (period: Partial<PricePeriod> = {}, label?: string) =>
    request<PriceCoverage>('GET', `/api/data/providers/dukascopy/coverage${query({ ...period, label })}`),
  fetchHistory: (period: PricePeriod, label?: string) =>
    request<PriceFetchStatus>('POST', '/api/data/providers/dukascopy/fetch', { body: label ? { ...period, label } : period }),
  stopFetchingHistory: () => request<PriceFetchStatus>('POST', '/api/data/providers/dukascopy/fetch/stop'),
  keepHistory: (body: { enabled: boolean; days: number }) =>
    request<PriceKeepStatus>('PUT', '/api/data/providers/dukascopy/keep', { body }),

  // ── Technical Analysis: Pine scripts and the layouts of the charts ──
  taScripts: () => request<{ items: TaScriptSummary[] }>('GET', '/api/ta/scripts'),
  taScript: (id: string) => request<TaScript>('GET', `/api/ta/scripts/${encodeURIComponent(id)}`),
  createTaScript: (body: { name: string; source: string; description?: string }) =>
    request<TaScript>('POST', '/api/ta/scripts', { body }),
  /** A changed source makes a new version; `base_version` is the one the change was made on (409 if another came first). */
  saveTaScript: (id: string, body: { base_version: number; source?: string; name?: string; description?: string }) =>
    request<TaScript>('PUT', `/api/ta/scripts/${encodeURIComponent(id)}`, { body }),
  deleteTaScript: (id: string) => request<void>('DELETE', `/api/ta/scripts/${encodeURIComponent(id)}`),
  duplicateTaScript: (id: string, name: string) =>
    request<TaScript>('POST', `/api/ta/scripts/${encodeURIComponent(id)}/duplicate`, { body: { name } }),
  taScriptVersion: (id: string, number: number) =>
    request<TaVersion>('GET', `/api/ta/scripts/${encodeURIComponent(id)}/versions/${number}`),
  /** The library that declares library("<name>"), with its current source. */
  taLibrary: (name: string) => request<TaScript>('GET', `/api/ta/libraries/${encodeURIComponent(name)}`),
  taWorkspace: (key: string) => request<{ value: string | null }>('GET', `/api/ta/workspaces/${encodeURIComponent(key)}`),
  saveTaWorkspace: (key: string, value: string) =>
    request<void>('PUT', `/api/ta/workspaces/${encodeURIComponent(key)}`, { body: { value } }),
  // Signal rules: each change names the revision it was made on (409 if the rules changed meanwhile).
  taRules: () => request<TaRules>('GET', '/api/ta/rules'),
  createTaRule: (body: TaRuleFields & { revision: string; mode?: 'off' | 'shadow' }) =>
    request<TaRuleSaved>('POST', '/api/ta/rules', { body }),
  saveTaRule: (id: string, body: TaRuleFields & { revision: string; refresh_libraries?: boolean }) =>
    request<TaRuleSaved>('PUT', `/api/ta/rules/${encodeURIComponent(id)}`, { body }),
  /**
   * Live is an operator action: refused (403) while actions are disabled, and (409) while the rule is not ready
   * unless `force` goes live anyway, which the audit trail records with what was missing.
   */
  setTaRuleMode: (id: string, mode: TaRuleMode, revision: string, force = false) =>
    request<TaRuleSaved>('POST', `/api/ta/rules/${encodeURIComponent(id)}/mode`, {
      body: force ? { mode, revision, force } : { mode, revision },
    }),
  deleteTaRule: (id: string, revision: string) =>
    request<void>('DELETE', `/api/ta/rules/${encodeURIComponent(id)}${query({ revision })}`),
  /** How many bars the TA engine would run a rule of this asset, side, bars and days of history on. */
  taHistory: (params: { epic: string; side: 'bid' | 'ask'; timeframe?: string; ticks?: number; days?: number }) =>
    request<TaHistory>('GET', `/api/ta/history${query(params)}`),
  taSignals: (params: DateRangeParams & { rule?: string } = {}) =>
    request<{ items: TaSignal[] }>('GET', `/api/ta/signals${query({ ...params, limit: 500 })}`),
  taEngine: () => request<TaEngine>('GET', '/api/ta/engine'),
  /** The Engine monitor: the last minutes and their checks, and a period of what the engine measured. */
  taMonitorNow: () => request<TaMonitorNow>('GET', '/api/ta/monitor/now'),
  taMonitor: (params: { hours: number; rule?: string | null }) =>
    request<TaMonitor>('GET', `/api/ta/monitor${query({ hours: params.hours, rule: params.rule ?? undefined })}`),
  taComparison: (id: string, day: string, tolerance?: number) =>
    request<TaComparison>('GET', `/api/ta/rules/${encodeURIComponent(id)}/comparison${query({ day, tolerance })}`),

  // ── Claude's research through the MCP endpoint: watched, paused, its tokens revoked ──
  mcp: () => request<McpState>('GET', '/api/mcp'),
  /** An operator action, audited: the token opens the endpoint no more. */
  createMcpToken: (name: string, days: number) =>
    request<{ token: string; entry: McpToken }>('POST', '/api/mcp/tokens', { body: { name, days } }),
  revokeMcpToken: (id: string) =>
    request<{ revoked: McpToken[] }>('POST', `/api/mcp/tokens/${encodeURIComponent(id)}/revoke`),
  /** Paused, every call of Claude's is refused until it is resumed; audited. */
  pauseMcp: (paused: boolean) => request<McpState['paused']>('PUT', '/api/mcp/paused', { body: { paused } }),
  experiments: () => request<Experiments>('GET', '/api/research/experiments'),
  /** The operator moves the lock box or clears it (start null), as Claude cannot. */
  setLockbox: (period: { start: string | null; end: string | null }) =>
    request<{ lockbox: ResearchLockbox | null }>('PUT', '/api/research/lockbox', { body: period }),

  // ── The AI agent of the pages: its conversations, worked on by the admin whether a page follows them or not ──
  agent: () => request<AgentState>('GET', '/api/agent'),
  pauseAgent: (paused: boolean) =>
    request<{ pause: AgentState['pause'] }>('POST', paused ? '/api/agent/pause' : '/api/agent/resume'),
  agentConversations: (subject?: string) =>
    request<{ items: AgentConversationListed[] }>('GET', `/api/agent/conversations${query({ subject })}`),
  agentConversation: (id: string) =>
    request<AgentConversation>('GET', `/api/agent/conversations/${encodeURIComponent(id)}`),
  /** A new conversation, about a run or a script when given: its first request queued. */
  startAgentConversation: (
    prompt: string,
    subject?: { kind: AgentSubject['kind']; id: string; version?: number; day?: string; at?: string },
    choice?: AgentChoice,
  ) =>
    request<AgentConversationListed>('POST', '/api/agent/conversations', { body: { prompt, subject, ...choice } }),
  askAgent: (id: string, prompt: string, choice?: AgentChoice) =>
    request<AgentConversationListed>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/requests`, { body: { prompt, ...choice } }),
  /** What the next request would put in the model's context window: of a conversation, or of a new one. */
  agentContext: (id: string | null, model: string) =>
    request<AgentContext>('GET', id
      ? `/api/agent/conversations/${encodeURIComponent(id)}/context${query({ model })}`
      : `/api/agent/context${query({ model })}`),
  stopAgent: (id: string) => request<unknown>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/stop`),
  retryAgent: (id: string) => request<unknown>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/retry`),
  agentFeedback: (id: string, requestIndex: number, value: 'like' | 'dislike' | null) =>
    request<void>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/feedback`, {
      body: { request: requestIndex, value },
    }),
  renameAgentConversation: (id: string, title: string) =>
    request<AgentConversationListed>('PATCH', `/api/agent/conversations/${encodeURIComponent(id)}`, { body: { title } }),
  removeAgentConversation: (id: string) =>
    request<void>('DELETE', `/api/agent/conversations/${encodeURIComponent(id)}`),
  /** The proposal applied through the stores' checks: a new version of the script, or the rule (not live) changed. */
  settleAgentProposal: (id: string, proposal: string, apply: boolean) =>
    request<AgentProposal>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/proposals/${encodeURIComponent(proposal)}/${apply ? 'apply' : 'dismiss'}`),
  startAgentTask: (body: { goal: string; budget: { usd: number; backtests: number; hours: number }; model?: string; effort?: string }) =>
    request<AgentConversationListed>('POST', '/api/agent/tasks', { body }),
  stopAgentTask: (id: string) =>
    request<AgentConversationListed>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/task/stop`),
  /** A change of settings as a save of the Configuration page is previewed, with the file's revision it was read at. */
  previewAgentSettings: (id: string, proposal: string) =>
    request<ConfigPreview & { revision: string }>('POST', `/api/agent/conversations/${encodeURIComponent(id)}/proposals/${encodeURIComponent(proposal)}/preview`),
  saveAgentSettings: (id: string, proposal: string, revision: string) =>
    request<AgentProposal & { impact: ConfigImpact; restart_required: boolean }>(
      'POST', `/api/agent/conversations/${encodeURIComponent(id)}/proposals/${encodeURIComponent(proposal)}/apply`, { body: { revision } }),
  agentArtefacts: () => request<{ items: AgentArtefact[] }>('GET', '/api/agent/artefacts'),
  // Its memory: kept whether the agent is on or not, every change journaled and undoable.
  agentMemory: () => request<AgentMemoryState>('GET', '/api/agent/memory'),
  agentMemoryHistory: (name?: string) =>
    request<{ items: AgentMemoryChange[] }>('GET', `/api/agent/memory/history${query({ name })}`),
  createAgentMemory: (entry: AgentMemoryDraft) =>
    request<AgentMemoryEntry>('POST', '/api/agent/memory', { body: entry }),
  /** Changed, or renamed to entry.name, from the revision the page showed: refused (409) once it moved on. */
  changeAgentMemory: (name: string, entry: AgentMemoryDraft & { revision: number }) =>
    request<AgentMemoryEntry>('PUT', `/api/agent/memory/${encodeURIComponent(name)}`, { body: entry }),
  lockAgentMemory: (name: string, locked: boolean) =>
    request<AgentMemoryEntry>('POST', `/api/agent/memory/${encodeURIComponent(name)}/lock`, { body: { locked } }),
  forgetAgentMemory: (name: string, revision: number) =>
    request<void>('DELETE', `/api/agent/memory/${encodeURIComponent(name)}${query({ revision })}`),
  /** The entry as a line of the history left it, back. */
  restoreAgentMemory: (seq: number) => request<AgentMemoryEntry>('POST', '/api/agent/memory/restore', { body: { seq } }),
  pauseAgentMemory: (paused: boolean) =>
    request<{ pause: AgentMemoryState['pause'] }>('POST', paused ? '/api/agent/memory/pause' : '/api/agent/memory/resume'),

  config: () => request<ConfigDocument>('GET', '/api/config'),
  /** Every parameter of config.json, fixed for a version of WATA. */
  configCatalog: () => request<ConfigCatalog>('GET', '/api/config/catalog'),
  configHistory: () => request<{ items: ConfigHistoryEntry[] }>('GET', '/api/config/history'),
  /** Checked as a save would be; nothing is written. */
  validateConfig: (config: JsonObject) => request<ConfigPreview>('POST', '/api/config/validate', { body: { config } }),
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
