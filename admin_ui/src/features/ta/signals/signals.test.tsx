import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { BacktestPaper, Session, TaEngine, TaHistory, TaRule, TaScriptSummary, TaSignal } from '@/lib/types'
import { run as backtest } from '@/test/backtests'
import { WithTrail } from '@/test/WithTrail'

import { SignalRulesPage } from './SignalRulesPage'

// The script's inputs, as PineTS reads them in the page's worker.
const FAST = { id: 'in_0', name: 'Fast', title: 'Fast', varId: 'fast', type: 'int', defval: 9, minval: 1 }
const worker = vi.hoisted(() => ({ inputs: [] as object[] }))
vi.mock('../engine/pineWorkerClient', () => ({
  PineWorkerClient: class {
    compile = async () => ({ id: 1, ok: true, op: 'compile', declaration: 'indicator', inputs: worker.inputs })
    terminate() {}
  },
}))

const SESSION: Session = {
  authenticated: true,
  version: '0.20.0',
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
alertcondition(ta.crossover(close, ta.ema(close, fast)), title = "Up", message = '{"action": "long", "indice": "us100"}')
alertcondition(ta.crossunder(close, ta.ema(close, fast)), title = "Down", message = '{"action": "short"}')
`

const CROSS: TaScriptSummary = {
  id: 'cccccccccccc',
  name: 'Cross',
  kind: 'indicator',
  description: '',
  pine_version: 6,
  library_name: null,
  imports: [],
  version: 1,
  versions: [{ number: 1, saved_at: '2026-10-19T10:00:00+00:00', size: 300, sha256: 'x' }],
  created_at: '2026-10-19T10:00:00+00:00',
  updated_at: '2026-10-19T10:00:00+00:00',
}

function rule(changes: Partial<TaRule> = {}): TaRule {
  return {
    id: 'aaaaaaaaaaaa',
    name: 'Cross on US100',
    asset: { provider: 'capital', key: 'US100' },
    side: 'bid',
    bars: { type: 'time', timeframe: '1' },
    script: { id: CROSS.id, version: 1, sha256: 'x' },
    libraries: [],
    inputs: [],
    alerts: [{ title: 'Up', action: 'long' }],
    alert_calls: 'ignore',
    confidence_plot: null,
    indice: 'us100',
    mode: 'shadow',
    created_at: '2026-10-19T10:00:00+00:00',
    updated_at: '2026-10-19T10:00:00+00:00',
    mode_changed_at: '2026-10-19T10:00:00+00:00',
    script_name: 'Cross',
    latest: { script: 1, libraries: {} },
    ...changes,
  }
}

const SIGNAL: TaSignal = {
  id: 1, rule_id: 'aaaaaaaaaaaa', rule_name: 'Cross on US100', script_id: CROSS.id, script_version: 1,
  asset: 'capital/US100', side: 'bid', bars: '1', bar_time: '2026-10-19T09:31:00Z', bar_closed_at: '2026-10-19T09:32:00Z',
  alert_key: 'alertcondition:Up', alert_title: 'Up', alert_message: '{"action": "long"}', action: 'long', indice: 'us100',
  confidence: null, mode: 'live', raised_at: '2026-10-19T09:32:01Z', outcome: 'sent', sent_at: '2026-10-19T09:32:02Z',
  http_status: 200, signal_uuid: 'u-1', detail: null,
  trader: { decided_at: '2026-10-19T09:32:03Z', outcome: 'refused', reason: 'Max trades per day reached', position_id: null },
}

const ENGINE: TaEngine = {
  enabled: true,
  status: 'ok',
  detail: '1 rule(s) on',
  engine: {
    updated_at: '2026-10-19T09:40:00Z', started_at: '2026-10-19T06:00:00Z', version: '0.20.0', state: 'running', detail: '1 rule(s) on',
    figures: { live: 0, shadow: 1, covered_until: 1792056000, runner: { pinets: '0.11.1', node: 'v24.21.0' } },
  },
  rules: {
    aaaaaaaaaaaa: {
      updated_at: '2026-10-19T09:40:00Z', state: 'error', detail: 'The script does not run: Undeclared identifier (script, line 4)',
      mode: 'shadow', script_version: 1, bars: 1380, last_bar_time: '2026-10-19T09:39:00Z', last_alert_at: null, figures: { milliseconds: 41 },
    },
  },
}

/** What the server counts of one-minute bars of US100, a day before each restart: the week up to Thursday 22 October. */
const HISTORY: TaHistory = {
  asset: { provider: 'capital', key: 'US100' },
  side: 'bid',
  bars: { type: 'time', timeframe: '1' },
  history_days: 1,
  restart_time: '21:15',
  restarted_at: '2026-10-22T21:15:00+00:00',
  starts_at: '2026-10-21T21:15:00+00:00',
  at_restart: 1374,
  now: 1412,
  engine_days: [1380, 45, 0, 1380, 1378, 1380, 1374].map((bars, index) => ({
    start: new Date(Date.UTC(2026, 9, 15 + index, 21, 15)).toISOString(),
    end: new Date(Date.UTC(2026, 9, 16 + index, 21, 15)).toISOString(),
    bars,
  })),
}

let rules: TaRule[] = []
let paper: BacktestPaper = { rules: {} }
let sent: { method: string; path: string; body: unknown }[] = []

function serve({ modeError }: { modeError?: { status: number; detail: string } } = {}) {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path: `${url.pathname}${url.search}`, body })
      const listed = {
        revision: 'r1', rules, indices: ['us100'], instruments: [{ epic: 'US100', label: 'us100' }], engine_history_days: 1,
        engine_restart_time: '21:15',
      }
      if (url.pathname === '/api/ta/rules' && method === 'GET') return json(listed)
      if (url.pathname === '/api/ta/history') {
        const days = url.searchParams.get('days')
        return json({ ...HISTORY, history_days: days === null ? 1 : Number(days) })
      }
      if (url.pathname === '/api/ta/rules' && method === 'POST') {
        const made = rule({ ...(body as Partial<TaRule>), id: 'bbbbbbbbbbbb', mode: 'shadow' })
        return json({ rule: made, revision: 'r2' }, 201)
      }
      const mode = /^\/api\/ta\/rules\/([0-9a-f]+)\/mode$/.exec(url.pathname)
      if (mode && method === 'POST') {
        if (modeError) return json({ detail: modeError.detail }, modeError.status)
        const changed = rules.find((entry) => entry.id === mode[1])!
        return json({ rule: { ...changed, mode: (body as { mode: TaRule['mode'] }).mode }, revision: 'r2' })
      }
      const one = /^\/api\/ta\/rules\/([0-9a-f]+)$/.exec(url.pathname)
      if (one && method === 'DELETE') return json(null, 204)
      if (url.pathname === '/api/ta/scripts') return json({ items: [CROSS] })
      if (url.pathname === `/api/ta/scripts/${CROSS.id}/versions/1`) return json({ ...CROSS.versions[0], source: SOURCE })
      if (url.pathname === '/api/ta/engine') return json(ENGINE)
      if (url.pathname === '/api/ta/signals') return json({ items: [SIGNAL] })
      if (url.pathname.endsWith('/comparison')) {
        return json({ day: '2026-10-19', indice: 'us100', tolerance_seconds: 120, rows: [], paired: 0, only_wata: 0, only_tradingview: 0 })
      }
      if (url.pathname.startsWith('/api/data/assets/')) return json({ bars: [] })
      if (url.pathname === '/api/backtests/paper') return json(paper)
      if (url.pathname === `/api/backtests/${FROM_RUN.id}`) return json(FROM_RUN)
      if (url.pathname === '/api/data/assets') {
        return json({
          timezone: 'Europe/Paris', seen: 'all', truncated: false, max: 5000, saxo_error: null,
          items: [
            { provider: 'capital', key: 'US100', ref: 'capital/US100', role: 'underlying', status: 'recording', tick: 0.1 },
            { provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', role: 'underlying', status: 'files', tick: 0.001 },
          ],
        })
      }
      return json({ detail: `Not served in this test: ${method} ${url.pathname}` }, 404)
    }),
  )
}

/** A backtest of the Cross on Dukascopy's us100, in range bars of 6 points: R6000 in its tick. */
const FROM_RUN = (() => {
  const made = backtest({ id: '20261021-133842-453160', name: 'Cross on Dukascopy' })
  return {
    ...made,
    request: {
      ...made.request,
      chart: { asset: { provider: 'dukascopy' as const, key: 'us100' }, side: 'bid' as const, bars: { type: 'range' as const, ticks: 6000 } },
      source: {
        kind: 'pine' as const,
        rule: {
          ...(made.request.source.kind === 'pine' ? made.request.source.rule : ({} as never)),
          name: 'Cross',
          script: { id: CROSS.id, version: 1, sha256: 'x' },
          inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 12 }],
          alerts: [{ title: 'Up', action: 'long' as const }, { title: 'Down', action: 'short' as const }],
        },
        from_rule: null,
      },
    },
  }
})()

function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

function renderPage(session: Session = SESSION, address = '/ta/signals') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/ta/signals', element: <WithTrail><SignalRulesPage /><Address /></WithTrail> }],
    { initialEntries: [address] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <AuthContext.Provider value={{ session, error: null, login: async () => {}, logout: async () => {} }}>
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
  worker.inputs = [FAST]
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Signal rules page', () => {
  it('lists each rule with what it runs, what makes signals, and how the engine runs it', async () => {
    renderPage()

    expect(await screen.findByText('Cross on US100', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('“Up” → long')).toBeInTheDocument()
    expect(screen.getByText('Failing')).toBeInTheDocument()
    expect(screen.getByText('The script does not run: Undeclared identifier (script, line 4)')).toBeInTheDocument()
    expect(screen.getByText('PineTS 0.11.1, Node v24.21.0')).toBeInTheDocument()
    // The journal: the signal sent, and what the trader made of it.
    expect(await screen.findByText('Max trades per day reached')).toBeInTheDocument()
  })

  it('leads to the Engine monitor, for the engine and for each rule it runs', async () => {
    renderPage()

    expect(await screen.findByRole('link', { name: 'Engine monitor' })).toHaveAttribute('href', '/ta/monitor')
    expect(screen.getByRole('link', { name: 'run in 41 ms' })).toHaveAttribute('href', '/ta/monitor?rule=aaaaaaaaaaaa')
  })

  it('says why each new bar runs a rule whole, when it does', async () => {
    const state = ENGINE.rules.aaaaaaaaaaaa
    const whole = { ...state, state: 'running' as const, detail: null, figures: { milliseconds: 1840, continued: false, whole: 'its script reads barstate.islast' } }
    ENGINE.rules.aaaaaaaaaaaa = whole
    try {
      renderPage()

      expect(await screen.findByText('Each new bar runs it whole: its script reads barstate.islast.')).toBeInTheDocument()
    } finally {
      ENGINE.rules.aaaaaaaaaaaa = state
    }
  })

  it('shows each shadow rule traded on paper, beside its reference', async () => {
    paper = {
      rules: {
        aaaaaaaaaaaa: {
          days: [
            { day: '2026-10-19', run_id: '20261020-010000-aaaaaa', state: 'done', day_percent: 0.4, trades: 2, outside: false },
            { day: '2026-10-16', run_id: '20261017-010000-bbbbbb', state: 'done', day_percent: -6, trades: 3, outside: true },
          ],
          reference: { run_id: '20261012-010000-cccccc', name: 'Cross, 3 months', days: 60, low: -1.5, median: 0.2, high: 2.75 },
        },
      },
    }
    renderPage()

    expect(await screen.findByText('2 days, −5.60%')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Last, 19 Oct: +0.40% in 2 trades' })).toHaveAttribute('href', '/backtests/20261020-010000-aaaaaa')
    expect(screen.getByText(/^1 day outside it\. Its reference \(Cross, 3 months\)/)).toBeInTheDocument()
  })

  it('switches a rule off at once, and asks before it goes live', async () => {
    const user = userEvent.setup()
    renderPage()
    const select = await screen.findByRole('combobox', { name: 'Mode of Cross on US100' })

    await user.selectOptions(select, 'off')
    await waitFor(() =>
      expect(sent.find((request) => request.path.endsWith('/mode'))?.body).toEqual({ mode: 'off', revision: 'r1' }),
    )

    sent = []
    await user.selectOptions(select, 'live')
    const dialog = await screen.findByRole('dialog', { name: 'Switch Cross on US100 to live' })
    expect(within(dialog).getByText(/switch TradingView's alerts for us100 off first/)).toBeInTheDocument()
    expect(within(dialog).getByText('“Up” → long')).toBeInTheDocument()
    // Whether it is ready, as far as the page knows: the engine fails to run it, no signal journaled yet.
    const ready = within(dialog).getByRole('list', { name: 'Is it ready?' })
    expect(within(ready).getByText('Failing: The script does not run: Undeclared identifier (script, line 4).')).toBeInTheDocument()
    expect(within(ready).getByText('None yet: what it would send is not known.')).toBeInTheDocument()
    // Not ready, as the server holds it: the slide waits until the operator asks to go live anyway.
    const slider = within(dialog).getByRole('slider')
    slider.focus()
    fireEvent.keyDown(slider, { key: 'Enter' })
    expect(within(dialog).getByRole('checkbox', { name: 'Go live anyway' })).not.toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(sent.filter((request) => request.method === 'POST')).toEqual([])
  })

  it('takes a rule not ready live only once the operator goes live anyway, and tells the server so', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Mode of Cross on US100' }), 'live')
    const dialog = await screen.findByRole('dialog', { name: 'Switch Cross on US100 to live' })
    await user.click(within(dialog).getByRole('checkbox', { name: 'Go live anyway' }))
    const slider = within(dialog).getByRole('slider')
    slider.focus()
    fireEvent.keyDown(slider, { key: 'Enter' })

    await waitFor(() =>
      expect(sent.find((request) => request.path.endsWith('/mode'))?.body).toEqual({ mode: 'live', revision: 'r1', force: true }),
    )
    expect(await screen.findByText(/Gone live without its readiness/)).toBeInTheDocument()
  })

  it('asks nothing more of a rule the page finds ready, and offers to go live anyway if the server finds it is not', async () => {
    const state = ENGINE.rules.aaaaaaaaaaaa
    ENGINE.rules.aaaaaaaaaaaa = { ...state, state: 'running', detail: null, last_alert_at: '2026-10-19T09:32:01Z' }
    rules = [rule({ mode_changed_at: '2026-09-01T10:00:00+00:00' })]
    serve({ modeError: { status: 409, detail: 'Not ready to go live: the engine fails to run it (no more said). Go live anyway to switch it all the same: that is recorded' } })
    try {
      const user = userEvent.setup()
      renderPage()

      await user.selectOptions(await screen.findByRole('combobox', { name: 'Mode of Cross on US100' }), 'live')
      const dialog = await screen.findByRole('dialog', { name: 'Switch Cross on US100 to live' })
      expect(within(dialog).queryByRole('checkbox', { name: 'Go live anyway' })).not.toBeInTheDocument()
      const slider = within(dialog).getByRole('slider')
      slider.focus()
      fireEvent.keyDown(slider, { key: 'Enter' })

      expect(await within(dialog).findByText(/^Not ready to go live: the engine fails to run it/)).toBeInTheDocument()
      expect(sent.find((request) => request.path.endsWith('/mode'))?.body).toEqual({ mode: 'live', revision: 'r1' })
      expect(within(dialog).getByRole('checkbox', { name: 'Go live anyway' })).not.toBeChecked()
    } finally {
      ENGINE.rules.aaaaaaaaaaaa = state
    }
  })

  it('says why a rule did not go live', async () => {
    serve({ modeError: { status: 409, detail: 'The Capital.com feed is off: a live rule would have no bars' } })
    const user = userEvent.setup()
    renderPage()

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Mode of Cross on US100' }), 'live')
    const dialog = await screen.findByRole('dialog', { name: 'Switch Cross on US100 to live' })
    await user.click(within(dialog).getByRole('checkbox', { name: 'Go live anyway' }))
    const slider = within(dialog).getByRole('slider')
    slider.focus()
    fireEvent.keyDown(slider, { key: 'Enter' })

    expect(await within(dialog).findByText('The Capital.com feed is off: a live rule would have no bars')).toBeInTheDocument()
  })

  it('offers no live while operator actions are disabled', async () => {
    renderPage({ ...SESSION, actions_enabled: false })

    const select = await screen.findByRole('combobox', { name: 'Mode of Cross on US100' })
    expect(within(select).getByRole('option', { name: 'Live' })).toBeDisabled()
  })

  it('makes a rule in shadow from a script: its inputs read by PineTS, its alerts mapped, reviewed first', async () => {
    rules = []
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New rule' }))
    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    // Four steps: the script first, then its market, its inputs and alerts, and the review.
    const steps = within(within(dialog).getByRole('list', { name: 'Progress' })).getAllByRole('listitem')
    expect(steps.map((entry) => entry.textContent)).toEqual([
      '1Scriptwhat it runs', '2Marketits bars, its indice', '3Inputs and alertswhat makes a signal', '4Review',
    ])
    expect(within(dialog).getByRole('combobox', { name: 'Script' })).toHaveValue(CROSS.id)
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    // Bars of seconds too, any other length typed, or range bars.
    const bars = await within(dialog).findByRole('combobox', { name: 'Bars' })
    expect(within(bars).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '10 s', '20 s', '30 s', '45 s', '1 min', '5 min', '15 min', '1 hour', 'Other length…', 'Range bars',
    ])
    await user.selectOptions(bars, '10S')
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    const fast = await within(dialog).findByRole('spinbutton', { name: 'Fast' })
    await user.clear(fast)
    await user.type(fast, '12')
    expect(within(dialog).getByText('Its message says long.')).toBeInTheDocument()
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'What “Up” becomes' }), 'message')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'What “Down” becomes' }), 'short')
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await within(dialog).findByText('The new rule, in shadow:')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Make the rule' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/ta/rules')).toBe(true))
    const made = sent.find((request) => request.method === 'POST' && request.path === '/api/ta/rules')!.body
    expect(made).toEqual({
      name: 'Cross on US100',
      asset: { provider: 'capital', key: 'US100' },
      side: 'bid',
      bars: { type: 'time', timeframe: '10S' },
      script: { id: CROSS.id, version: 1 },
      inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 12 }],
      alerts: [
        { title: 'Up', action: 'message' },
        { title: 'Down', action: 'short' },
      ],
      alert_calls: 'ignore',
      confidence_plot: null,
      indice: 'us100',
      // Left empty: the engine's.
      history_days: null,
      revision: 'r1',
      mode: 'shadow',
    })
  })

  it('makes a rule in shadow from a backtest, its range brought to the tick of the Capital.com feed', async () => {
    rules = []
    const user = userEvent.setup()
    renderPage(SESSION, `/ta/signals?from-run=${FROM_RUN.id}`)

    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await within(dialog).findByRole('combobox', { name: 'Bars' })).toHaveValue('range')
    // 6 points: R6000 on Dukascopy's us100 (0.001), R60 on Capital.com's US100 (0.1).
    expect(within(dialog).getByRole('spinbutton', { name: 'Range, in steps of the price' })).toHaveValue(60)
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await within(dialog).findByRole('spinbutton', { name: 'Fast' })).toHaveValue(12)
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Make the rule' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/ta/rules')).toBe(true))
    expect(sent.find((request) => request.method === 'POST' && request.path === '/api/ta/rules')!.body).toMatchObject({
      name: 'Cross on US100',
      asset: { provider: 'capital', key: 'US100' },
      bars: { type: 'range', ticks: 60 },
      script: { id: CROSS.id, version: 1 },
      inputs: [{ id: 'in_0', var: 'fast', title: 'Fast', value: 12 }],
      alerts: [{ title: 'Up', action: 'long' }, { title: 'Down', action: 'short' }],
      mode: 'shadow',
    })
    // Asked once: the address forgets it, so that the page opened again does not make it twice.
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^\/ta\/signals$/))
  })

  it('makes a rule of the script its page asked for', async () => {
    rules = []
    renderPage(SESSION, `/ta/signals?new=${CROSS.id}`)

    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    expect(within(dialog).getByRole('combobox', { name: 'Script' })).toHaveValue(CROSS.id)
    expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('Cross on US100')
  })

  it('says what the days of bars before each day are, and counts the bars the engine would run the script on', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New rule' }))
    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    const days = await within(dialog).findByRole('spinbutton', { name: 'Days of bars before each day' })
    // Said under the field, not on hover: the restart in the trading timezone of the day, and in UTC.
    expect(days).toHaveAccessibleDescription(/^Each day at \d\d:\d\d \(21:15 UTC\) the engine starts the rule again on the bars of these days before/)
    expect(within(dialog).getByText(/a weekend or a holiday counts, and brings no bar\..*Left empty: the engine’s history_days, 1 day\.$/)).toBeInTheDocument()

    const count = await within(dialog).findByRole('region', { name: 'Its bars now' })
    expect(
      await within(count).findByText('The script was started on 1,374 bars at the last restart (Thu 22 Oct, 23:15), and would run on 1,412 bars now.'),
    ).toBeInTheDocument()
    expect(within(count).getByText('US100, bid, 1 min:')).toBeInTheDocument()
    expect(within(count).getAllByRole('term').map((term) => term.textContent)).toEqual([
      'Fri 16', 'Sat 17', 'Sun 18', 'Mon 19', 'Tue 20', 'Wed 21', 'Thu 22',
    ])
    expect(within(count).getAllByRole('definition').map((value) => value.textContent)).toEqual(['1,380', '45', '0', '1,380', '1,378', '1,380', '1,374'])
    const asked = () =>
      sent.filter((request) => request.path.startsWith('/api/ta/history')).map((request) => Object.fromEntries(new URL(request.path, 'http://admin.test').searchParams))
    expect(asked()).toEqual([{ epic: 'US100', side: 'bid', timeframe: '1' }])

    // Days of its own: counted again once typed.
    await user.type(days, '3')
    await waitFor(() => expect(asked().at(-1)).toEqual({ epic: 'US100', side: 'bid', days: '3', timeframe: '1' }))
    expect(asked()).toHaveLength(2)
  })

  it('sets the script’s inputs out in the groups it declares them in', async () => {
    // As PineTS reads them: in the order they are declared, each with the group of its input call.
    worker.inputs = [
      { ...FAST, group: 'Main' },
      { id: 'in_1', name: 'Source', title: 'Source', varId: 'src', type: 'source', defval: 'close', options: ['open', 'close'] },
      { id: 'in_2', name: 'Lookback', title: 'Lookback', varId: 'h', type: 'float', defval: 8, group: 'Kernel settings' },
      { id: 'in_3', name: 'Weight', title: 'Weight', varId: 'r', type: 'float', defval: 1, group: 'Kernel settings' },
      { id: 'in_4', name: 'Show', title: 'Show', varId: 'show', type: 'bool', defval: true, group: 'Main' },
    ]
    rules = []
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New rule' }))
    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    for (let step = 0; step < 2; step += 1) await user.click(within(dialog).getByRole('button', { name: 'Next' }))

    const main = await within(dialog).findByRole('group', { name: 'Main' })
    const kernel = within(dialog).getByRole('group', { name: 'Kernel settings' })
    const source = within(dialog).getByRole('combobox', { name: 'Source' })
    // A group where its first input is, with all of its inputs; an input of none where it is, outside them.
    expect(within(main).getByRole('spinbutton', { name: 'Fast' })).toBeInTheDocument()
    expect(within(main).getByRole('checkbox', { name: 'Show' })).toBeInTheDocument()
    expect(within(kernel).getByRole('spinbutton', { name: 'Lookback' })).toBeInTheDocument()
    expect(within(kernel).getByRole('spinbutton', { name: 'Weight' })).toBeInTheDocument()
    expect(main).not.toContainElement(source)
    expect(kernel).not.toContainElement(source)
    expect(main.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(source.compareDocumentPosition(kernel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    // An input of a group is changed as any other.
    const lookback = within(kernel).getByRole('spinbutton', { name: 'Lookback' })
    await user.clear(lookback)
    await user.type(lookback, '10')
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await within(dialog).findByText('Lookback = 10')).toBeInTheDocument()
  })
})

describe('the rules chosen together', () => {
  const choose = async (user: ReturnType<typeof userEvent.setup>, ...names: string[]) => {
    for (const name of names) await user.click(await screen.findByRole('checkbox', { name: `Select ${name}` }))
    return within(await screen.findByRole('toolbar', { name: 'Bulk actions toolbar' }))
  }

  it('switches them off one after the other, each with the revision the last answer gave; a live rule has no box', async () => {
    const user = userEvent.setup()
    rules = [rule(), rule({ id: 'dddddddddddd', name: 'Second' }), rule({ id: 'eeeeeeeeeeee', name: 'Live one', mode: 'live' })]
    renderPage()

    expect(await screen.findByRole('checkbox', { name: 'Select Cross on US100' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Select Live one' })).not.toBeInTheDocument()
    // The journal's signals are records: nothing to choose there.
    expect(screen.queryByRole('checkbox', { name: /^Select row/ })).not.toBeInTheDocument()
    const basket = await choose(user, 'Cross on US100', 'Second')
    expect(basket.getByText('rules selected')).toBeInTheDocument()
    // Both are in shadow already.
    expect(basket.getByRole('button', { name: 'Switch to shadow' })).toBeDisabled()
    await user.click(basket.getByRole('button', { name: 'Switch off' }))

    expect(await screen.findByText('2 rules switched off')).toBeInTheDocument()
    const switched = sent.filter((request) => request.method === 'POST' && request.path.endsWith('/mode'))
    expect(switched.map((request) => [request.path, request.body])).toEqual([
      ['/api/ta/rules/aaaaaaaaaaaa/mode', { mode: 'off', revision: 'r1' }],
      ['/api/ta/rules/dddddddddddd/mode', { mode: 'off', revision: 'r2' }],
    ])
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Bulk actions toolbar' })).not.toBeInTheDocument())
  })

  it('has nothing to choose when every rule is live', async () => {
    rules = [rule({ mode: 'live' })]
    renderPage()

    expect(await screen.findByRole('checkbox', { name: 'Select all rows' })).toBeDisabled()
    expect(screen.queryByRole('checkbox', { name: 'Select Cross on US100' })).not.toBeInTheDocument()
  })

  it('stops at the first refusal, and says how far it went', async () => {
    const user = userEvent.setup()
    rules = [rule({ mode: 'off' }), rule({ id: 'dddddddddddd', name: 'Second', mode: 'off' })]
    serve({ modeError: { status: 409, detail: 'The rules changed meanwhile (revision r0, not r1)' } })
    renderPage()

    const basket = await choose(user, 'Cross on US100', 'Second')
    await user.click(basket.getByRole('button', { name: 'Switch to shadow' }))

    expect(await screen.findByText(/^Stopped after 0 of 2: The rules changed since this page read them/)).toBeInTheDocument()
    expect(sent.filter((request) => request.method === 'POST' && request.path.endsWith('/mode'))).toHaveLength(1)
  })

  it('deletes them once asked, reading the rules again between two', async () => {
    const user = userEvent.setup()
    rules = [rule(), rule({ id: 'dddddddddddd', name: 'Second' })]
    renderPage()

    const basket = await choose(user, 'Cross on US100', 'Second')
    await user.click(basket.getByRole('button', { name: 'Delete 2' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete 2 rules?' })
    expect(within(dialog).getByText('Second')).toBeInTheDocument()
    const before = sent.length
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('2 rules deleted')).toBeInTheDocument()
    const asked = sent.slice(before).filter((request) => request.method === 'DELETE' || request.path === '/api/ta/rules')
    expect(asked.slice(0, 3).map((request) => `${request.method} ${request.path}`)).toEqual([
      'DELETE /api/ta/rules/aaaaaaaaaaaa?revision=r1',
      'GET /api/ta/rules',
      'DELETE /api/ta/rules/dddddddddddd?revision=r1',
    ])
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Delete 2 rules?' })).not.toBeInTheDocument())
  })
})

describe('the rule dialog, step by step', () => {
  it('says what is wrong under its step, and on the review what is left, each leading back to its step', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New rule' }))
    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    // The name a new rule is given is the existing rule's: said under the step it is on.
    expect(within(dialog).getByText('A rule named “Cross on US100” already exists.')).toBeInTheDocument()
    for (let step = 0; step < 3; step += 1) await user.click(within(dialog).getByRole('button', { name: 'Next' }))

    expect(await within(dialog).findByText('Before it can be made:')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Make the rule' })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: 'Script' }))
    const name = await within(dialog).findByRole('textbox', { name: 'Name' })
    await user.clear(name)
    await user.type(name, 'Cross, fast')
    expect(within(dialog).queryByText(/already exists/)).not.toBeInTheDocument()
    for (let step = 0; step < 3; step += 1) await user.click(within(dialog).getByRole('button', { name: 'Next' }))

    expect(await within(dialog).findByRole('button', { name: 'Make the rule' })).toBeEnabled()
    expect(within(dialog).queryByText('Before it can be made:')).not.toBeInTheDocument()
  })

  it('keeps what was typed when it goes back a step', async () => {
    const user = userEvent.setup()
    rules = []
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New rule' }))
    const dialog = await screen.findByRole('dialog', { name: 'New signal rule' })
    await user.clear(within(dialog).getByRole('textbox', { name: 'Name' }))
    await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Mine')
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    await user.selectOptions(await within(dialog).findByRole('combobox', { name: 'Bars' }), '10S')
    await user.click(within(dialog).getByRole('button', { name: 'Back' }))

    expect(await within(dialog).findByRole('textbox', { name: 'Name' })).toHaveValue('Mine')
    await user.click(within(dialog).getByRole('button', { name: 'Next' }))
    expect(await within(dialog).findByRole('combobox', { name: 'Bars' })).toHaveValue('10S')
  })

  it('opens an edit at its start, with nothing to save until something changes', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Edit Cross on US100' }))
    const dialog = await screen.findByRole('dialog', { name: 'Signal rule: Cross on US100' })
    expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('Cross on US100')
    for (let step = 0; step < 3; step += 1) await user.click(within(dialog).getByRole('button', { name: 'Next' }))

    expect(await within(dialog).findByText('0 changes to the rule.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('opens a newer version of the script on its inputs and alerts, and newer libraries on the review', async () => {
    const user = userEvent.setup()
    rules = [
      rule({ latest: { script: 2, libraries: {} } }),
      rule({
        id: 'dddddddddddd', name: 'Cross with helpers',
        libraries: [{ id: 'eeeeeeeeeeee', version: 1, sha256: 'y', name: 'Helpers' }],
        latest: { script: 1, libraries: { Helpers: 2 } },
      }),
    ]
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Run v2' }))
    let dialog = await screen.findByRole('dialog', { name: 'Signal rule: Cross on US100' })
    const current = () => within(within(dialog).getByRole('list', { name: 'Progress' })).getAllByRole('listitem').find((step) => step.getAttribute('aria-current') === 'step')
    expect(current()).toHaveTextContent('Inputs and alerts')
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))

    await user.click(await screen.findByRole('button', { name: 'Run Helpers v2' }))
    dialog = await screen.findByRole('dialog', { name: 'Signal rule: Cross with helpers' })
    expect(current()).toHaveTextContent('Review')
    expect(within(dialog).getByText('The libraries it imports run at their current versions.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeEnabled()
  })
})
