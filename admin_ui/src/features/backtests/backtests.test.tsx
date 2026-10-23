import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type {
  BacktestBreakdowns,
  BacktestEdge,
  BacktestOptions,
  BacktestReconciliation,
  BacktestRun,
  BacktestSweep,
  BacktestTurboMeasure,
  BacktestTurboModel,
  DataAsset,
  PriceBarRow,
  Session,
  TaRule,
  TaScriptSummary,
} from '@/lib/types'
import { day, OPTIONS, run, RUN_ID, SETTINGS, signal, trade } from '@/test/backtests'
import { charts, resetCharts, seriesOf } from '@/test/fakeCharts'
import { stubSystemTheme } from '@/test/systemTheme'
import { WithTrail } from '@/test/WithTrail'

import { BacktestsPage } from './BacktestsPage'
import { ComparePage } from './ComparePage'
import { ExperimentsPage } from './ExperimentsPage'
import { DayPage } from './DayPage'
import { NewBacktestPage } from './NewBacktestPage'
import { RunPage } from './RunPage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)
// The script's inputs, as PineTS reads them in the page's worker.
vi.mock('@/features/ta/engine/pineWorkerClient', () => ({
  PineWorkerClient: class {
    compile = async () => ({
      id: 1,
      ok: true,
      op: 'compile',
      declaration: 'indicator',
      inputs: [{ id: 'in_0', name: 'Fast length', title: 'Fast length', varId: 'fastLength', type: 'int', defval: 9, minval: 1 }],
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
indicator("EMA cross")
fastLength = input.int(9, "Fast length", minval = 1)
fast = ta.ema(close, fastLength)
alertcondition(ta.crossover(close, fast), title = "Cross up", message = '{"action": "long"}')
alertcondition(ta.crossunder(close, fast), title = "Cross down", message = '{"action": "short"}')
`

const SCRIPT: TaScriptSummary = {
  id: '8c2df05570d5',
  name: 'EMA cross',
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

// A second script, at its second version: what a script's page asks the form for.
const OTHER: TaScriptSummary = {
  ...SCRIPT,
  id: 'aaaabbbbcccc',
  name: 'Breakout',
  version: 2,
  versions: [SCRIPT.versions[0], { number: 2, saved_at: '2026-10-20T10:00:00+00:00', size: 320, sha256: 'y' }],
}

const RULE: TaRule = {
  id: 'r00000000001',
  name: 'EMA cross on US100',
  asset: { provider: 'capital', key: 'US100' },
  side: 'bid',
  bars: { type: 'range', ticks: 40 },
  script: { id: SCRIPT.id, version: 1, sha256: 'x' },
  libraries: [],
  inputs: [{ id: 'in_0', var: 'fastLength', title: 'Fast length', value: 12 }],
  alerts: [{ title: 'Cross up', action: 'long' }],
  alert_calls: 'ignore',
  confidence_plot: null,
  indice: 'us100',
  mode: 'shadow',
  created_at: '2026-10-19T10:00:00+00:00',
  updated_at: '2026-10-19T10:00:00+00:00',
  mode_changed_at: '2026-10-19T10:00:00+00:00',
  script_name: 'EMA cross',
  latest: { script: 1, libraries: {} },
}

function asset(changes: Partial<DataAsset>): DataAsset {
  return {
    provider: 'capital', key: 'US100', ref: 'capital/US100', id: null, uic: null, asset_type: 'INDICES', symbol: 'US100',
    description: 'US Tech 100', currency: 'USD', role: 'underlying', indice: 'us100', underlying_uic: null, direction: null,
    barrier: null, issuer: null, first_t: null, last_t: null, last_bid: null, last_ask: null, status: 'recording', ...changes,
  }
}

const ASSETS: DataAsset[] = [
  asset({}),
  asset({ provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', description: 'US Tech 100 (Dukascopy)', status: 'downloaded' }),
  asset({ provider: 'saxo', key: '1', ref: 'saxo/1', id: 1, description: 'NASDAQ 100' }),
  // A turbo: not something a strategy reads.
  asset({ provider: 'saxo', key: '5', ref: 'saxo/5', id: 5, role: 'turbo', description: 'NASDAQ 100 Turbo Short 25900 SG' }),
]

const BREAKDOWNS: BacktestBreakdowns = {
  trades: 3,
  groups: {
    hour: [], weekday: [], direction: [], exit: [], confidence: [], holding: [], turbo_price: [], issuer: [],
    weekday_hour: [
      { weekday: 2, hour: 9, trade_count: 2, win_count: 1, loss_count: 1, win_rate: 50, profit_loss: 5, average_percent: 0.5, average_best_percent: 1 },
      { weekday: 3, hour: 15, trade_count: 1, win_count: 0, loss_count: 1, win_rate: 0, profit_loss: -2, average_percent: -0.4, average_best_percent: 0.2 },
    ],
  },
  given_back: { average_given_back: null, turned_negative_count: 0, turned_negative_percent: null },
}

const utc = (iso: string) => Date.parse(iso) / 1000

/** Bars of an instrument on 14 October from 08:40 to 09:20 in Paris, `interval` seconds apart. */
function bars(first: number, interval: number): PriceBarRow[] {
  const rows: PriceBarRow[] = []
  for (let t = utc('2026-10-14T06:40:00Z'); t < utc('2026-10-14T07:20:00Z'); t += interval) {
    const bid = first + ((t / interval) % 7) * 0.01
    rows.push([t, bid, bid + 0.02, bid - 0.02, bid + 0.01, bid + 0.05, bid + 0.07, bid + 0.03, bid + 0.06, 3])
  }
  return rows
}

let runs: Record<string, BacktestRun> = {}
let research = {
  enabled: false, keep_days: 30, state: 'off', exporting: null, days: 0, first: null, last: null, bytes: 0, error: null,
  location: 'research/days', limits: { keep_days: 365 },
}
let sent: { method: string; path: string; body: unknown }[] = []
let sweeps: Record<string, BacktestSweep> = {}
let edges: Record<string, BacktestEdge> = {}
let reconciliations: Record<string, BacktestReconciliation> = {}
let options: BacktestOptions = OPTIONS
const MEASURE: BacktestTurboMeasure = {
  made_at: '2026-10-20T01:00:00Z',
  underlying: { provider: 'dukascopy', key: 'us100', side: 'bid' },
  indice: 'us100',
  days: 6,
  first: '2026-10-13',
  last: '2026-10-20',
  few_days: false,
  directions: {
    long: { factor: 0.0086, offset: -2, spacing: 50, turbos: 40, bars: 9000, r2: 0.998, error_eur: 0.02, error_percent: 1.2 },
    short: { factor: 0.0085, offset: 2, spacing: 50, turbos: 38, bars: 8800, r2: 0.997, error_eur: 0.03, error_percent: 1.4 },
  },
  quoting: { first_minute: 480, last_minute: 1320 },
  issuer: 'SG',
}
let turboModel: BacktestTurboModel = { state: 'idle', error: null, asked: null, finished_at: null, measure: null, underlying: 'dukascopy/us100', max_days: 92 }
const STRESS_ID = '20261020-010000-dddddd'
const CHECK_ID = '20261020-020000-eeeeee'
const MODEL_CHECK = {
  recorded_trades: 34, modelled_trades: 41, paired: 31, mean_difference_percent: 0.42, median_absolute_difference_percent: 0.9,
  correlation: 0.91, same_exit_percent: 84, median_entry_price: { recorded: 5.04, modelled: 5.11 },
  recorded_total_percent: -67.42, modelled_total_percent: -66.43,
}

function serve() {
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
      const route = url.pathname
      if (route === '/api/backtests' && method === 'GET') {
        return json({ items: Object.values(runs).map(({ request: _r, settings: _s, summary: _m, progress: _p, lines: _l, ...listed }) => listed), storage: { runs: { count: 2, bytes: 52_000 }, former: { count: 7, bytes: 3_400_000 } } })
      }
      if (route === '/api/backtests' && method === 'POST') return json(run({ id: '20261019-230000-cccccc', status: { state: 'queued' } }), 202)
      if (route === '/api/backtests/options') return json(options)
      if (route === '/api/backtests/execution') {
        const spread = (median: number, count: number) => ({ median, p90: median * 1.5, count })
        return json({
          from: '2026-07-21', to: '2026-10-20', signals: 30, orders: 60, requests: 62,
          signal_delay_seconds: { from_signal: spread(2.6, 30), from_receipt: spread(0.6, 30) }, order_seconds: spread(0.4, 60),
          slippage: { buy: { ...spread(0.01, 30), median_ticks: 1 }, sell: { ...spread(0, 30), median_ticks: 0 }, all: { ...spread(0.005, 60), median_ticks: 0.5 } },
          refusals: { share_percent: 3.2, by_hour: [], codes: {} }, suggested: { signal_delay_seconds: 2.6, order_seconds: 0.4, slippage: 0.005 }, least: 10,
        })
      }
      if (route === '/api/backtests/turbo-model') {
        if (method === 'POST') turboModel = { ...turboModel, state: 'running', asked: body as { from: string; to: string } }
        return json(turboModel, method === 'POST' ? 202 : 200)
      }
      if (route === '/api/backtests/former' && method === 'DELETE') return json({ removed: 7 })
      if (route === '/api/backtests/remove' && method === 'POST') return json({ removed: (body as { ids: string[] }).ids, kept: [] })
      if (route === '/api/research/experiments') return json({ experiments: [], lockbox: null })
      if (route === '/api/backtests/research') {
        if (method === 'PUT') research = { ...research, ...(body as object), state: 'waiting_for_close' }
        return json(research)
      }
      if (route === '/api/backtests/coverage') {
        return json({
          database: true,
          timezone: 'Europe/Paris',
          days: ['2026-10-14', '2026-10-15', '2026-10-16'].map((entry) => ({
            day: entry, turbos: { long: 3, short: 3 }, first: null, last: null, recorded_minutes: 831, asset_bars: 5000, signals: null,
          })),
        })
      }
      const match = /^\/api\/backtests\/([^/]+)(?:\/(.+))?$/.exec(route)
      if (match) {
        const found = runs[match[1]]
        if (!found) return json({ detail: 'No such backtest' }, 404)
        const day14 = url.searchParams.get('day')
        switch (match[2]) {
          case undefined:
            return json(found)
          case 'days':
            return json({ items: [day(), day({ day: '2026-10-15', day_percent: 1.7 }), day({ day: '2026-10-16', status: 'no_recorded_turbos', trades: 0, recorded: null })] })
          case 'trades':
            return json({ items: day14 === null || day14 === '2026-10-14' ? [trade()] : [] })
          case 'signals':
            return json({
              items: [
                signal(),
                signal({ id: 13, action: 'long', fate: 'refused', rule: 'day_profit', reason: 'Day profit above 1.25%', trade_id: null, candidates: null, bar_time: '2026-10-14T07:10:00Z' }),
              ],
            })
          case 'baseline':
            return json({ repetitions: 20, seed: 1, entries: 113, hours: [0, 1439], totals: [1, 2, 6], trades: [3, 4, 3], histogram: [] })
          case 'breakdowns':
            return json(BREAKDOWNS)
          case 'sweep':
            return sweeps[match[1]] ? json(sweeps[match[1]]) : json({ detail: 'Not a finished sweep' }, 409)
          case 'edge':
            return json(edges[match[1]] ?? {})
          case 'reconciliation':
            return json(reconciliations[match[1]] ?? {})
          case 'stress':
            return json(run({ id: STRESS_ID, kind: 'sweep', name: `Stress: ${found.name}`, stress_of: found.id, status: { state: 'queued' } }), 202)
          case 'model-check':
            return json(run({ id: CHECK_ID, name: `Model check: ${found.name}`, model_check_of: found.id, turbos: 'modelled', status: { state: 'queued' } }), 202)
          default:
            return json({ detail: `Not served: ${route}` }, 404)
        }
      }
      if (route === '/api/ta/scripts') return json({ items: [SCRIPT, OTHER] })
      const version = /^\/api\/ta\/scripts\/([0-9a-f]+)\/versions\/(\d+)$/.exec(route)
      if (version) return json({ ...SCRIPT.versions[0], number: Number(version[2]), source: SOURCE })
      if (route === '/api/ta/rules') {
        return json({ revision: 'r1', rules: [RULE], indices: ['us100'], instruments: [{ epic: 'US100', label: 'us100' }], engine_history_days: 1 })
      }
      if (route === '/api/data/assets') {
        return json({ timezone: 'Europe/Paris', seen: 'all', items: ASSETS, truncated: false, max: 3000, saxo_error: null })
      }
      const bar = /^\/api\/data\/assets\/(capital|saxo|dukascopy)\/([^/]+)\/bars$/.exec(route)
      if (bar) {
        const interval = Number(url.searchParams.get('interval'))
        const instrument = ASSETS.find((entry) => entry.ref === `${bar[1]}/${bar[2]}`)
        return json({
          instrument, from: url.searchParams.get('from'), to: url.searchParams.get('to'), timezone: 'Europe/Paris', interval, columns: [],
          bars: bars(bar[1] === 'saxo' ? 10.4 : 24850, interval),
        })
      }
      return json({ detail: `Not served in this test: ${method} ${route}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (element: React.ReactNode) => (
    <WithTrail>
      {element}
      <Address />
    </WithTrail>
  )
  const router = createMemoryRouter(
    [
      { path: '/backtests', element: page(<BacktestsPage />) },
      { path: '/backtests/new', element: page(<NewBacktestPage />) },
      { path: '/backtests/compare', element: page(<ComparePage />) },
      { path: '/backtests/experiments', element: page(<ExperimentsPage />) },
      { path: '/backtests/:runId', element: page(<RunPage />) },
      { path: '/backtests/:runId/days/:day', element: page(<DayPage />) },
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

const address = () => screen.getByRole('status', { name: 'address' }).textContent

beforeEach(() => {
  resetCharts()
  sweeps = {}
  edges = {}
  reconciliations = {}
  options = OPTIONS
  turboModel = { ...turboModel, state: 'idle', asked: null, measure: null }
  research = { ...research, enabled: false, keep_days: 30, state: 'off' }
  runs = {
    [RUN_ID]: run(),
    '20261019-210000-bbbbbb': run({
      id: '20261019-210000-bbbbbb',
      name: 'Stop at 10',
      changes: { stoploss_percent: -10 },
      settings: { settings: { ...SETTINGS, stoploss_percent: -10 }, changed: { stoploss_percent: -10 }, live_before: { stoploss_percent: -15 } },
    }),
  }
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the list of backtests', () => {
  it('lists each run with what it ran and what it made, and compares two of them', async () => {
    const user = userEvent.setup()
    renderAt('/backtests')

    expect(await screen.findByRole('link', { name: 'Smoke EMA cross' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    expect(screen.getAllByText('EMA cross v1 · Capital.com US100 · 1 min')).toHaveLength(2)
    expect(screen.getAllByText('too few to conclude')).toHaveLength(2)
    expect(screen.getByText('2 runs take 52.0 kB on disk.')).toBeInTheDocument()

    // The basket comes with a first run chosen. Nowhere to go before two are; then a link, which can be opened in another tab.
    expect(screen.queryByRole('toolbar', { name: 'Bulk actions toolbar' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Select Smoke EMA cross' }))
    const basket = within(await screen.findByRole('toolbar', { name: 'Bulk actions toolbar' }))
    expect(basket.getByText('run selected')).toBeInTheDocument()
    expect(basket.getByRole('button', { name: 'Compare' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select Stop at 10' }))
    expect(basket.getByText('runs selected')).toBeInTheDocument()
    expect(basket.getByRole('link', { name: 'Compare 2' })).toHaveAttribute('href', `/backtests/compare?runs=${RUN_ID},20261019-210000-bbbbbb`)
    expect(screen.getByRole('link', { name: 'New backtest' })).toHaveAttribute('href', '/backtests/new')
    await user.click(basket.getByRole('link', { name: 'Compare 2' }))
    expect(address()).toBe(`/backtests/compare?runs=${RUN_ID},20261019-210000-bbbbbb`)
  })

  it('selects every run that can be, empties the selection, and removes what is selected once asked', async () => {
    const user = userEvent.setup()
    runs = { ...runs, '20261019-220000-dddddd': run({ id: '20261019-220000-dddddd', name: 'Still running', status: { state: 'running' } }) }
    renderAt('/backtests')

    // A run that waits or runs can neither be compared nor removed: no box.
    expect(await screen.findByRole('link', { name: 'Still running' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Select Still running' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Select Stop at 10' }))
    const basket = within(await screen.findByRole('toolbar', { name: 'Bulk actions toolbar' }))
    await user.click(basket.getByRole('button', { name: 'All (2)' }))
    expect(screen.getByRole('checkbox', { name: 'Select Smoke EMA cross' })).toBeChecked()
    expect(basket.getByRole('button', { name: 'Select none' })).toBeInTheDocument()
    await user.click(basket.getByRole('button', { name: 'Clear selection' }))
    await waitFor(() => expect(screen.queryByRole('toolbar', { name: 'Bulk actions toolbar' })).not.toBeInTheDocument())
    expect(screen.getByRole('checkbox', { name: 'Select Stop at 10' })).not.toBeChecked()

    await user.click(screen.getByRole('checkbox', { name: 'Select Stop at 10' }))
    await user.click(within(await screen.findByRole('toolbar', { name: 'Bulk actions toolbar' })).getByRole('button', { name: 'Remove 1' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove 1 backtest?' })
    expect(within(dialog).getByText('Stop at 10')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/backtests/remove')?.body).toEqual({
      ids: ['20261019-210000-bbbbbb'],
    }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove 1 backtest?' })).not.toBeInTheDocument())
  })

  it('narrows the list as its address says, and keeps what is chosen in the address', async () => {
    const user = userEvent.setup()
    runs = { ...runs, [RUN_ID]: run({ made_by: 'claude', experiment: '1a2b3c' }) }
    renderAt('/backtests?by=claude')

    expect(await screen.findByRole('link', { name: 'Smoke EMA cross' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Stop at 10' })).not.toBeInTheDocument()
    const narrow = screen.getByRole('group', { name: 'Narrow the list' })
    expect(within(narrow).getByRole('combobox', { name: 'Made by' })).toHaveValue('claude')

    await user.selectOptions(within(narrow).getByRole('combobox', { name: 'Made by' }), 'me')
    expect(address()).toBe('/backtests?by=me')
    expect(await screen.findByRole('link', { name: 'Stop at 10' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Smoke EMA cross' })).not.toBeInTheDocument()
    // Every run again: a link to the list whole.
    expect(within(narrow).getByRole('link', { name: 'Every run' })).toHaveAttribute('href', '/backtests')
  })

  it('folds the failed attempts of a run under it, and removes them when asked', async () => {
    const user = userEvent.setup()
    const attempt = (id: string) =>
      run({ id, status: { state: 'failed', created_at: '2026-10-19T20:30:00Z', error: 'Traceback\nThe backtest failed: stopped by the kernel' }, summary: null, headline: null })
    runs = { ...runs, '20261019-203000-cccc01': attempt('20261019-203000-cccc01'), '20261019-202000-cccc02': attempt('20261019-202000-cccc02') }
    renderAt('/backtests')

    const row = (await screen.findByRole('link', { name: 'Smoke EMA cross' })).closest('tr') as HTMLElement
    expect(within(row).getByText('2 earlier failed attempts')).toBeInTheDocument()
    expect(within(row).getAllByText('The backtest failed: stopped by the kernel')).toHaveLength(2)
    await user.click(within(row).getByText('2 earlier failed attempts'))
    await user.click(within(row).getByRole('button', { name: 'Remove them' }))

    const dialog = await screen.findByRole('dialog', { name: 'Remove 2 backtests?' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/backtests/remove')?.body).toEqual({
      ids: ['20261019-203000-cccc01', '20261019-202000-cccc02'],
    }))
  })

  it("marks Claude's runs, and leads to its experiments", async () => {
    const user = userEvent.setup()
    runs = { ...runs, [RUN_ID]: run({ made_by: 'claude', experiment: '1a2b3c' }) }
    renderAt('/backtests')

    const claudes = (await screen.findByRole('link', { name: 'Smoke EMA cross' })).closest('tr') as HTMLElement
    const operators = screen.getByRole('link', { name: 'Stop at 10' }).closest('tr') as HTMLElement
    expect(within(claudes).getByText('Claude')).toBeInTheDocument()
    expect(within(operators).queryByText('Claude')).toBeNull()
    // Claude's runs alone, through the search.
    await user.type(screen.getByPlaceholderText('Search…'), 'Claude')
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Stop at 10' })).toBeNull())
    expect(screen.getByRole('link', { name: 'Smoke EMA cross' })).toBeInTheDocument()
    await user.clear(screen.getByPlaceholderText('Search…'))
    await user.click(screen.getByRole('link', { name: 'Experiments' }))
    expect(address()).toBe('/backtests/experiments')
    expect(await screen.findByText('No experiment yet')).toBeInTheDocument()
  })

  it('switches on the files written each night for a research machine', async () => {
    const user = userEvent.setup()
    renderAt('/backtests')

    expect(await screen.findByText(/Each finished day can be written as files for a research machine/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Write them' }))

    expect(await screen.findByText(/Research files on: No day written yet; the last 30 are kept\. Days not written yet wait/)).toBeInTheDocument()
    expect(sent.find((request) => request.method === 'PUT' && request.path === '/api/backtests/research')?.body).toEqual({ enabled: true })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Days of research files kept on the server' }), '90')
    await waitFor(() => expect(sent.filter((request) => request.method === 'PUT').at(-1)?.body).toEqual({ keep_days: 90 }))
  })

  it('measures the turbos made up from the index on the recorded days', async () => {
    const user = userEvent.setup()
    renderAt('/backtests')

    expect(await screen.findByText(/^Not measured yet/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Measure them' }))

    const asked = sent.find((request) => request.method === 'POST' && request.path === '/api/backtests/turbo-model')?.body as { from: string; to: string }
    // The last thirty days.
    expect((Date.parse(asked.to) - Date.parse(asked.from)) / 86_400_000).toBe(29)
    expect(await screen.findByText(/^Measuring the turbos of/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Measure them' })).toBeDisabled()
  })

  it('shows what the turbos were measured on and how well the model prices them', async () => {
    turboModel = { ...turboModel, state: 'done', measure: MEASURE }
    renderAt('/backtests')

    const table = await screen.findByRole('table', { name: 'The measure of each direction' })
    expect(within(table).getByText('0.00860')).toBeInTheDocument()
    expect(within(table).getByText('0.020 € (1.2 %)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Measure them again' })).toBeEnabled()
  })

  it('removes what the former backtester left, once asked', async () => {
    const user = userEvent.setup()
    renderAt('/backtests')

    expect(await screen.findByText(/The former backtester left 7 runs \(3.4 MB\)/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove them' }))
    const dialog = await screen.findByRole('dialog', { name: "Remove the former backtester's runs?" })
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(sent.some((request) => request.method === 'DELETE' && request.path === '/api/backtests/former')).toBe(true))
  })
})

describe('a new backtest', () => {
  it('goes through the steps and asks for the run the form describes', async () => {
    const user = userEvent.setup()
    renderAt('/backtests/new')

    // 1. The asset: Capital.com's first; a turbo is not offered.
    const assetField = await screen.findByRole('combobox', { name: 'Asset' })
    expect(assetField).toHaveTextContent('Capital.com · US Tech 100')
    await user.click(assetField)
    expect(await screen.findByRole('option', { name: 'Dukascopy · US Tech 100 (Dukascopy)' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /Turbo Short/ })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 2. The strategy: the script's alerts made signals.
    expect(await screen.findByText('Cross up')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'What “Cross up” becomes' }), 'long')
    await user.selectOptions(screen.getByRole('combobox', { name: 'What “Cross down” becomes' }), 'short')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 3. The period, and what was recorded of it.
    // The last seven days by default, of which the server says three were recorded.
    expect(await screen.findByText('3 days of the 7 can be traded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 4. A setting changed from the live one, and the fills WATA's own orders had.
    // Each step comes in once the one before has gone.
    const stop = await screen.findByRole('spinbutton', { name: /^Stop-loss/ })
    await user.clear(stop)
    await user.type(stop, '-10')
    expect(screen.getByText(/Live: -15 %/)).toBeInTheDocument()
    // The account: the backtests' own, the live one a click away.
    expect(screen.getByText(/The backtest’s default; the live account holds €21\.82/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'start from it' }))
    expect(screen.getByRole('spinbutton', { name: /^Account at the start/ })).toHaveValue(21.82)
    expect(await screen.findByText(/a signal reached the trader 2\.6 s after its time/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Use the measured values' }))
    expect(screen.getByRole('button', { name: 'Measured values in use' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // 5. The review, and the request.
    expect(await screen.findByText('-10 %')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Name (optional)' }), 'EMA, stop at 10')
    await user.click(screen.getByRole('button', { name: 'Start the backtest' }))

    await waitFor(() => expect(address()).toBe('/backtests/20261019-230000-cccccc'))
    const asked = sent.find((request) => request.method === 'POST' && request.path === '/api/backtests')?.body as Record<string, unknown>
    expect(asked).toMatchObject({
      name: 'EMA, stop at 10',
      indice: 'us100',
      source: {
        kind: 'pine',
        script: { id: SCRIPT.id, version: 1 },
        asset: { provider: 'capital', key: 'US100' },
        bars: { type: 'time', timeframe: '1' },
        alerts: [
          { title: 'Cross up', action: 'long' },
          { title: 'Cross down', action: 'short' },
        ],
      },
      changes: { stoploss_percent: -10, capital: 21.82, signal_delay_seconds: 2.6, order_seconds: 0.4, slippage: 0.005 },
      baseline: { repetitions: 100, seed: 1 },
    })
  })

  it('runs on bars of seconds, of the menu or of a length typed', async () => {
    const user = userEvent.setup()
    renderAt('/backtests/new')

    const bars = await screen.findByRole('combobox', { name: 'Bars' })
    expect(within(bars).getAllByRole('option').map((option) => option.textContent)).toEqual([
      '10 s', '20 s', '30 s', '45 s', '1 min', '5 min', '15 min', '1 hour', 'Other length…', 'Range bars',
    ])
    await user.selectOptions(bars, '45S')
    expect(screen.getByText(/45 s is no multiple of the ten seconds WATA records/)).toBeInTheDocument()

    await user.selectOptions(bars, 'typed')
    const length = screen.getByRole('textbox', { name: 'Length of the bars' })
    await user.clear(length)
    await user.type(length, '5s')
    expect(screen.getByText(/WATA records ten-second bars: 10 seconds at least\./)).toBeInTheDocument()
    expect(length).toHaveAttribute('aria-invalid', 'true')
    await user.clear(length)
    // "1" on the way is a length of the menu: the field stays.
    await user.type(length, '15s')
    expect(screen.getByText('Bars of 15 s.')).toBeInTheDocument()
    expect(screen.getByText(/each bar holds the ten-second bars that begin in it, 10 or 20 s of prices/)).toBeInTheDocument()
    await user.clear(length)
    await user.type(length, '90s')
    expect(screen.getByText('Bars of 90 s.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('Cross up')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'What “Cross up” becomes' }), 'long')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('3 days of the 7 can be traded.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('spinbutton', { name: /^Stop-loss/ })
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: 'Start the backtest' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/backtests')).toBe(true))
    const asked = sent.find((request) => request.method === 'POST' && request.path === '/api/backtests')?.body as Record<string, unknown>
    expect(asked).toMatchObject({ source: { kind: 'pine', bars: { type: 'time', timeframe: '90S' } } })
  })

  it('trades on turbos made up from the index once they are measured', async () => {
    options = { ...OPTIONS, limits: { ...OPTIONS.limits, modelled_period_days: 731 }, turbo_model: MEASURE }
    const user = userEvent.setup()
    renderAt('/backtests/new')

    await user.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Cross up')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'What “Cross up” becomes' }), 'long')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await user.click(await screen.findByRole('button', { name: 'Made up from the index' }))
    expect(screen.getByText(/731 at most\. Every day the index has prices for is traded\./)).toBeInTheDocument()
    expect(screen.queryByText(/can be traded\./)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(await screen.findByRole('button', { name: 'Next' }))

    expect(await screen.findByText('us100, made up from Dukascopy us100')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start the backtest' }))
    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/backtests')).toBe(true))
    expect(sent.find((request) => request.method === 'POST' && request.path === '/api/backtests')?.body).toMatchObject({ turbos: 'modelled' })
  })

  it('cannot make turbos up before they are measured', async () => {
    const user = userEvent.setup()
    renderAt('/backtests/new')

    await user.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Cross up')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('button', { name: 'Made up from the index' })).toBeDisabled()
  })

  it('starts from the script its page asked for, at the version asked', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/new?script=${OTHER.id}&version=1`)

    await user.click(await screen.findByRole('button', { name: 'Next' }))
    expect(await screen.findByRole('combobox', { name: 'Script' })).toHaveValue(OTHER.id)
    expect(screen.getByRole('combobox', { name: 'Version' })).toHaveValue('1')
  })

  it('starts from a signal rule', async () => {
    renderAt(`/backtests/new?rule=${RULE.id}`)

    expect(await screen.findByRole('combobox', { name: 'Signal rule to start from' })).toHaveValue(RULE.id)
    expect(screen.getByRole('combobox', { name: 'Asset' })).toHaveTextContent('Capital.com · US Tech 100')
  })

  it('finds an asset by what is typed, its symbol too', async () => {
    const user = userEvent.setup()
    renderAt('/backtests/new')

    await user.click(await screen.findByRole('combobox', { name: 'Asset' }))
    // Typed into the field's own search: "nasdaq" is the recorder's index alone.
    await user.keyboard('nasdaq')
    expect(within(screen.getByRole('listbox')).getAllByRole('option').map((option) => option.textContent)).toEqual(['Saxo · NASDAQ 100'])
    await user.keyboard('{Enter}')

    expect(screen.getByRole('combobox', { name: 'Asset' })).toHaveTextContent('Saxo · NASDAQ 100')
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument())
  })
})

describe('a run', () => {
  it('shows what it made, how far to trust it, and its trades linked to their day', async () => {
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByRole('heading', { name: 'Smoke EMA cross' })).toBeInTheDocument()
    expect(screen.getByText('+5.21%')).toBeInTheDocument()
    expect(screen.getByText(/too few to conclude/)).toBeInTheDocument()
    expect(screen.getByText('of the random runs did worse than this one')).toBeInTheDocument()
    expect(screen.getAllByText('85%').length).toBeGreaterThan(0)
    expect(await screen.findByRole('link', { name: '#1' })).toHaveAttribute('href', `/backtests/${RUN_ID}/days/2026-10-14?trade=1`)
    // Its script, and the rule it can become: pages of their own.
    expect(screen.getByRole('link', { name: 'EMA cross' })).toHaveAttribute('href', `/ta/indicators/${SCRIPT.id}`)
    expect(screen.getByRole('link', { name: 'Make a shadow rule' })).toHaveAttribute('href', `/ta/signals?from-run=${RUN_ID}`)
  })

  it('keeps its table in the address, each tab and its run again a link', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)
    expect(await screen.findByRole('link', { name: '#1' })).toBeInTheDocument()

    expect(screen.getByRole('link', { name: 'Run again with changes' })).toHaveAttribute('href', `/backtests/new?again=${RUN_ID}`)
    // The tabs of the panel of tables (the index of the page names that panel too).
    const tables = () => within(document.getElementById('tables') as HTMLElement)
    const signals = tables().getByRole('link', { name: /^Signals/ })
    expect(signals).toHaveAttribute('href', `/backtests/${RUN_ID}?tab=signals`)
    expect(tables().getByRole('link', { name: /^Trades/ })).toHaveAttribute('aria-current', 'page')

    await user.click(signals)
    expect(address()).toBe(`/backtests/${RUN_ID}?tab=signals`)
    expect(screen.queryByRole('link', { name: '#1' })).not.toBeInTheDocument()
    expect(tables().getByRole('link', { name: /^Signals/ })).toHaveAttribute('aria-current', 'page')
    await user.click(tables().getByRole('link', { name: /^Trades/ }))
    expect(address()).toBe(`/backtests/${RUN_ID}`)
  })

  it('says whether it can be believed, each check leading to its part of the page', async () => {
    renderAt(`/backtests/${RUN_ID}`)

    const verdict = (await screen.findByText('Can it be believed?')).closest('section') as HTMLElement
    // Three trades over three days, 85 % of the random runs beaten: under the bar Claude's research is held to.
    expect(within(verdict).getByText('Not to be believed yet: enough trades and days, beats random entries.')).toBeInTheDocument()
    expect(within(verdict).getAllByLabelText('Failed')).toHaveLength(2)
    expect(within(verdict).getByRole('link', { name: 'Enough trades and days' })).toHaveAttribute('href', '#trust')
    expect(within(verdict).getByRole('link', { name: 'An edge in its entries' })).toHaveAttribute('href', '#edge')
    expect(within(verdict).getByText('Not stressed yet.')).toBeInTheDocument()

    // The parts of the page, numbered, each an anchor the nav leads to.
    const nav = within(screen.getByRole('navigation', { name: 'Parts of this page' }))
    const parts = nav.getAllByRole('link').map((link) => [link.textContent, link.getAttribute('href')])
    expect(parts).toEqual([
      ['01Verdict', '#verdict'], ['02Trust', '#trust'], ['03Edge', '#edge'], ['04Stress', '#stress'], ['05Made-up turbos', '#model-check'],
      ['06Day after day', '#curves'], ['07Why', '#why'], ['08Where it comes from', '#breakdowns'], ['09Trades, signals, days', '#tables'],
      ['10What it ran', '#setup'],
    ])
    for (const [, href] of parts) expect(document.getElementById((href as string).slice(1))).not.toBeNull()
    // Named by the part alone; the first is the current one until the page scrolls.
    expect(nav.getByRole('link', { name: 'Verdict' })).toHaveAttribute('aria-current', 'location')
  })

  it('goes to a part from the nav, which says it is there and puts it in the address in place', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)

    const nav = within(await screen.findByRole('navigation', { name: 'Parts of this page' }))
    const before = window.history.length
    await user.click(nav.getByRole('link', { name: 'Edge' }))

    expect(nav.getByRole('link', { name: 'Edge' })).toHaveAttribute('aria-current', 'location')
    expect(nav.getByRole('link', { name: 'Verdict' })).not.toHaveAttribute('aria-current')
    expect(window.location.hash).toBe('#edge')
    expect(window.history.length).toBe(before)
    expect(address()).toBe(`/backtests/${RUN_ID}`)
  })

  it('says the risk the days show, and what they give without the best of them', async () => {
    renderAt(`/backtests/${RUN_ID}`)

    const risk = await screen.findByRole('region', { name: 'Risk, day by day' })
    expect(within(risk).getByText('A winner kept of its best').nextSibling).toHaveTextContent('61%')
    expect(within(risk).getByText('3 days traded: under 20, nothing is brought to a year.')).toBeInTheDocument()
    expect(screen.getByText('Without its best day: +3.45%; best two: +1.70%.')).toBeInTheDocument()
  })

  it('says whether its entries move the asset their way more than random moments do', async () => {
    const row = (minutes: number, mean: number, beaten: number) => ({
      minutes, signals: 48, mean_bp: mean, median_bp: mean, hit_rate: 56, best_bp: 8, worst_bp: -6, independent: 30, t: 2.6,
      random: { mean_bp: 0.2, hit_rate: 50, beaten, draws: 20 },
    })
    const group = (key: string, value: unknown) => ({ [key]: value, '1': { signals: 24, mean_bp: 1, hit_rate: 50 }, '15': { signals: 24, mean_bp: 4, hit_rate: 60 } })
    edges[RUN_ID] = {
      horizons: [row(1, 0.4, 70), row(15, 3.5, 99)], by_hour: [group('hour', 15)], by_direction: [group('direction', 'long')],
      by_fate: [group('fate', 'executed'), group('fate', 'not_taken')], unpriced: 2, draws: 20, seed: 1,
    } as BacktestEdge
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText(/After 15 min the asset had moved \+3\.50 bp/)).toBeInTheDocument()
    expect(screen.getByText('2 signals came when the asset had no price: left out.')).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Moves split' })).getByText('Not taken')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hour' }))
    expect(within(screen.getByRole('table', { name: 'Moves split' })).getByText('15:00')).toBeInTheDocument()
  })

  it('replaying the journal, is set beside the positions WATA really had', async () => {
    runs[RUN_ID] = run({ request: { ...run().request, source: { kind: 'journal', signals: 12, entries: 9 } } })
    reconciliations[RUN_ID] = {
      summary: {
        paired: 1, backtest_only: 0, live_only: 1, backtest_only_live_outcomes: {}, live_only_backtest_fates: { refused: 1 },
        mean_result_difference: 0.4, median_result_difference: 0.4, correlation: null, same_exit_percent: 0, same_turbo_percent: 0,
        median_entry_seconds: 1.2, backtest_total_percent: 2.4, real_total_percent: 2.0,
      },
      pairs: [{
        signal_uuid: 'a', trade_id: 1, position_id: 'p-a', day: '2026-10-14', direction: 'long', entry_seconds: 1.2, exit_seconds: 0.5,
        same_turbo: false, entry_price: { backtest: 5.04, real: 5.02 }, exit_price: { backtest: 5.16, real: 5.12 },
        result: { backtest: 2.4, real: 2.0 }, exit: { backtest: 'Explicit Close (All)', real: 'Performance (Takeprofit (60%) hit at 61%)' },
        same_exit: false,
      }],
      backtest_only: [],
      live_only: [{ signal_uuid: 'b', position_id: 'p-b', backtest_fate: 'refused' }],
    }
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText(/^1 trade set beside WATA’s positions of the same signals/)).toBeInTheDocument()
    expect(screen.getByText('Only WATA traded 1: 1 refused by the backtest’s rules.')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: "Trades beside WATA's positions" })
    expect(within(table).getByText('another turbo')).toBeInTheDocument()
    expect(within(table).getByText('5.04 / 5.02')).toBeInTheDocument()
  })

  it('says when its edge was not measured', async () => {
    renderAt(`/backtests/${RUN_ID}`)
    expect(await screen.findByText('Not measured: this run was made before the edge of its entries was.')).toBeInTheDocument()
  })

  it('is stressed with later and dearer fills, and shows what they leave of it', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText('Not stressed yet: sixteen combinations, one replay of the script.')).toBeInTheDocument()
    runs[STRESS_ID] = run({ id: STRESS_ID, kind: 'sweep', name: 'Stress: Smoke EMA cross', stress_of: RUN_ID, status: { state: 'queued' }, summary: null })
    await user.click(screen.getByRole('button', { name: 'Stress this run' }))

    expect(sent.some((request) => request.method === 'POST' && request.path === `/api/backtests/${RUN_ID}/stress`)).toBe(true)
    expect(await screen.findByRole('link', { name: 'Stress: Smoke EMA cross' })).toHaveAttribute('href', `/backtests/${STRESS_ID}`)
    expect(screen.getByRole('button', { name: 'Stress it again' })).toBeDisabled()
  })

  it('offers no model check before the turbos are measured', async () => {
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText('The turbos are not measured yet: measure them on the Backtests page first.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check the turbo model' })).toBeDisabled()
  })

  it('makes a model check of a recorded run, and says what its trades show', async () => {
    options = { ...OPTIONS, turbo_model: MEASURE }
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText('Not checked yet: one run of the same signals on made-up turbos.')).toBeInTheDocument()
    runs[CHECK_ID] = run({ id: CHECK_ID, name: 'Model check: Smoke EMA cross', model_check_of: RUN_ID, turbos: 'modelled', status: { state: 'queued' }, summary: null })
    await user.click(screen.getByRole('button', { name: 'Check the turbo model' }))

    expect(sent.some((request) => request.method === 'POST' && request.path === `/api/backtests/${RUN_ID}/model-check`)).toBe(true)
    expect(await screen.findByRole('link', { name: 'Model check: Smoke EMA cross' })).toHaveAttribute('href', `/backtests/${CHECK_ID}`)
    expect(screen.getByRole('button', { name: 'Check it again' })).toBeDisabled()
  })

  it('shows on a model check what its trades show beside the recorded ones', async () => {
    runs[CHECK_ID] = run({
      id: CHECK_ID, name: 'Model check: Smoke EMA cross', model_check_of: RUN_ID, turbos: 'modelled',
      summary: { ...run().summary!, model_check: MODEL_CHECK },
    })
    renderAt(`/backtests/${CHECK_ID}`)

    expect(await screen.findByText('31 trades paired by signal (34 on the recorded turbos, 41 on made-up ones).')).toBeInTheDocument()
    expect(screen.getByText('Model check')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'The run it checks' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    expect(screen.queryByRole('button', { name: 'Check the turbo model' })).not.toBeInTheDocument()
  })

  it('places its trades by the weekday and the hour they were bought, at once, as a heatmap', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}`)

    const choices = within(await screen.findByRole('group', { name: 'Trades grouped by' }))
    const both = choices.getByRole('button', { name: 'Weekday and hour' })
    // Beside the two it puts together.
    const labels = choices.getAllByRole('button').map((button) => button.textContent)
    expect(labels.slice(0, 3)).toEqual(['Hour of entry', 'Weekday', 'Weekday and hour'])
    await user.click(both)

    expect(both).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('img', { name: /^Profit and loss of the run's trades by weekday and hour of entry\./ })).toBeInTheDocument()
    expect(screen.getByText('+€3.00 over 3 trades')).toBeInTheDocument()
    expect(screen.getByRole('rowheader', { name: 'Wednesday, 15:00' })).toBeInTheDocument()
  })

  it('follows a run while it waits its turn', async () => {
    runs[RUN_ID] = run({ status: { state: 'queued', created_at: '2026-10-19T20:33:49Z' }, position: 2, summary: null, headline: null })
    renderAt(`/backtests/${RUN_ID}`)

    expect(await screen.findByText('One backtest runs at a time: this one is number 2 in line.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop' })).toBeInTheDocument()
  })
})

function figures(total: number) {
  return { total_percent: total, trades: 3, days: 3, expectancy: total / 3, win_rate: 50, worst_drawdown: -1 } as NonNullable<BacktestSweep['cells'][number]['whole']>
}

describe('a sweep', () => {
  const later = [3, 5, 8, 18]
  const dearer = [0, 0.01, 0.02, 0.05]

  it('made of a run with later and dearer fills says what is left of the run, beside it', async () => {
    runs[STRESS_ID] = run({
      id: STRESS_ID, kind: 'sweep', name: 'Stress: Smoke EMA cross', stress_of: RUN_ID,
      summary: { made_with: run().summary!.made_with, days: { asked: 3, tested: 3, by_status: { tested: 3 } }, cells: 16, warnings: [] },
    })
    sweeps[STRESS_ID] = {
      axes: [{ kind: 'setting', name: 'order_seconds', values: later }, { kind: 'setting', name: 'slippage', values: dearer }],
      held_out: 0.3,
      held_out_from: '2026-10-16',
      cells: later.flatMap((seconds, row) =>
        dearer.map((price) => {
          const total = Math.round((5.21 - row - 40 * price) * 100) / 100
          return { values: [seconds, price], tuning: figures(total), held_out: figures(total), whole: figures(total), money: run().summary!.money!, signals: run().summary!.signals! }
        }),
      ),
    }
    renderAt(`/backtests/${STRESS_ID}`)

    expect(await screen.findByText(/As it was run: \+5\.21%\. Each order 2 s later and 0\.01 € dearer a turbo: \+3\.81%\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'The run it stresses' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    expect(screen.getByText('Order to fill (s) ↓ · Worse price a turbo (€) →')).toBeInTheDocument()
    // Nothing is chosen among worse fills.
    expect(screen.queryByText('Would choosing have worked')).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Days shown' })).not.toBeInTheDocument()

    // And the run it stresses shows the same, its verdict too.
    renderAt(`/backtests/${RUN_ID}`)
    expect(await screen.findByRole('link', { name: 'Stress: Smoke EMA cross' })).toBeInTheDocument()
    expect(await screen.findAllByText(/As it was run: \+5\.21%/)).toHaveLength(2)
    expect(screen.getAllByText('Still above zero with each order a step later and dearer.').length).toBeGreaterThan(0)
  })

  it('says whether choosing would have worked: walk-forward and overfitting', async () => {
    const days = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15']
    runs[STRESS_ID] = run({
      id: STRESS_ID, kind: 'sweep', name: 'EMA lengths',
      summary: {
        made_with: run().summary!.made_with, days: { asked: 4, tested: 4, by_status: { tested: 4 } }, cells: 2, warnings: [],
        walk_forward: {
          train_days: 2, test_days: 1, days: 2, total_percent: -0.5, worst_day: -1.5, worst_drawdown: -1.5, changes: 1,
          best_in_hindsight_percent: 3.03, curve: [['2026-10-14', 1], ['2026-10-15', -0.52]],
          windows: [
            { train: ['2026-10-12', '2026-10-13'], test: ['2026-10-14', '2026-10-14'], cell: 0, values: [8], train_percent: 4.04, test_percent: 1, test_rank: 2, cells: 2 },
            { train: ['2026-10-13', '2026-10-14'], test: ['2026-10-15', '2026-10-15'], cell: 1, values: [12], train_percent: 2.5, test_percent: -1.5, test_rank: 2, cells: 2 },
          ],
        },
        pbo: { probability: 0.71, splits: 8, combinations: 70, sentence: 'Choosing the best was fitting 71 % of the time: the choice is mostly fitting.' },
        deflated_sharpe: null,
      },
    })
    sweeps[STRESS_ID] = {
      axes: [{ kind: 'input', name: 'in_0', values: [8, 12] }],
      held_out: 0.3,
      held_out_from: '2026-10-15',
      cells: [8, 12].map((length, index) => ({
        values: [length], tuning: figures(2), held_out: figures(-1), whole: figures(1), money: run().summary!.money!, signals: run().summary!.signals!,
        curve: days.map((entry, number) => [entry, index === 0 ? [2, 2, 1, -2][number] : [1, 1.5, 2, 1][number], 1] as [string, number, number]),
      })),
    }
    renderAt(`/backtests/${STRESS_ID}`)

    expect(await screen.findByText('Would choosing have worked')).toBeInTheDocument()
    expect(screen.getByText(/the choice is mostly fitting/)).toBeInTheDocument()
    const walk = await screen.findByRole('region', { name: 'Walk-forward' })
    expect(within(walk).getByText(/again every 1 day, made −0\.50% over the 2 days after each choice/)).toBeInTheDocument()
    expect(within(walk).getByText('12 Oct to 13 Oct')).toBeInTheDocument()
    expect(within(walk).getAllByText('2 of 2')).toHaveLength(2)
    expect(within(walk).getByText('The best, known afterwards')).toBeInTheDocument()
  })
})

describe('a day of a run', () => {
  it('draws the asset of the day with its signals and trades, under the run in the trail', async () => {
    renderAt(`/backtests/${RUN_ID}/days/2026-10-14`)

    expect(await screen.findByRole('heading', { name: '14 Oct 2026' })).toBeInTheDocument()
    const trail = within(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0])
    expect(await trail.findByRole('link', { name: 'Smoke EMA cross' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)

    await waitFor(() => expect(charts.filter((chart) => !chart.removed)).toHaveLength(1))
    const candles = seriesOf(charts[0], 'Candlestick')!
    await waitFor(() => expect(candles.markers.length).toBeGreaterThan(0))
    expect(candles.markers.map((marker) => marker.text)).toEqual(['Short', '#1 short', '+1.72 %', 'Long ✕'])
    // Minute bars of the asset, as the strategy read them.
    expect(sent.some((request) => request.path.startsWith('/api/data/assets/capital/US100/bars?from=2026-10-14&to=2026-10-14&interval=60'))).toBe(true)
    // The first day has none before it; the next one is a link.
    expect(screen.getByRole('button', { name: 'Day before' })).toBeDisabled()
    // The 16th had no turbo recorded: the day after the 14th that was traded is the 15th.
    await waitFor(() => expect(screen.getByRole('link', { name: 'Day after' })).toHaveAttribute('href', `/backtests/${RUN_ID}/days/2026-10-15`))
  })

  it('draws the day again in another theme, its chart drawn with the colours it read when it was made', async () => {
    const system = stubSystemTheme(true)
    renderAt(`/backtests/${RUN_ID}/days/2026-10-14`)
    await waitFor(() => expect(charts.filter((chart) => !chart.removed)).toHaveLength(1))
    const first = charts[0]
    await waitFor(() => expect(seriesOf(first, 'Candlestick')!.markers).toHaveLength(4))

    act(() => system.set(false))

    expect(first.removed).toBe(true)
    await waitFor(() => {
      const drawn = charts.filter((chart) => !chart.removed)
      expect(drawn).toHaveLength(1)
      expect(seriesOf(drawn[0], 'Candlestick')!.markers.map((marker) => marker.text)).toEqual(['Short', '#1 short', '+1.72 %', 'Long ✕'])
    })
  })

  it('opens a trade on the chart of its turbo, with the turbos the trader looked at', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN_ID}/days/2026-10-14`)

    // A link to the day with the trade opened (another tab opens it so); here, a click opens it in place.
    const trade = await screen.findByRole('link', { name: '#1' })
    expect(trade).toHaveAttribute('href', `/backtests/${RUN_ID}/days/2026-10-14?trade=1`)
    await user.click(trade)
    expect(address()).toBe(`/backtests/${RUN_ID}/days/2026-10-14?trade=1`)
    expect(await screen.findByText('Trade #1: short on SG-NDX-S5')).toBeInTheDocument()
    expect(screen.getByText('the position monitor')).toBeInTheDocument()
    expect(screen.getByText('BNP-NDX-S6 BNP')).toBeInTheDocument()

    // The turbo's ten-second bid, its levels drawn over it, the strategy's asset under it.
    await waitFor(() => expect(charts.filter((chart) => !chart.removed)).toHaveLength(2))
    const turbo = charts.filter((chart) => !chart.removed)[1]
    await waitFor(() => expect(seriesOf(turbo, 'Candlestick')?.markers.map((marker) => marker.text)).toEqual(['Bought 10.48', 'Sold 10.66 (+1.72 %)']))
    expect(turbo.series.filter((series) => series.type === 'Line' && !series.removed).length).toBeGreaterThanOrEqual(4)
    expect(sent.some((request) => request.path.startsWith('/api/data/assets/saxo/5/bars?from=2026-10-14&to=2026-10-14&interval=10'))).toBe(true)

    // The same trade again closes it.
    await user.click(screen.getByRole('link', { name: '#1' }))
    expect(address()).toBe(`/backtests/${RUN_ID}/days/2026-10-14`)
    expect(screen.queryByText('Trade #1: short on SG-NDX-S5')).not.toBeInTheDocument()
  })
})

describe('runs compared', () => {
  it('sets their figures side by side with the settings they did not share', async () => {
    renderAt(`/backtests/compare?runs=${RUN_ID},20261019-210000-bbbbbb`)

    const table = await screen.findByRole('table')
    expect(within(table).getByRole('link', { name: 'Stop at 10' })).toHaveAttribute('href', '/backtests/20261019-210000-bbbbbb')
    const stopRow = await within(table).findByRole('row', { name: /Stop-loss/ })
    expect(within(stopRow).getByText('-15 %')).toBeInTheDocument()
    expect(within(stopRow).getByText('-10 %')).toBeInTheDocument()
    expect(within(screen.getByRole('list', { name: 'Legend' })).getByText('Stop at 10')).toBeInTheDocument()
  })

  it('keeps only what differs between them when asked, and finds a row by its name', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/compare?runs=${RUN_ID},20261019-210000-bbbbbb`)

    const table = await screen.findByRole('table', { name: 'The runs side by side' })
    // Each run a column, its total under its name.
    expect(within(table).getByRole('columnheader', { name: /Smoke EMA cross/ })).toBeInTheDocument()
    expect(within(table).getByRole('row', { name: /^Asset/ })).toBeInTheDocument()

    await user.click(within(table).getByRole('button', { name: 'Only what differs' }))
    // The same asset for both: gone; the stop-loss they did not share stays.
    await waitFor(() => expect(within(table).queryByRole('row', { name: /^Asset/ })).not.toBeInTheDocument())
    expect(within(table).getByRole('row', { name: /Stop-loss/ })).toBeInTheDocument()

    await user.click(within(table).getByRole('button', { name: 'Only what differs' }))
    await user.type(within(table).getByRole('textbox', { name: 'Find a row…' }), 'drawdown')
    await waitFor(() => expect(within(table).getAllByRole('row').filter((row) => row.getAttribute('role') === 'row' && row.textContent?.includes('Worst drawdown'))).toHaveLength(1))
    expect(within(table).queryByRole('row', { name: /^Trades/ })).not.toBeInTheDocument()
  })

  it('asks for two runs at least', async () => {
    renderAt(`/backtests/compare?runs=${RUN_ID}`)
    expect(await screen.findByText(/Tick two runs or more/)).toBeInTheDocument()
  })

  it('leaves out a run with no result', async () => {
    runs['20261019-210000-bbbbbb'] = { ...runs['20261019-210000-bbbbbb'], status: { state: 'failed' }, summary: null }
    runs['20261019-220000-dddddd'] = run({ id: '20261019-220000-dddddd', name: 'Third' })
    renderAt(`/backtests/compare?runs=${RUN_ID},20261019-210000-bbbbbb,20261019-220000-dddddd`)
    expect(await screen.findByText(/1 of the runs chosen has no result to compare/)).toBeInTheDocument()
  })
})

describe('an address that names no run', () => {
  it('says there is no such backtest, under the trail of the backtests, with the way to them', async () => {
    renderAt('/backtests/20261019-000000-ffffff')

    expect(await screen.findByRole('heading', { level: 1, name: 'No such backtest' })).toBeInTheDocument()
    expect(document.title).toBe('No such backtest · WATA Admin')
    const trail = within(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0])
    expect(trail.getByRole('link', { name: 'Backtests' })).toHaveAttribute('href', '/backtests')
    expect(trail.getByText('No such backtest')).toBeInTheDocument()
    const suggestions = within(screen.getByRole('navigation', { name: 'Helpful links' }))
    expect(suggestions.getByRole('link', { name: /^Backtests/ })).toHaveAttribute('href', '/backtests')
    expect(suggestions.getByRole('link', { name: /^New backtest/ })).toHaveAttribute('href', '/backtests/new')
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/')
  })

  it('says the same on a day of a run that is not there', async () => {
    renderAt('/backtests/20261019-000000-ffffff/days/2026-10-14')

    expect(await screen.findByRole('heading', { level: 1, name: 'No such backtest' })).toBeInTheDocument()
  })

  it('says there is no such day when the run did not cover it, under the run in the trail', async () => {
    renderAt(`/backtests/${RUN_ID}/days/2026-10-20`)

    expect(await screen.findByRole('heading', { level: 1, name: 'No such day' })).toBeInTheDocument()
    const trail = within(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0])
    expect(trail.getByRole('link', { name: 'Smoke EMA cross' })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    const suggestions = within(screen.getByRole('navigation', { name: 'Helpful links' }))
    expect(suggestions.getByRole('link', { name: /^Smoke EMA cross/ })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
  })
})
