import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type {
  BacktestDayIndicator, BacktestPaper, LiveCheck, LiveCheckDay, LiveCheckSegment, RuleCheckSummary, RuleDayIndicator, Session, TaEngine, TaRule,
  TaRuleHistory, TaScriptSummary, TaSignal,
} from '@/lib/types'
import { resetVela, velas } from '@/test/fakeVela'
import { WithTrail } from '@/test/WithTrail'

import { LiveCheckDayPage } from './LiveCheckDayPage'
import { firstDay, openingDay, steps } from './rulePageModel'
import { SignalRulePage } from './SignalRulePage'

vi.mock('@luxalgo/vela', async () => (await import('@/test/fakeVela')).fakeVelaCore)
vi.mock('@luxalgo/vela-pinets', async () => (await import('@/test/fakeVela')).fakeVelaPinets)

const SESSION: Session = {
  authenticated: true,
  version: '0.73.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const SOURCE = `//@version=6
indicator("Cross")
fast = input.int(9, "Fast", minval = 1)
plot(ta.ema(close, fast), "Fast EMA")
alertcondition(ta.crossover(close, ta.ema(close, fast)), title = "Up", message = '{"action": "long"}')
`

const CROSS: TaScriptSummary = {
  id: 'cccccccccccc', name: 'Cross', kind: 'indicator', description: '', pine_version: 6, library_name: null, imports: [], version: 1,
  versions: [{ number: 1, saved_at: '2026-10-19T10:00:00+00:00', size: 300, sha256: 'x' }],
  created_at: '2026-10-19T10:00:00+00:00', updated_at: '2026-10-19T10:00:00+00:00',
}

const ID = 'aaaaaaaaaaaa'

function rule(changes: Partial<TaRule> = {}): TaRule {
  return {
    id: ID, name: 'Cross on US100', asset: { provider: 'capital', key: 'US100' }, side: 'bid', bars: { type: 'time', timeframe: '1' },
    script: { id: CROSS.id, version: 1, sha256: 'x' }, libraries: [], inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 20 }],
    alerts: [{ title: 'Up', action: 'long' }], alert_calls: 'ignore', confidence_plot: null, indice: 'us100', mode: 'shadow',
    created_at: '2026-10-19T10:00:00+00:00', updated_at: '2026-10-21T10:00:00+00:00', mode_changed_at: '2026-10-19T10:00:00+00:00',
    run_changed_at: '2026-10-21T10:00:00+00:00', made_by: 'claude', script_name: 'Cross', latest: { script: 1, libraries: {} },
    ...changes,
  }
}

function signal(changes: Partial<TaSignal> = {}): TaSignal {
  return {
    id: 7, rule_id: ID, rule_name: 'Cross on US100', script_id: CROSS.id, script_version: 1, asset: 'capital/US100', side: 'bid', bars: '1',
    bar_time: '2026-10-22T08:31:00Z', bar_closed_at: '2026-10-22T08:32:00Z', alert_key: 'alertcondition:Up', alert_title: 'Up',
    alert_message: '{"action": "long"}', action: 'long', indice: 'us100', confidence: null, mode: 'shadow', raised_at: '2026-10-22T08:32:01Z',
    outcome: 'shadow', sent_at: null, http_status: null, signal_uuid: null, detail: null, trader: null, ...changes,
  }
}

const ENGINE: TaEngine = {
  enabled: true, status: 'ok', detail: '1 rule(s) on',
  engine: { updated_at: '2026-10-22T09:40:00Z', started_at: '2026-10-22T06:00:00Z', version: '0.73.0', state: 'running', detail: '1 rule(s) on', figures: {} },
  rules: {
    [ID]: {
      updated_at: '2026-10-22T09:40:00Z', state: 'running', detail: null, mode: 'shadow', script_version: 1, bars: 1380,
      last_bar_time: '2026-10-22T09:39:00Z', last_alert_at: '2026-10-22T08:32:01Z', figures: { milliseconds: 4, continued: true },
    },
  },
}

const HISTORY: TaRuleHistory = {
  rule_id: ID, mode: 'shadow', mode_since: '2026-10-19T10:00:00+00:00',
  events: [
    { at: '2026-10-21T10:00:00+00:00', kind: 'changed', live: false, also: [],
      changes: [{ what: 'input', id: 'in_0', name: 'Fast', before: 12, after: 20 }] },
    { at: '2026-10-19T10:00:00+00:00', kind: 'made', by: 'claude', runs: {
      script: { id: CROSS.id, name: 'Cross', version: 1 }, libraries: [], asset: { provider: 'capital', key: 'US100' }, side: 'bid',
      bars: { type: 'time', timeframe: '1' }, history_days: null, inputs: 1 } },
  ],
}

/** The day's minute bars as the engine ran the script on them: [open ms, open, high, low, close, volume, close ms]. */
function dayBars(): BacktestDayIndicator['bars'] {
  return Array.from({ length: 90 }, (_, index) => {
    const open = Date.parse('2026-10-22T08:00:00Z') + index * 60_000
    const price = 24850 + (index % 7) * 0.5
    return [open, price, price + 1, price - 1, price + 0.5, 3, open + 60_000]
  })
}

function dayOf(day: string): RuleDayIndicator {
  return {
    kind: 'rule', day, script: { id: CROSS.id, version: 1, name: 'Cross', source: SOURCE }, libraries: [],
    inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 20 }], inputs_version: 1,
    chart: { asset: { provider: 'capital', key: 'US100' }, side: 'bid', bars: { type: 'time', timeframe: '1' } },
    ticker: 'CAPITAL_US100', timeframe: '1', decimals: 1,
    window: { begin: '2026-10-21T21:15:00Z', end: '2026-10-22T21:15:00Z', anchor: '2026-10-20T21:15:00Z' },
    history_days: 1, restart_time: '21:15', bars: dayBars(), exact: true, notes: [],
    signals: [signal(), signal({ id: 8, action: 'short', bar_time: '2026-10-22T09:00:00Z', bar_closed_at: '2026-10-22T09:01:00Z', alert_title: 'Down' })],
  }
}

// The opening day and the one before it, as the page works them out today.
const opening = openingDay(rule(), Date.now(), 'Europe/Paris')!
const before = steps(opening, firstDay(rule(), 'Europe/Paris'), opening).before!

const TO_PAPER: LiveCheckSegment = {
  from: 'backtest', to: 'paper', days: 3, start: { trades: 4, sum: 12.1 }, end: { trades: 4, sum: 11.6 },
  buckets: [{ id: 'same_signal', label: 'Same signal, other fills', points: -0.5, trades: 4 }],
}
const TO_LIVE: LiveCheckSegment = {
  from: 'paper', to: 'live', days: 3, start: { trades: 4, sum: 11.6 }, end: { trades: 3, sum: 4.1 },
  buckets: [
    { id: 'not_traded_live', label: 'Not traded live', points: -3.04, trades: 1 },
    { id: 'exit_fill', label: 'Exit fill', points: -4.46, trades: 3 },
  ],
}
const FIGURES = { trades: 1, sum: 3.04, compounded: 3.04, entry_signals: 1, unkeyed: 0 }
const CALIBRATED_RUN = '20261021-213000-abcdef'

function liveCheck(changes: Partial<LiveCheck> = {}): LiveCheck {
  return {
    rule: { id: ID, name: 'Cross on US100', mode: 'live', live_since: '2026-10-21T08:00:00Z', bars: { type: 'time', timeframe: '1' } },
    link: { configuration: '2bb6f8514b16', set_at: '2026-10-21T08:00:00Z', found: true, nightly: { on: false, from: null } },
    strategy: { same: true, checks: [{ id: 'script', state: 'pass', detail: 'The same source' }] },
    template: { id: '20261119-050828-c18c49', name: 'v4: room to the session extreme', from: '2026-10-15', to: '2026-10-21', headline: null },
    calibrated: { run: CALIBRATED_RUN, template: '20261119-050828-c18c49', values: { capital: 21.82 }, asked_at: '2026-10-21T21:30:00Z',
      error: null, settled: true, state: 'done' },
    period: { from: '2026-10-19', to: '2026-10-21' }, database: true, tolerance_seconds: 120,
    days: [{
      day: '2026-10-21', present: ['backtest', 'paper', 'live'], agreement: { same_bar: 1, other_bar: 0, not_replayed: 0, not_raised: 0 },
      runs: { backtest: '20261119-050828-c18c49', paper: '20261021-231000-aaaaaa' }, gap: { against: 'live', points: -1.61 },
      figures: { backtest: FIGURES, paper: FIGURES, live: { ...FIGURES, sum: 1.43, compounded: 1.43 } },
      bridge: { to_paper: TO_PAPER, to_live: TO_LIVE, calibrated: null },
    }],
    summary: {
      tracks: { backtest: { days: 3, trades: 4, sum: 12.1, mean: 3.03, compounded: 12.6 }, paper: { days: 3, trades: 4, sum: 11.6, mean: 2.9, compounded: 12 },
        live: { days: 3, trades: 3, sum: 4.1, mean: 1.37, compounded: 4.2 } },
      to_paper: TO_PAPER, to_live: TO_LIVE, calibrated: null,
      agreement: { days: 3, same_bar: 4, other_bar: 0, not_replayed: 0, not_raised: 1, raised: 4, same_bar_percent: 100 },
    },
    calibration: {
      now: {
        values: { execution: 'measured', capital: 21.82 }, template: { signal_delay_seconds: 3, execution: 'fixed', capital: 100 },
        measured: { signal_delay_seconds: { count: 3, median: 4.2, p90: 6 }, entry_slippage_percent: { count: 3, median: 0.6, p90: 1 },
          exit_slippage_percent: { count: 3, median: 0.3, p90: 0.4 }, trades: 3 },
        said: ['The signal’s delay keeps the backtest’s 3 s: 3 of 10 live entries measured'], template_run: '20261119-050828-c18c49',
        orders: { from: '2026-07-22', to: '2026-10-21', measures: { order_seconds: 40, slippage_buy: 22, slippage_sell: 20 } },
      },
      history: [],
    },
    ...changes,
  }
}

function liveCheckDay(): LiveCheckDay {
  const trade = { key: '1', id: 1, direction: 'long', turbo: { uic: 555, description: 'Turbo 555' }, decided_at: null,
    filled_at: '2026-10-21T08:31:03Z', entry_price: 5, exit_at: '2026-10-21T09:00:00Z', exit_price: 5.15, exit_reason: 'Daily profit target',
    exit_group: 'Daily profit target', result: 3, best: 3, worst: -1, units: 3, flags: [] }
  return {
    rule: liveCheck().rule, database: true, day: '2026-10-21', present: ['backtest', 'paper', 'live'], tolerance_seconds: 120,
    figures: liveCheck().days[0].figures, bridge: { to_paper: TO_PAPER, to_live: TO_LIVE, calibrated: null }, agreement: null,
    runs: { backtest: '20261119-050828-c18c49', paper: '20261021-231000-aaaaaa' },
    signals: [
      { key: '1', at: '2026-10-21T08:30:00Z', closed_at: '2026-10-21T08:31:00Z', action: 'long', title: 'Up', near: null,
        backtest: { fate: 'executed' }, paper: { fate: 'executed' },
        live: { fate: 'executed', engine: { outcome: 'sent', raised_seconds: 1, sent_seconds: 1.3 }, trader: { outcome: 'executed', reason: null, decided_seconds: 9 } },
        why_not_raised: null },
      { key: '2', at: '2026-10-21T10:00:00Z', closed_at: '2026-10-21T10:01:00Z', action: 'short', title: 'Down', near: null,
        backtest: { fate: 'executed' }, paper: null, live: { fate: 'not_sent', reason: 'Not alone on us100' }, why_not_raised: null },
    ],
    trades: [{
      key: '1', at: '2026-10-21T08:30:00Z', action: 'long', title: 'Up', near: null, same_turbo: false,
      backtest: trade, paper: trade, live: { ...trade, id: '7712345', turbo: { uic: 556, description: 'Turbo 556' }, entry_price: 5.05,
        result: 1.43, decided_price: 5.14 },
    }],
  }
}

let rules: TaRule[] = []
let paper: BacktestPaper = { rules: {} }
let checked: LiveCheck | null = null
let refusedDay: string | null = null
let sent: { method: string; path: string }[] = []
// Its script as a rule (the server's rule check), as going live reads it.
let asRule: RuleCheckSummary | null = null
const RULE_CHECKS_STATE = { available: true, queued: [], testing: null, this_hour: 0, an_hour: 6, checking: 0, last_refusal: null }

function serve() {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      sent.push({ method, path: `${url.pathname}${url.search}` })
      if (url.pathname === '/api/ta/rules' && method === 'GET') {
        return json({ revision: 'r1', rules, indices: ['us100'], instruments: [{ epic: 'US100', label: 'us100' }],
          engine_history_days: 1, engine_restart_time: '21:15' })
      }
      const day = /^\/api\/ta\/rules\/[0-9a-f]+\/days\/([0-9-]+)\/indicator$/.exec(url.pathname)
      if (day) {
        return day[1] === refusedDay
          ? json({ detail: 'No bar of capital/US100 that engine day: its feed recorded nothing then' }, 409)
          : json(dayOf(day[1]))
      }
      if (url.pathname === `/api/ta/rules/${ID}/history`) return json(HISTORY)
      if (url.pathname === '/api/ta/rule-checks') return json({ scripts: {}, rules: asRule ? { [ID]: asRule } : {}, state: RULE_CHECKS_STATE })
      if (url.pathname === `/api/ta/rules/${ID}/rule-check`) {
        return method === 'POST'
          ? json({ ...RULE_CHECKS_STATE, queued: [{ about: { rule: ID }, settings: {}, asked_at: 'now' }] }, 202)
          : json({ report: { tested: null, as_rule: false }, state: RULE_CHECKS_STATE })
      }
      const mode = /^\/api\/ta\/rules\/([0-9a-f]+)\/mode$/.exec(url.pathname)
      if (mode && method === 'POST') {
        return json({ rule: rule({ mode: (JSON.parse(init.body as string) as { mode: TaRule['mode'] }).mode }), revision: 'r2' })
      }
      if (/^\/api\/ta\/rules\/[0-9a-f]+$/.test(url.pathname) && method === 'DELETE') return json(null, 204)
      if (url.pathname === '/api/ta/scripts') return json({ items: [CROSS] })
      if (url.pathname === `/api/ta/scripts/${CROSS.id}/versions/1`) return json({ ...CROSS.versions[0], source: SOURCE })
      if (url.pathname === '/api/ta/engine') return json(ENGINE)
      if (url.pathname === '/api/ta/signals') return json({ items: [signal()] })
      if (url.pathname.endsWith('/comparison')) {
        return json({ day: opening, indice: 'us100', tolerance_seconds: 120, rows: [], paired: 0, only_wata: 0, only_tradingview: 0 })
      }
      if (url.pathname.startsWith('/api/data/assets/')) return json({ bars: [] })
      if (url.pathname === '/api/backtests/paper') return json(paper)
      if (url.pathname === `/api/ta/rules/${ID}/live-check`) return checked ? json(checked) : json({ detail: 'off' }, 404)
      if (url.pathname === `/api/ta/rules/${ID}/live-check/days/2026-10-21`) return json(liveCheckDay())
      if (url.pathname === `/api/ta/rules/${ID}/live-check/candidates`) {
        return json({ items: [{ configuration: '2bb6f8514b16', run: '20261119-050828-c18c49', name: 'v4 (again)', from: '2026-10-15',
          to: '2026-10-21', headline: null, strategy: { same: true, checks: [] } }] })
      }
      if (url.pathname === `/api/ta/rules/${ID}/live-check/backtest`) return json({ configuration: '2bb6f8514b16', strategy: { same: true, checks: [] } })
      if (url.pathname === '/api/backtests/configurations/2bb6f8514b16' && method === 'PATCH') return json({})
      if (url.pathname === '/api/agent') return json({ enabled: false, model: 'claude-opus-5-5' })
      return json({ detail: `Not served in this test: ${method} ${url.pathname}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/ta/signals/:id', element: <WithTrail><SignalRulePage /><Address /></WithTrail> },
      { path: '/ta/signals/:id/live/:day', element: <WithTrail><LiveCheckDayPage /><Address /></WithTrail> },
      { path: '/ta/signals', element: <Address /> },
    ],
    { initialEntries: [address] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <RouterProvider router={router} />
          </AuthContext.Provider>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  rules = [rule()]
  paper = { rules: {} }
  checked = null
  refusedDay = null
  asRule = null
  serve()
})

afterEach(() => {
  vi.unstubAllGlobals()
  resetVela()
})

describe("a signal rule's page", () => {
  it('gathers what the rule runs, its readiness, its signals and its history, under Signal rules in the trail', async () => {
    renderAt(`/ta/signals/${ID}`)

    expect(await screen.findByRole('heading', { name: 'Cross on US100', level: 1 })).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Technical Analysis.*Signal rules.*Cross on US100/)
    expect(within(trail).getByRole('link', { name: 'Signal rules' })).toHaveAttribute('href', '/ta/signals')

    // Its readiness, check by check, and in a few words.
    expect(await screen.findByText('A week in shadow')).toBeInTheDocument()
    expect(screen.getByText(/to go before live|Ready to go live/)).toBeInTheDocument()
    expect(screen.getByText('Engine: running')).toBeInTheDocument()
    // What it ran over time.
    expect(await screen.findByText('Made by Claude')).toBeInTheDocument()
    expect(screen.getByText('Fast: 12 → 20.')).toBeInTheDocument()
    // Its journal, held to it: no rule to choose.
    const journal = screen.getByRole('heading', { name: 'Signals' }).closest('section')!
    expect(await within(journal).findAllByText('Up')).not.toHaveLength(0)
    expect(within(journal).queryByRole('combobox', { name: 'Rule' })).toBeNull()
    // Its days on paper, with the way to make it a reference run.
    expect(screen.getByRole('link', { name: 'start a run from this rule' })).toHaveAttribute('href', `/backtests/new?rule=${ID}`)
    expect(screen.getByRole('link', { name: 'Engine monitor' })).toHaveAttribute('href', `/ta/monitor?rule=${ID}`)
  })

  it('holds going live back while its script may repaint, untested as it runs it, and tests it so', async () => {
    asRule = {
      verdict: 'may_repaint', checked_at: '2026-10-22T09:00:00Z', counts: { risk: 1 }, tested_at: null, tested_bars: null, candidates: 0,
      worst: { id: 'other-series', level: 'risk', message: 'Its alerts read another series.' }, as_rule: false,
    }
    const user = userEvent.setup()
    renderAt(`/ta/signals/${ID}`)

    const readiness = within(await screen.findByRole('list', { name: 'Is it ready to go live?' }))
    expect(await readiness.findByText('Its script may repaint, and was never tested on recorded bars as this rule runs it.')).toBeInTheDocument()
    expect(screen.getByText(/Not tested as it runs yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Its script as a rule' })).toHaveAttribute('href', `/ta/indicators/${CROSS.id}?tab=rule&version=1`)

    await user.click(screen.getByRole('button', { name: 'Test its script as it runs it' }))
    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === `/api/ta/rules/${ID}/rule-check`)).toBe(true))
  })

  it('draws its day as the engine ran it, with its signals, and steps a weekday back', async () => {
    const user = userEvent.setup()
    renderAt(`/ta/signals/${ID}`)

    await waitFor(() => expect(velas).toHaveLength(1))
    expect(sent.some((request) => request.path === `/api/ta/rules/${ID}/days/${opening}/indicator`)).toBe(true)
    const chart = velas[0]
    expect(chart.options).toMatchObject({ symbol: 'capital:CAPITAL_US100', timeframe: '1', live: false })
    await waitFor(() => expect(chart.indicators).toHaveLength(2))
    const [script, journal] = chart.indicators
    expect(script.options).toEqual({ id: 'script', inputs: { fast: 20 } })
    expect(journal.options).toEqual({ id: 'run', language: 'wata-run', title: 'The rule: its signals' })
    const layer = JSON.parse(journal.source) as { labels: { text: string; tooltip: string }[]; trades: unknown[] }
    expect(layer.labels.map((label) => label.text)).toEqual(['Long', 'Short'])
    expect(layer.labels[0].tooltip).toBe('#7 Long (Up), journaled in shadow, never sent')
    expect(layer.trades).toEqual([])
    expect(screen.getByText('2 signals that engine day, on the bar each was raised on.')).toBeInTheDocument()

    // Its own stepper: the comparison with TradingView has one too.
    const day = screen.getByRole('heading', { name: 'Its day' }).closest('section')!
    await user.click(within(day).getByRole('button', { name: 'The weekday before' }))
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(`/ta/signals/${ID}?day=${before}`))
    await waitFor(() => expect(sent.some((request) => request.path === `/api/ta/rules/${ID}/days/${before}/indicator`)).toBe(true))
  })

  it('says why a day cannot be drawn', async () => {
    refusedDay = before
    renderAt(`/ta/signals/${ID}?day=${before}`)

    expect(await screen.findByText(/No bar of capital\/US100 that engine day/)).toBeInTheDocument()
    expect(velas).toHaveLength(0)
  })

  it('goes live through its confirmation, and once deleted leads back to the list', async () => {
    const user = userEvent.setup()
    renderAt(`/ta/signals/${ID}`)

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Mode of Cross on US100' }), 'live')
    const confirmation = await screen.findByRole('dialog')
    expect(within(confirmation).getByText('Is it ready?')).toBeInTheDocument()
    expect(sent.some((request) => request.path.endsWith('/mode'))).toBe(false)
    await user.click(within(confirmation).getByRole('button', { name: 'Cancel' }))

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    const asked = await screen.findByRole('dialog', { name: 'Delete Cross on US100' })
    await user.click(within(asked).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^\/ta\/signals$/))
    expect(sent.some((request) => request.method === 'DELETE' && request.path === `/api/ta/rules/${ID}?revision=r1`)).toBe(true)
  })

  it('shows the 404 for an address that names no rule', async () => {
    renderAt('/ta/signals/0123456789ab')

    expect(await screen.findByRole('heading', { name: 'No such signal rule' })).toBeInTheDocument()
  })
})

describe("a rule's real trades beside its paper days and its backtest", () => {
  it('sets each day of a live rule beside its paper day and its backtest, and says where the result went', async () => {
    rules = [rule({ mode: 'live', mode_changed_at: '2026-10-21T08:00:00Z' })]
    checked = liveCheck()
    renderAt(`/ta/signals/${ID}`)

    const panel = (await screen.findByRole('heading', { name: 'Live, paper and backtest' })).closest('section')!
    expect(await within(panel).findByRole('link', { name: 'v4: room to the session extreme' })).toHaveAttribute('href', '/backtests/configurations/2bb6f8514b16')
    expect(within(panel).getByText('Same strategy')).toBeInTheDocument()
    expect(within(panel).getByText(/The backtest raised 4 of the engine’s 4 entry signals at the same bar \(100.0 %\)/)).toBeInTheDocument()
    // The bridge: from paper to live, where most of it went.
    expect(within(panel).getByText(/most of it exit fill, −4.46 pt over 3 trades/)).toBeInTheDocument()
    // A day opens on its own page.
    expect(within(panel).getByRole('link', { name: /21 Oct/ })).toHaveAttribute('href', `/ta/signals/${ID}/live/2026-10-21`)
    // What live measured, taken or kept by the calibrated run, and a run made with them.
    expect(within(panel).getByText('21.82 €')).toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Make a run with them' })).toHaveAttribute('href', `/backtests/new?again=${CALIBRATED_RUN}`)
    // Its nightly off: the days after its newest run have no backtest until it is switched on.
    await userEvent.click(within(panel).getByRole('button', { name: 'Switch its nightly on' }))
    await waitFor(() => expect(sent).toContainEqual({ method: 'PATCH', path: '/api/backtests/configurations/2bb6f8514b16' }))
  })

  it('links a configuration playing the rule’s strategy as its backtest', async () => {
    checked = liveCheck({ link: null, strategy: null, template: null, calibrated: null, days: [],
      summary: { tracks: {}, to_paper: null, to_live: null, calibrated: null, agreement: null }, calibration: { now: null, history: [] } })
    renderAt(`/ta/signals/${ID}`)

    const panel = (await screen.findByRole('heading', { name: 'Live, paper and backtest' })).closest('section')!
    await userEvent.click(await within(panel).findByRole('button', { name: 'Link a backtest' }))
    await userEvent.click(await within(panel).findByRole('button', { name: 'Link it' }))
    await waitFor(() => expect(sent).toContainEqual({ method: 'PUT', path: `/api/ta/rules/${ID}/live-check/backtest` }))
  })

  it('opens a day: each signal and each trade on each track, under the rule in the trail', async () => {
    rules = [rule({ mode: 'live' })]
    renderAt(`/ta/signals/${ID}/live/2026-10-21`)

    expect(await screen.findByRole('heading', { name: /live, paper and backtest/, level: 1 })).toBeInTheDocument()
    expect(await screen.findByText('Not sent: Not alone on us100')).toBeInTheDocument()
    // The rule read, its name stands between Signal rules and the day.
    await waitFor(() => expect(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]).toHaveTextContent(/Signal rules.*Cross on US100.*21 Oct/))
    expect(screen.getByText('raised 1.0 s, sent 1.3 s, decided 9.0 s after the bar’s close')).toBeInTheDocument()
    expect(screen.getByText('Another turbo live')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'The position' })).toHaveAttribute('href', '/positions?find=7712345')
    expect(screen.getByRole('link', { name: 'Paper: its run’s day' })).toHaveAttribute('href', '/backtests/20261021-231000-aaaaaa/days/2026-10-21')
  })
})
