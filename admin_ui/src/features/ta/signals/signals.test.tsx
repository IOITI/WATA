import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { BacktestPaper, Session, TaEngine, TaRule, TaScriptSummary, TaSignal } from '@/lib/types'
import { run as backtest } from '@/test/backtests'
import { WithTrail } from '@/test/WithTrail'

import { SignalRulesPage } from './SignalRulesPage'

// The script's inputs, as PineTS reads them in the page's worker.
vi.mock('../engine/pineWorkerClient', () => ({
  PineWorkerClient: class {
    compile = async () => ({
      id: 1,
      ok: true,
      op: 'compile',
      declaration: 'indicator',
      inputs: [{ id: 'in_0', name: 'Fast', title: 'Fast', varId: 'fast', type: 'int', defval: 9, minval: 1 }],
    })
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
      }
      if (url.pathname === '/api/ta/rules' && method === 'GET') return json(listed)
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
    // Told, never stopped: the slide is there all the same.
    expect(within(dialog).getByRole('slider')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(sent.filter((request) => request.method === 'POST')).toEqual([])
  })

  it('says why a rule did not go live', async () => {
    serve({ modeError: { status: 409, detail: 'The Capital.com feed is off: a live rule would have no bars' } })
    const user = userEvent.setup()
    renderPage()

    await user.selectOptions(await screen.findByRole('combobox', { name: 'Mode of Cross on US100' }), 'live')
    const dialog = await screen.findByRole('dialog', { name: 'Switch Cross on US100 to live' })
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
    // Bars of seconds too, any other length typed, or range bars.
    const bars = within(dialog).getByRole('combobox', { name: 'Bars' })
    expect(within(bars).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '10 s', '20 s', '30 s', '45 s', '1 min', '5 min', '15 min', '1 hour', 'Other length…', 'Range bars',
    ])
    await user.selectOptions(bars, '10S')
    const fast = await within(dialog).findByRole('spinbutton', { name: 'Fast' })
    await user.clear(fast)
    await user.type(fast, '12')
    expect(within(dialog).getByText('Its message says long.')).toBeInTheDocument()
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'What “Up” becomes' }), 'message')
    await user.selectOptions(within(dialog).getByRole('combobox', { name: 'What “Down” becomes' }), 'short')
    await user.click(within(dialog).getByRole('button', { name: 'Review' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Make the rule' }))

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
    expect(within(dialog).getByRole('combobox', { name: 'Bars' })).toHaveValue('range')
    // 6 points: R6000 on Dukascopy's us100 (0.001), R60 on Capital.com's US100 (0.1).
    expect(within(dialog).getByRole('spinbutton', { name: 'Range, in steps of the price' })).toHaveValue(60)
    expect(await within(dialog).findByRole('spinbutton', { name: 'Fast' })).toHaveValue(12)
    await user.click(within(dialog).getByRole('button', { name: 'Review' }))
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
})
