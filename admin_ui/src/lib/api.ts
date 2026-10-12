import type {
  AuditEntry,
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
