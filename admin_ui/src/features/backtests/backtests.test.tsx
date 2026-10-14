import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type {
  BacktestBreakdowns,
  BacktestCalibration,
  BacktestComparison,
  BacktestDayChart,
  BacktestFigures,
  BacktestGroup,
  BacktestOptions,
  BacktestRun,
  BacktestRunListed,
  BacktestSweep,
  BacktestTradePath,
  PriceCoverage,
  PriceFetchStatus,
  PriceKeepStatus,
  Session,
} from '@/lib/types'
import { resetCharts, seriesOf, theChart } from '@/test/fakeCharts'

import { BacktestDayPage } from './BacktestDayPage'
import { BacktestFormPage } from './BacktestFormPage'
import { BacktestRunPage } from './BacktestRunPage'
import { BacktestsPage } from './BacktestsPage'
import { ComparePage } from './ComparePage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const SESSION: Session = {
  authenticated: true,
  version: '0.12.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const FIRST = '20261012-221947-d838d0'
const SECOND = '20261012-221950-751439'

const OPTIONS: BacktestOptions = {
  strategies: [
    {
      name: 'kernel_vwap',
      parameters: [
        { name: 'kernel_bandwidth', kind: 'integer', default: 15, help: 'Range bars the kernel looks back on.', least: 2, most: 500, choices: [], advanced: false },
        { name: 'session_start', kind: 'text', default: '08:00', help: 'Start of the session.', least: null, most: null, choices: [], advanced: false },
        { name: 'tick', kind: 'number', default: 0.1, help: 'Smallest step of the prices.', least: 0.01, most: 10, choices: [], advanced: true },
      ],
    },
  ],
  turbo: { entry_price: 4.4, spread: 0.03, leverages: [80, 110, 140], measured: false },
  rules: {
    live: { stoploss_percent: -15, day_profit_target: 1.7, trailing_enabled: true, timezone: 'Europe/Paris' },
    editable: [
      { name: 'stoploss_percent', kind: 'number', label: 'Stop-loss, in percent of the turbo', least: -100, most: -0.1 },
      { name: 'day_profit_target', kind: 'number', label: 'Daily target, in percent', least: 0.01, most: 10000 },
      { name: 'trailing_enabled', kind: 'boolean', label: 'Trailing stop', least: null, most: null },
    ],
  },
  execution: { capital: 10000, fill_delay: 0, slippage: 0 },
  limits: {
    period_days: 366, leverages: 5, queued: 10, fetch_days: 92, note_length: 200,
    fill_delay: 600, slippage: 100, sweep_cells: 64, sweep_values: 12, tags: 6, tag_length: 24,
  },
  held_out: { share: 0.3, least: 0.1, most: 0.5 },
  timezone: 'Europe/Paris',
}

const fetchStatus = (changes: Partial<PriceFetchStatus> = {}): PriceFetchStatus => ({ state: 'idle', lines: [], ...changes })

const keeping = (changes: Partial<PriceKeepStatus> = {}): PriceKeepStatus => ({
  enabled: false, days: 120, state: 'off', missing: 84, settled: 0, failures: 0, next_at: null, limits: { days: 730 },
  ...changes,
})

const coverage = (changes: Partial<PriceCoverage> = {}): PriceCoverage => ({
  from: '2026-09-14',
  to: '2026-09-18',
  label: 'us100',
  symbol: 'USATECHIDXUSD',
  days: [
    { day: '2026-09-14', state: 'incomplete', hours: 20, hours_expected: 24 },
    { day: '2026-09-15', state: 'ready', hours: 24, hours_expected: 24 },
    { day: '2026-09-16', state: 'missing', hours: 0, hours_expected: 24 },
    { day: '2026-09-17', state: 'ready', hours: 24, hours_expected: 24 },
    { day: '2026-09-19', state: 'closed', hours: 0, hours_expected: 0 },
  ],
  ready: { count: 2, first: '2026-09-15', last: '2026-09-17' },
  fetch: fetchStatus(),
  keep: keeping(),
  ...changes,
})

/** The figures of a run made before it was set against random entries. */
const figures = (trades: number, total: number): BacktestFigures => ({
  days: 3, days_with_an_entry: 2, trades, win_rate: 100, average_win: 1.7, average_loss: null,
  total_percent: total, best_day: 1.7, worst_day: 0, worst_drawdown: 0, index_points: 23.1,
  exits: { 'Daily profit target': trades }, entries: { cross: trades }, refusals: { 'Day profit reached': 11 },
})

/** What a run says of how far it can be trusted. */
const TRUST: Partial<BacktestFigures> = {
  sample: 'too_few', expectancy: 1.7, profit_factor: null, longest_losing_streak: 0,
  total_range: null, median_day: 1.7, day_spread: 0.8, winning_days: 2, losing_days: 0,
  winners_lowest: { median: -0.68, tenth: -4.2 }, losers_highest: null,
  against_random: {
    repetitions: 200, seed: 1, entries: 13, beaten: 64, median: 3.4, low: -13.6, high: 3.43, level: 90,
    worst: -27.75, best: 3.43, trades: 3, win_rate: 66.7,
    totals: [-27.75, -13.56, -13.56, 1.7, 3.4, 3.43, 3.43, 3.43],
  },
}

const MEASURED = {
  trades: 42, used: 38, without_prices: 3, unreadable: 0, set_aside: 1, fitted: true, reason: null, sample: 'few' as const,
  entry_price: 5.1, entry_price_low: 4.2, entry_price_high: 7.9, points_per_unit: 104.2, points_per_unit_low: 96.5,
  points_per_unit_high: 113.3, level: 90, spread: 0.04, median_error: 0.3, ninth_error: 1.1, measured_at: '2026-10-13T06:00:00Z',
}

const calibration = (changes: Partial<BacktestCalibration> = {}): BacktestCalibration => ({
  status: { state: 'idle' }, result: null, turbo: OPTIONS.turbo, lines: [], ...changes,
})

const leg = (reference: string, performance: number, exit: string, entered = Date.parse('2026-09-15T07:52:46Z') / 1000) => ({
  direction: 'short', entered_at: entered, left_at: entered + 480, performance, exit, reference,
})

const COMPARISON: BacktestComparison = {
  id: FIRST, leverage: 110, timezone: 'Europe/Paris',
  journal: { executed: 2, refused: 11, ignored: 1 },
  summary: {
    real_trades: 2, model_trades: 2, paired: 1, average_gap: -0.12, median_gap: 0.12, ninth_gap: 0.12, same_exit: 1,
    real_total: -13.45, model_total: 3.43, days: 2, median_day_gap: 8.5,
  },
  pairs: [{
    real: leg('p1', 1.82, 'Daily profit target'), model: leg('', 1.7, 'Daily profit target'),
    gap: -0.12, same_exit: true, left_later: -75,
  }],
  only_real: [leg('p0', -15, 'Stop-loss', Date.parse('2026-09-17T09:00:00Z') / 1000)],
  only_model: [],
  days: [
    { day: '2026-09-15', real_percent: 1.82, model_percent: 1.7, gap: -0.12, real_trades: 1, model_trades: 1 },
    { day: '2026-09-17', real_percent: -15, model_percent: 1.7, gap: 16.7, real_trades: 1, model_trades: 1 },
  ],
}

const listed = (changes: Partial<BacktestRunListed> = {}): BacktestRunListed => ({
  id: FIRST,
  from: '2026-09-15',
  to: '2026-09-22',
  strategy: 'kernel_vwap',
  parameters: {},
  rules: {},
  turbo: { entry_price: 4.4, spread: 0.03, leverages: [80, 110, 140] },
  note: '',
  status: { state: 'done', created_at: '2026-10-12T22:19:47Z', finished_at: '2026-10-12T22:19:48Z' },
  headline: { leverage: 110, days: 3, days_with_an_entry: 2, trades: 2, win_rate: 100, total_percent: 3.43, worst_drawdown: 0 },
  ...changes,
})

const whole = (changes: Partial<BacktestRun> = {}): BacktestRun => ({
  ...listed(),
  summary: {
    parameters: {},
    bars: 23310,
    decisions: 15,
    seconds: 0.3,
    days_without_prices: ['2026-09-16'],
    days_incomplete: [],
    runs: [80, 110, 140].map((leverage) => ({
      turbo: { entry_price: 4.4, points_per_unit: leverage, spread: 0.03 },
      summary: figures(2, leverage === 110 ? 3.43 : 3.4),
    })),
  },
  progress: null,
  run_rules: {},
  output: [],
  waiting_before: 0,
  ...changes,
})

const CHANGED = whole({
  id: SECOND,
  note: 'catch-up, target 3 %',
  parameters: { entry_mode: 'cross_catchup', kernel_bandwidth: 8 },
  rules: { day_profit_target: 3 },
  rules_before: { day_profit_target: 1.7 },
  headline: { leverage: 110, days: 3, days_with_an_entry: 3, trades: 3, win_rate: 100, total_percent: 9.27, worst_drawdown: 0 },
})

const TRADE = {
  entry_time: '2026-09-15T07:52:40Z', exit_time: '2026-09-15T08:00:20Z', direction: 'short', entry_reason: 'catch-up',
  confidence: 1.86, entry_index: 28994.4, exit_index: 28982.9, index_points: 11.5, performance_percent: 1.7,
  max_performance_percent: 1.7, exit_reason: 'Daily profit target',
}
const DAYS = [
  { day: '2026-09-15', entries: 1, closed: 1, day_percent: 1.7 },
  { day: '2026-09-17', entries: 1, closed: 1, day_percent: 1.7 },
]
const REFUSAL = { time: '2026-09-15T10:00:00Z', direction: 'long', reason: 'Day profit reached' }

const T0 = Date.parse('2026-09-15T08:00:00Z') / 1000
const DAY_CHART: BacktestDayChart = {
  id: FIRST, day: '2026-09-15', timezone: 'Europe/Paris', leverage: 110, tick: 0.1, decimals: 1, size: 6,
  session: { start: T0 - 7200, end: T0 + 40000 },
  bars: [
    [T0, 29000, 29006, 29000, 29006, T0, T0 + 60, true, false, null],
    [T0 + 60, 29006, 29006, 29000, 29000, T0 + 60, T0 + 120, true, false, null],
  ],
  kernel: [29003, 29001],
  vwap: [29010, 29009],
  upper: [29030, 29031],
  lower: [28990, 28987],
  entries_allowed: [false, true],
  decisions: [],
  trades: [{ ...TRADE, entry_time: '2026-09-15T08:01:00Z', exit_time: '2026-09-15T08:01:30Z' }],
  refusals: [],
}

const group = (name: string | number, trades: number, total: number): BacktestGroup => ({
  group: name, trade_count: trades, win_count: trades - 1, loss_count: 1, win_rate: 50, profit_loss: total * 100,
  average_percent: total / trades, average_best_percent: 2.5, total_percent: total,
})

const BREAKDOWNS: BacktestBreakdowns = {
  id: FIRST, leverage: 110, timezone: 'Europe/Paris',
  summary: {
    trade_count: 6, win_count: 4, loss_count: 2, win_rate: 66.67, profit_loss: -120, average_percent: -1.2,
    average_best_percent: 2.5, total_percent: -7.2, average_given_back: 3.7, turned_negative_count: 2, turned_negative_percent: 33.33,
  },
  groups: {
    hour: [group(9, 4, 5.1), group(15, 2, -12.3)],
    weekday: [group('Monday', 3, 1.7), group('Tuesday', 3, -8.9)],
    direction: [group('long', 4, 3.4), group('short', 2, -10.6)],
    entry_reason: [group('cross', 6, -7.2)],
    confidence: [group('1.5 and above', 6, -7.2)],
    holding_time: [group('Under 5 min', 6, -7.2)],
    exit: [group('Daily profit target', 4, 6.8), group('Stoploss', 2, -14)],
  },
}

const PATH_START = Date.parse('2026-09-15T08:01:00Z') / 1000
const TRADE_PATH: BacktestTradePath = {
  id: FIRST, leverage: 110, timezone: 'Europe/Paris',
  trade: { ...TRADE, entry_time: '2026-09-15T08:01:00Z', exit_time: '2026-09-15T08:01:30Z' },
  day_before_percent: -5,
  points: [[PATH_START, 0.4, -0.7, 0.6, null], [PATH_START + 10, 5.2, 0.4, 5.6, -2.4], [PATH_START + 20, 7.05, 4.9, 7.05, -0.95]],
  levels: { stop: -15, take_profit: 60, day_target: 7.05, trailing_activation: 5, trailing_drawdown: 8 },
}

const swept = (total: number, trades = 12) => ({ ...figures(trades, total), expectancy: total / trades })
const SWEEP: BacktestSweep = {
  axes: [
    { kind: 'rule', name: 'day_profit_target', values: [1.7, 3] },
    { kind: 'parameter', name: 'kernel_bandwidth', values: [8, 15] },
  ],
  held_out: { share: 0.3, from: '2026-09-20', days: 6, tuning_days: 14 },
  cells: [
    { values: [1.7, 8], tuning: swept(4.1), held_out: swept(2.2, 5), whole: swept(6.4, 17) },
    { values: [1.7, 15], tuning: swept(5.0), held_out: swept(3.1, 5), whole: swept(8.3, 17) },
    { values: [3, 8], tuning: swept(9.6), held_out: swept(-4.2, 5), whole: swept(5.0, 17) },
    { values: [3, 15], tuning: swept(-2.0), held_out: swept(1.5, 5), whole: swept(-0.5, 17) },
  ],
  days_without_prices: [],
  bars: 150000,
  seconds: 4.2,
}

interface Server {
  runs?: BacktestRunListed[]
  run?: Record<string, BacktestRun>
  prices?: PriceCoverage
  /** What the server answers to a request for a new run, when it refuses it. */
  refused?: { status: number; detail: string }
  options?: BacktestOptions
  calibration?: BacktestCalibration
}

interface Sent {
  method: string
  path: string
  body: unknown
}

let sent: Sent[] = []

function serve({ runs = [], run = {}, prices = coverage(), refused, options = OPTIONS, calibration: measured = calibration() }: Server = {}) {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      sent.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined })
      const [, , , runId, what, day] = url.pathname.split('/')

      if (url.pathname === '/api/backtests/options') return json(options)
      if (url.pathname === '/api/backtests/prices') return json(prices)
      if (url.pathname === '/api/backtests/prices/keep') return json(keeping({ enabled: true, state: 'due' }))
      if (url.pathname === '/api/backtests/calibration') {
        return method === 'GET' ? json(measured) : json(calibration({ status: { state: 'running', trades: 42 } }), 202)
      }
      if (url.pathname === '/api/backtests/signals') return json(whole({ id: SECOND, kind: 'signals' }), 202)
      if (url.pathname === '/api/backtests/sweeps') return json(whole({ id: SECOND, kind: 'sweep' }), 202)
      if (url.pathname === '/api/backtests/storage') {
        return json({ runs: { count: 2, bytes: 48_200 }, prices: { days: 3, bars_bytes: 600_000, fetched_bytes: 13_400_000 } })
      }
      if (url.pathname === '/api/backtests/remove') {
        const { ids } = JSON.parse(String(init.body)) as { ids: string[] }
        return json({ removed: ids, kept: [] })
      }
      if (method === 'PATCH') return json({ ...run[runId], ...(JSON.parse(String(init.body)) as object) })
      if (what === 'cells') return json(whole({ id: SECOND }), 202)
      if (what === 'breakdowns') return json(BREAKDOWNS)
      if (what === 'trades' && day === 'path') return json(TRADE_PATH)
      if (url.pathname === '/api/backtests/prices/fetch') return json(fetchStatus({ state: 'running' }), 202)
      if (url.pathname === '/api/backtests/prices/fetch/stop') return json(fetchStatus({ state: 'running' }))
      if (url.pathname === '/api/backtests' && method === 'GET') return json({ items: runs })
      if (url.pathname === '/api/backtests' && method === 'POST') {
        return refused ? json({ detail: refused.detail }, refused.status) : json(whole({ id: SECOND }), 202)
      }
      if (method === 'DELETE') return json(null, 204)
      if (what === 'cancel') return json(run[runId])
      if (what === 'comparison') return json(COMPARISON)
      if (what === 'trades') return json({ items: [TRADE] })
      if (what === 'refusals') return json({ items: [REFUSAL] })
      if (what === 'days' && day) return json(DAY_CHART)
      if (what === 'days') return json({ items: DAYS })
      if (run[runId]) return json(run[runId])
      return json({ detail: 'No such backtest' }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

function renderAt(address: string, session: Session = SESSION) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (element: React.ReactNode) => (
    <>
      {element}
      <Address />
    </>
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthContext.Provider value={{ session, error: null, login: async () => {}, logout: async () => {} }}>
          <MemoryRouter initialEntries={[address]}>
            <Routes>
              <Route path="backtests" element={page(<BacktestsPage />)} />
              <Route path="backtests/new" element={page(<BacktestFormPage />)} />
              <Route path="backtests/compare" element={page(<ComparePage />)} />
              <Route path="backtests/:runId" element={page(<BacktestRunPage />)} />
              <Route path="backtests/:runId/days/:day" element={page(<BacktestDayPage />)} />
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

const address = () => screen.getByLabelText('address').textContent
const requests = (method: string) => sent.filter((request) => request.method === method)

beforeEach(() => {
  resetCharts()
  window.localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the list of backtests', () => {
  it('shows each run with its state and the figures of its middle leverage', async () => {
    serve({ runs: [CHANGED, listed()] })
    renderAt('/backtests')

    const row = (await screen.findByRole('link', { name: 'catch-up, target 3 %' })).closest('tr') as HTMLElement
    expect(within(row).getByText('Done')).toBeInTheDocument()
    expect(within(row).getByText('+9.27%')).toBeInTheDocument()
    expect(within(row).getByText('15 Sept 2026 to 22 Sept 2026')).toBeInTheDocument()
    // A run that changed nothing is named for that.
    expect(screen.getByRole('link', { name: 'Defaults and live rules' })).toHaveAttribute('href', `/backtests/${FIRST}`)
  })

  it('opens the comparison of the runs that were ticked', async () => {
    const user = userEvent.setup()
    serve({ runs: [CHANGED, listed()] })
    renderAt('/backtests')
    expect(await screen.findByRole('button', { name: 'Compare' })).toBeDisabled()

    await user.click(await screen.findByRole('checkbox', { name: 'Select: catch-up, target 3 %' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select: Defaults and live rules' }))
    await user.click(screen.getByRole('button', { name: 'Compare 2' }))

    expect(address()).toBe(`/backtests/compare?runs=${SECOND},${FIRST}`)
  })

  it('offers no comparison for a run that has no result yet', async () => {
    serve({ runs: [listed({ status: { state: 'running' }, headline: null })] })
    renderAt('/backtests')

    expect(await screen.findByText('Running')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('says what a total is worth: the random runs it beat, and when it rests on too little', async () => {
    const weighed = listed({ headline: { ...listed().headline!, sample: 'too_few', beaten: 64 } })
    serve({ runs: [weighed] })
    renderAt('/backtests')

    const row = (await screen.findByRole('link', { name: 'Defaults and live rules' })).closest('tr') as HTMLElement
    expect(within(row).getByText('64%')).toBeInTheDocument()
    expect(within(row).getByText('too few to conclude')).toBeInTheDocument()
    expect(screen.getByText(/near 50%, the entries have shown nothing/)).toBeInTheDocument()
  })

  it('names a replay of real signals for what it is', async () => {
    serve({ runs: [listed({ kind: 'signals', strategy: 'signals', rules: { day_profit_target: 3 } })] })
    renderAt('/backtests')

    expect(await screen.findByRole('link', { name: 'Real signals · day profit target 3' })).toBeInTheDocument()
  })

  it('shows the tags of a run, that it is stale, and what a sweep tried', async () => {
    const tagged = listed({ tags: ['keep', 'september'], stale: { code: true, prices: false, version: '0.12.0' } })
    const sweep = listed({
      id: SECOND, kind: 'sweep', headline: null,
      axes: [{ kind: 'rule', name: 'day_profit_target', values: [1.7, 3] }, { kind: 'parameter', name: 'kernel_bandwidth', values: [8, 15] }],
    })
    serve({ runs: [sweep, tagged] })
    renderAt('/backtests')

    const row = (await screen.findByRole('link', { name: 'Defaults and live rules' })).closest('tr') as HTMLElement
    expect(within(row).getByText('keep')).toBeInTheDocument()
    expect(within(row).getByText('september')).toBeInTheDocument()
    expect(within(row).getByText('stale')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Sweep of day profit target × kernel bandwidth' })).toHaveAttribute('href', `/backtests/${SECOND}`)
  })

  it('compares finished backtests only: a sweep that is selected is not one', async () => {
    const user = userEvent.setup()
    serve({ runs: [listed({ id: SECOND, kind: 'sweep', headline: null, axes: [] }), listed()] })
    renderAt('/backtests')

    await user.click(await screen.findByRole('checkbox', { name: 'Select: Defaults and live rules' }))
    await user.click(screen.getByRole('checkbox', { name: /Select: Sweep of/ }))

    expect(screen.getByRole('button', { name: 'Compare' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove 2' })).toBeEnabled()
  })

  it('removes the runs selected once that was confirmed', async () => {
    const user = userEvent.setup()
    serve({ runs: [CHANGED, listed()] })
    renderAt('/backtests')
    expect(await screen.findByRole('button', { name: 'Remove' })).toBeDisabled()

    await user.click(await screen.findByRole('checkbox', { name: 'Select: catch-up, target 3 %' }))
    await user.click(screen.getByRole('button', { name: 'Remove 1' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('catch-up, target 3 %')).toBeInTheDocument()
    expect(requests('POST')).toEqual([])
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(requests('POST')).toEqual([
      { method: 'POST', path: '/api/backtests/remove', body: { ids: [SECOND] } },
    ]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('selects the runs that failed or were stopped in one go, and says what the disk holds', async () => {
    const user = userEvent.setup()
    const failed = listed({ id: SECOND, status: { state: 'failed', created_at: '2026-10-12T22:19:50Z' }, headline: null, note: 'broken' })
    serve({ runs: [failed, listed()] })
    renderAt('/backtests')

    expect(await screen.findByText(/2 runs take 48.2 kB on disk; 3 days of prices, 14.0 MB\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Select the 1 failed or stopped' }))

    expect(screen.getByRole('checkbox', { name: 'Select: broken' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Select: Defaults and live rules' })).not.toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Select none' }))
    expect(screen.getByRole('checkbox', { name: 'Select: broken' })).not.toBeChecked()
  })

  it('says how to get a first run when there is none', async () => {
    serve()
    renderAt('/backtests')

    expect(await screen.findByText('No backtest yet')).toBeInTheDocument()
  })

  it('asks the server for nothing when backtests are switched off, and says where to switch them on', async () => {
    serve()
    renderAt('/backtests', { ...SESSION, backtests_enabled: false })

    expect(screen.getByText('Backtests are switched off')).toBeInTheDocument()
    expect(screen.getByText('admin.backtests_enabled')).toBeInTheDocument()
    expect(sent).toEqual([])
  })
})

describe('the prices panel', () => {
  it('says which days are ready, and shows each weekday with its state', async () => {
    serve()
    renderAt('/backtests')

    expect(await screen.findByText(/2 days of the index ready to backtest on, from 15 Sept 2026 to 17 Sept 2026/)).toBeInTheDocument()
    const month = screen.getByRole('list', { name: 'Prices of Sept 2026' })
    // The Saturday is not a day anything is fetched for.
    expect(within(month).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '14 Sept 2026: some hours fetched',
      '15 Sept 2026: ready',
      '16 Sept 2026: not fetched',
      '17 Sept 2026: ready',
    ])
  })

  it('starts a download of the period chosen', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests')

    await user.click(await screen.findByRole('button', { name: 'Fetch these days' }))

    await waitFor(() => expect(requests('POST')).toHaveLength(1))
    const [{ path, body }] = requests('POST')
    expect(path).toBe('/api/backtests/prices/fetch')
    expect(body).toEqual({ from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), to: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) })
  })

  it('shows what a running download says, and stops it when asked', async () => {
    const user = userEvent.setup()
    const running = fetchStatus({ state: 'running', from: '2026-10-05', to: '2026-10-09', lines: ['2026-10-05: 8010 bars'] })
    serve({ prices: coverage({ fetch: running }) })
    renderAt('/backtests')

    expect(await screen.findByText('2026-10-05: 8010 bars')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Fetch these days' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop the download' }))

    await waitFor(() => expect(requests('POST').map((request) => request.path)).toEqual(['/api/backtests/prices/fetch/stop']))
  })

  it('says that a download usually ends with days missing, and what to do', async () => {
    serve({ prices: coverage({ fetch: fetchStatus({ state: 'incomplete', from: '2026-10-05', to: '2026-10-09' }) }) })
    renderAt('/backtests')

    expect(await screen.findByText(/Ended with days still missing.*Run the same period again/)).toBeInTheDocument()
  })

  it('says so when no day has been fetched yet', async () => {
    serve({ prices: coverage({ days: [], ready: { count: 0, first: null, last: null } }) })
    renderAt('/backtests')

    expect(await screen.findByText(/No day of prices yet/)).toBeInTheDocument()
  })

  it('has the service told to keep the prices up to date', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests')

    const keep = await screen.findByRole('switch', { name: 'Keep the prices up to date' })
    expect(keep).toHaveAttribute('aria-checked', 'false')
    expect(keep).toHaveAccessibleDescription('Prices are fetched only when asked for below.')
    await user.click(keep)

    await waitFor(() => expect(requests('PUT')).toEqual([
      { method: 'PUT', path: '/api/backtests/prices/keep', body: { enabled: true, days: 120 } },
    ]))
  })

  it('says what the service is waiting for when it fetches by itself', async () => {
    const waiting = keeping({ enabled: true, state: 'backing_off', missing: 12, failures: 2, next_at: '2026-10-13T02:00:00Z' })
    serve({ prices: coverage({ keep: waiting, fetch: fetchStatus({ state: 'incomplete', automatic: true }) }) })
    renderAt('/backtests')

    expect(await screen.findByRole('switch', { name: 'Keep the prices up to date' })).toHaveAccessibleDescription(
      /12 days to fetch\. The source stopped answering 2 times in a row: left alone until .*04:00/,
    )
    // Its own downloads are not the operator's to run again.
    expect(screen.getByText(/The service will ask again later/)).toBeInTheDocument()
  })

  it('tells apart a day whose last hours are not there yet and one the source does not have', async () => {
    serve({ prices: coverage({ days: [
      { day: '2026-09-14', state: 'partial', hours: 22, hours_expected: 24 },
      { day: '2026-09-15', state: 'unavailable', hours: 0, hours_expected: 24 },
    ] }) })
    renderAt('/backtests')

    const month = await screen.findByRole('list', { name: 'Prices of Sept 2026' })
    expect(within(month).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '14 Sept 2026: ready, its last hours not published yet',
      '15 Sept 2026: nothing at the source',
    ])
  })
})

describe('the turbo of the backtests', () => {
  it('is a guess until it is measured, which is asked for from here', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests')

    expect(await screen.findByText(/Backtests assume a turbo paid 4.4 that moves by 1.00 for 80, 110, 140 index points/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Measure on real trades' }))

    await waitFor(() => expect(requests('POST').map((request) => request.path)).toEqual(['/api/backtests/calibration']))
  })

  it('says what was measured, on how many trades, and how far it is from them', async () => {
    const turbo = { entry_price: 5.1, spread: 0.04, leverages: [96.5, 104.2, 113.3], measured: true }
    serve({ calibration: calibration({ status: { state: 'done' }, result: MEASURED, turbo }) })
    renderAt('/backtests')

    expect(await screen.findByText(/made with the turbo measured on WATA's closed trades: paid 5.1, 1.00 for 96.5, 104.2, 113.3/)).toBeInTheDocument()
    expect(screen.getByText('42 closed trades: 38 used, 3 without index prices for their day, 1 set aside as unlike the others.')).toBeInTheDocument()
    expect(screen.getByText('1.00 of turbo for 104.2 index points (90 in 100: 96.5 to 113.3), spread 0.04.')).toBeInTheDocument()
    expect(screen.getByText(/Half of the real trades are within 0.3 points of percent/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Measure again' })).toBeEnabled()
  })

  it('shows a measure in progress, and one that failed', async () => {
    serve({ calibration: calibration({ status: { state: 'running', trades: 42 } }) })
    renderAt('/backtests')
    expect(await screen.findByRole('button', { name: /Measuring on 42 trades/ })).toBeDisabled()
  })

  it('says why a measure failed', async () => {
    serve({ calibration: calibration({ status: { state: 'failed', error: 'code 2' }, lines: ['The trades cannot be read'] }) })
    renderAt('/backtests')

    expect(await screen.findByText('The measure failed')).toBeInTheDocument()
    expect(screen.getByText('The trades cannot be read')).toBeInTheDocument()
  })
})

describe('asking for a backtest', () => {
  it('sends the period, the turbo assumed and only what was changed', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    const bandwidth = await screen.findByLabelText('Kernel bandwidth')
    await user.clear(bandwidth)
    await user.type(bandwidth, '8')
    const stop = screen.getByLabelText('Stop-loss, in percent of the turbo')
    await user.clear(stop)
    await user.type(stop, '-10')
    await user.click(screen.getByRole('switch', { name: 'Trailing stop' }))
    await user.type(screen.getByLabelText('What this run is for (optional)'), 'tighter stop')
    expect(screen.getByText('Default: 15')).toBeInTheDocument()
    expect(screen.getByText('Live: -15')).toBeInTheDocument()
    expect(screen.getByText(/1 parameter and 2 rules changed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run the backtest' }))

    await waitFor(() => expect(address()).toBe(`/backtests/${SECOND}`))
    expect(requests('POST')).toEqual([{
      method: 'POST',
      path: '/api/backtests',
      body: {
        // The days that have prices, by default.
        from: '2026-09-15', to: '2026-09-17', strategy: 'kernel_vwap',
        parameters: { kernel_bandwidth: 8 },
        rules: { stoploss_percent: -10, trailing_enabled: false },
        entry_price: 4.4, spread: 0.03, leverages: [80, 110, 140],
        note: 'tighter stop',
        // Fills as kind as the bars, and 10,000 to count the money on.
        capital: 10000, fill_delay: 0, slippage: 0,
      },
    }])
  })

  it('sends nothing as changed when nothing was', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await user.click(await screen.findByRole('button', { name: 'Run the backtest' }))

    await waitFor(() => expect(requests('POST')).toHaveLength(1))
    expect(requests('POST')[0].body).toMatchObject({ parameters: {}, rules: {} })
  })

  it('does not ask while a value cannot be used, and says which', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    const bandwidth = await screen.findByLabelText('Kernel bandwidth')
    await user.clear(bandwidth)
    await user.type(bandwidth, '1000')

    expect(screen.getByText('Between 2 and 500.')).toBeInTheDocument()
    expect(bandwidth).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Run the backtest' })).toBeDisabled()

    const leverages = screen.getByLabelText('Index points for 1.00 of turbo')
    await user.clear(bandwidth)
    await user.type(bandwidth, '15')
    await user.clear(leverages)
    await user.type(leverages, '80, 80')
    expect(screen.getByText('Numbers above zero separated by commas, each once.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run the backtest' })).toBeDisabled()
  })

  it('keeps the parameters seldom touched folded until they are asked for', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await screen.findByLabelText('Kernel bandwidth')
    expect(screen.queryByLabelText('Tick')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'More parameters' }))

    expect(screen.getByLabelText('Tick')).toHaveValue('0.1')
  })

  it('replays the signals WATA received instead of a strategy, when asked to', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await screen.findByLabelText('Kernel bandwidth')
    await user.click(screen.getByRole('button', { name: 'The signals WATA received' }))

    // No strategy, so none of its parameters: the rules and the turbo stay.
    expect(screen.queryByLabelText('Kernel bandwidth')).not.toBeInTheDocument()
    expect(screen.getByText(/it is how the model itself is checked/)).toBeInTheDocument()
    const target = screen.getByLabelText('Daily target, in percent')
    await user.clear(target)
    await user.type(target, '3')
    expect(screen.getByText(/1 rule changed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Replay the signals' }))

    await waitFor(() => expect(address()).toBe(`/backtests/${SECOND}`))
    expect(requests('POST')).toEqual([{
      method: 'POST',
      path: '/api/backtests/signals',
      body: {
        from: '2026-09-15', to: '2026-09-17', rules: { day_profit_target: 3 },
        entry_price: 4.4, spread: 0.03, leverages: [80, 110, 140], note: '',
        capital: 10000, fill_delay: 0, slippage: 0,
      },
    }])
  })

  it('does not hold a replay back for a parameter of the strategy that could not be used', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    const bandwidth = await screen.findByLabelText('Kernel bandwidth')
    await user.clear(bandwidth)
    await user.type(bandwidth, '1000')
    await user.click(screen.getByRole('button', { name: 'The signals WATA received' }))

    expect(screen.getByRole('button', { name: 'Replay the signals' })).toBeEnabled()
  })

  it('asks for less kind fills and another account', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    const delay = await screen.findByLabelText('Wait before a fill, in seconds')
    await user.clear(delay)
    await user.type(delay, '30')
    const slippage = screen.getByLabelText('Index points lost on each fill')
    await user.clear(slippage)
    await user.type(slippage, '1.5')
    const capital = screen.getByLabelText('Starting capital')
    await user.clear(capital)
    await user.type(capital, '2500')
    await user.click(screen.getByRole('button', { name: 'Run the backtest' }))

    await waitFor(() => expect(requests('POST')).toHaveLength(1))
    expect(requests('POST')[0].body).toMatchObject({ capital: 2500, fill_delay: 30, slippage: 1.5 })
  })

  it('does not ask while a fill could not be taken that way', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    const delay = await screen.findByLabelText('Wait before a fill, in seconds')
    await user.clear(delay)
    await user.type(delay, '7.5')

    expect(screen.getByText('Whole seconds, from 0 to 600.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run the backtest' })).toBeDisabled()
  })

  it('asks for a sweep when a setting is given several values', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await screen.findByLabelText('Kernel bandwidth')
    // No sweep until a setting is chosen: the form asks for one backtest.
    expect(screen.queryByLabelText(/Values of:/)).not.toBeInTheDocument()
    await user.click(screen.getByLabelText('Setting'))
    await user.click(await screen.findByRole('option', { name: 'Daily target, in percent' }))
    await user.type(screen.getByLabelText('Values of: Daily target, in percent'), '1, 1.7, 3')
    await user.click(screen.getByLabelText('Second setting'))
    await user.click(await screen.findByRole('option', { name: 'Kernel bandwidth' }))
    await user.type(screen.getByLabelText('Values of: Kernel bandwidth'), '8, 15')
    // The daily target typed above as one value is not sent beside its several values.
    const target = screen.getByLabelText('Daily target, in percent')
    await user.clear(target)
    await user.type(target, '2')
    await user.click(screen.getByRole('button', { name: 'Run the sweep of 6 combinations' }))

    await waitFor(() => expect(address()).toBe(`/backtests/${SECOND}`))
    expect(requests('POST')).toEqual([{
      method: 'POST',
      path: '/api/backtests/sweeps',
      body: {
        from: '2026-09-15', to: '2026-09-17', replay: 'strategy',
        axes: [
          { kind: 'rule', name: 'day_profit_target', values: [1, 1.7, 3] },
          { kind: 'parameter', name: 'kernel_bandwidth', values: [8, 15] },
        ],
        held_out: 0.3, strategy: 'kernel_vwap', parameters: {}, rules: {},
        entry_price: 4.4, spread: 0.03,
        // One turbo: the middle one of those typed.
        leverage: 110, note: '', fill_delay: 0, slippage: 0,
      },
    }])
  })

  it('does not ask for a sweep whose values cannot be used, and says why', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await screen.findByLabelText('Kernel bandwidth')
    await user.click(screen.getByLabelText('Setting'))
    await user.click(await screen.findByRole('option', { name: 'Kernel bandwidth' }))
    const values = screen.getByLabelText('Values of: Kernel bandwidth')
    await user.type(values, '8')
    expect(screen.getByText('Two values at least, separated by commas.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run the sweep' })).toBeDisabled()

    await user.type(values, ', 1000')
    expect(screen.getByText('1000: Between 2 and 500.')).toBeInTheDocument()

    await user.clear(values)
    await user.type(values, '8, 15')
    await user.click(screen.getByLabelText('Second setting'))
    await user.click((await screen.findAllByRole('option', { name: 'Kernel bandwidth' })).at(-1) as HTMLElement)
    await user.type(screen.getAllByLabelText('Values of: Kernel bandwidth')[1], '20, 30')
    expect(screen.getByText('The two settings are the same one.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run the sweep' })).toBeDisabled()
  })

  it('sweeps rules only over the signals WATA received', async () => {
    const user = userEvent.setup()
    serve()
    renderAt('/backtests/new')

    await screen.findByLabelText('Kernel bandwidth')
    await user.click(screen.getByRole('button', { name: 'The signals WATA received' }))
    await user.click(screen.getByLabelText('Setting'))

    expect(await screen.findByRole('option', { name: 'Daily target, in percent' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Kernel bandwidth' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Trailing stop' }))
    await user.type(screen.getByLabelText('Values of: Trailing stop'), 'on, off')
    await user.click(screen.getByRole('button', { name: 'Run the sweep of 2 combinations' }))

    await waitFor(() => expect(requests('POST')).toHaveLength(1))
    expect(requests('POST')[0].body).toMatchObject({
      replay: 'signals', axes: [{ kind: 'rule', name: 'trailing_enabled', values: [true, false] }],
    })
  })

  it('starts again from a replay as a replay', async () => {
    serve({ run: { [SECOND]: whole({ id: SECOND, kind: 'signals', strategy: 'signals' }) } })
    renderAt(`/backtests/new?from=${SECOND}`)

    expect(await screen.findByRole('button', { name: 'The signals WATA received' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Replay the signals' })).toBeInTheDocument()
  })

  it('says where the turbo offered comes from once it was measured', async () => {
    const measured = { ...OPTIONS, turbo: { entry_price: 5.1, spread: 0.04, leverages: [96.5, 104.2, 113.3], measured: true } }
    serve({ options: measured })
    renderAt('/backtests/new')

    expect(await screen.findByText('Turbo measured on real trades')).toBeInTheDocument()
    expect(screen.getByLabelText('Price paid')).toHaveValue('5.1')
    expect(screen.getByLabelText('Index points for 1.00 of turbo')).toHaveValue('96.5, 104.2, 113.3')
  })

  it('starts from another run when told to, with what that one changed', async () => {
    serve({ run: { [SECOND]: CHANGED } })
    renderAt(`/backtests/new?from=${SECOND}`)

    expect(await screen.findByLabelText('Kernel bandwidth')).toHaveValue('8')
    expect(screen.getByLabelText('Daily target, in percent')).toHaveValue('3')
    expect(screen.getByLabelText('What this run is for (optional)')).toHaveValue('catch-up, target 3 %')
    expect(screen.getByText('Live: 1.7')).toBeInTheDocument()
  })

  it('shows what the server refused', async () => {
    const user = userEvent.setup()
    serve({ refused: { status: 409, detail: '10 backtests are already waiting: let some finish first' } })
    renderAt('/backtests/new')

    await user.click(await screen.findByRole('button', { name: 'Run the backtest' }))

    expect(await screen.findByText('10 backtests are already waiting: let some finish first')).toBeInTheDocument()
    expect(address()).toBe('/backtests/new')
  })

  it('warns that the period has days without prices, and that there are none at all', async () => {
    serve({ prices: coverage({ ready: { count: 3, first: '2026-09-14', last: '2026-09-17' } }) })
    renderAt('/backtests/new')
    expect(await screen.findByText(/2 weekdays of this period have no complete prices/)).toBeInTheDocument()
  })

  it('says a backtest would find nothing when no prices were fetched', async () => {
    serve({ prices: coverage({ days: [], ready: { count: 0, first: null, last: null } }) })
    renderAt('/backtests/new')

    expect(await screen.findByText(/No day of prices has been fetched yet/)).toBeInTheDocument()
  })
})

describe('a run', () => {
  it('shows what was asked, what it found, and the days it could not test', async () => {
    serve({ run: { [SECOND]: CHANGED } })
    renderAt(`/backtests/${SECOND}`)

    expect(await screen.findByRole('heading', { name: 'catch-up, target 3 %' })).toBeInTheDocument()
    // What it changed, against the default and against the live rule.
    expect(await screen.findByText('Kernel bandwidth')).toBeInTheDocument()
    expect(screen.getByText('Kernel bandwidth').nextElementSibling).toHaveTextContent('15→ changed to 8')
    expect(screen.getByText('Daily target, in percent').nextElementSibling).toHaveTextContent('1.7→ changed to 3')
    expect(screen.getByText(/No prices for 1 weekday.*16 Sept/)).toBeInTheDocument()
    // The figures of the middle leverage first.
    expect(screen.getByRole('button', { name: '110' })).toHaveAttribute('aria-pressed', 'true')
    const tile = screen.getByText('All days, one applied on the other').parentElement as HTMLElement
    expect(within(tile).getByText('+3.43%')).toBeInTheDocument()
    expect(screen.getByText('On 2 of 3 days tested')).toBeInTheDocument()
    // Why entries were refused: counted in the summary, and each one listed in its table.
    const why = screen.getByText('Entries refused by').parentElement as HTMLElement
    expect(within(why).getByText('Day profit reached').parentElement).toHaveTextContent('Day profit reached11')
    expect((await screen.findAllByText('Day profit reached')).length).toBe(2)
  })

  it('says how far its figures can be trusted: against random entries, per trade, and for a stop', async () => {
    const trusted = whole({
      summary: {
        ...whole().summary!,
        runs: [80, 110, 140].map((leverage) => ({
          turbo: { entry_price: 4.4, points_per_unit: leverage, spread: 0.03 },
          summary: { ...figures(2, 3.43), ...TRUST },
        })),
      },
    })
    serve({ run: { [FIRST]: trusted } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/2 trades over 3 days: too few to conclude/)).toBeInTheDocument()
    const random = screen.getByRole('region', { name: 'Against random entries' })
    expect(within(random).getByText('64%')).toBeInTheDocument()
    expect(within(random).getByText(/the entries have shown nothing the exit rules do not explain/)).toBeInTheDocument()
    expect(within(random).getByText(/traded 200 times with 13 entries at random moments.*half of them ended above \+3.40%, 90 in 100 between −13.60% and \+3.43%/)).toBeInTheDocument()
    const perTrade = screen.getByRole('region', { name: 'Per trade' })
    expect(within(perTrade).getByText('Won for 1 lost').nextElementSibling).toHaveTextContent('no loss')
    expect(within(screen.getByRole('region', { name: 'The total with other days' })).getByText('Too few days to draw a range from.')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Room for a stop and a target' })).getByText(/never fell below −0.68%, nine in ten never below −4.20%/)).toBeInTheDocument()
    // Every leverage with what it is worth.
    expect(screen.getAllByRole('columnheader', { name: 'Random runs beaten' }).length).toBeGreaterThan(0)
  })

  it('gives the range of its total when there are days enough to draw one', async () => {
    const ranged = { ...TRUST, sample: 'fair' as const, total_range: { low: -8.2, high: 14.6, level: 90, resamples: 2000, losing_share: 31 } }
    const trusted = whole({
      summary: {
        ...whole().summary!,
        runs: [{ turbo: { entry_price: 4.4, points_per_unit: 110, spread: 0.03 }, summary: { ...figures(120, 3.43), ...ranged, days: 80 } }],
      },
      turbo: { entry_price: 4.4, spread: 0.03, leverages: [110] },
    })
    serve({ run: { [FIRST]: trusted } })
    renderAt(`/backtests/${FIRST}`)

    const range = await screen.findByRole('region', { name: 'The total with other days' })
    expect(range).toHaveTextContent('Drawing 80 days again from the ones tested, 90 totals in 100 fall between −8.20% and +14.60%. 31% of the draws end in a loss.')
    expect(screen.queryByText(/too few to conclude/)).not.toBeInTheDocument()
  })

  it('says of a run made before all this that it has to be made again', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/made before a backtest was set against random entries/)).toBeInTheDocument()
  })

  it('sets a replay of real signals beside what WATA really did', async () => {
    const replay = whole({ kind: 'signals', strategy: 'signals', signals: 14, real_trades: 2 })
    replay.summary = { ...replay.summary!, signals: { replayed: 13, without_prices: ['2026-09-15T19:55:00Z'] } }
    serve({ run: { [FIRST]: replay } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByRole('heading', { name: 'Real signals' })).toBeInTheDocument()
    expect(screen.getByText(/14 signals of WATA's journal.*2 real trades of the period are kept/)).toBeInTheDocument()
    expect(screen.queryByText('Strategy parameters changed')).not.toBeInTheDocument()

    const paired = (await screen.findByText('Real trades the model made too')).parentElement as HTMLElement
    expect(within(paired).getByText('1 of 2')).toBeInTheDocument()
    expect(screen.getByText("Of the 14 signals replayed, WATA's journal has 2 executed, 11 refused, 1 ignored.")).toBeInTheDocument()
    expect(screen.getByText(/1 signal came at a moment the prices do not cover/)).toBeInTheDocument()
    // The pair: the real result, the model's, and how they ended.
    const pair = (await screen.findByText('75 s shorter')).closest('tr') as HTMLElement
    expect(within(pair).getByText('+1.82%')).toBeInTheDocument()
    expect(within(pair).getByText('−0.12 pts')).toBeInTheDocument()
    expect(within(pair).getByText('the same')).toBeInTheDocument()
    // The trade WATA made that the model did not.
    const alone = screen.getByText('Entered by WATA, not by the model').parentElement as HTMLElement
    expect(within(alone).getByText('Stop-loss')).toBeInTheDocument()
    expect(sent.map((request) => request.path)).toContain(`/api/backtests/${FIRST}/comparison?leverage=110`)
  })

  it('gives the result in money too, at the size WATA gives each trade', async () => {
    const money = {
      capital: 10000, final: 10342.66, profit_loss: 342.66, return_percent: 3.43, worst_drawdown: -120.5,
      worst_drawdown_percent: -1.2, average_scale_percent: 75, unfunded: 1,
    }
    const paid = whole({
      execution: { capital: 10000, fill_delay: 30, slippage: 1.5 },
      summary: {
        ...whole().summary!,
        runs: [80, 110, 140].map((leverage) => ({
          turbo: { entry_price: 4.4, points_per_unit: leverage, spread: 0.03 }, summary: { ...figures(2, 3.43), ...TRUST, money },
        })),
      },
    })
    serve({ run: { [FIRST]: paid } })
    renderAt(`/backtests/${FIRST}`)

    const tile = (await screen.findByText('In money, on €10,000.00')).parentElement as HTMLElement
    expect(within(tile).getByText('+€342.66')).toBeInTheDocument()
    expect(tile).toHaveTextContent('+3.43% at the size WATA gives each trade (75% on average)')
    expect(tile).toHaveTextContent('1 entry it could not buy')
    // How the fills were taken is part of what was asked.
    expect(screen.getByText('Each decision is filled 30 s after the next ten seconds, and each fill loses 1.5 index points.')).toBeInTheDocument()
    expect((await screen.findAllByRole('columnheader', { name: /In money/ })).length).toBeGreaterThan(0)
  })

  it('says when it would come out otherwise today', async () => {
    serve({ run: { [FIRST]: whole({ stale: { code: true, prices: true, version: '0.12.0' } }) } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/would come out otherwise today: the code that replays has changed since \(it was made with WATA 0.12.0\), and the prices of its period are not the ones it read/)).toBeInTheDocument()
  })

  it('breaks its trades down by what could explain them', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    const panel = (await screen.findByRole('heading', { name: 'Where the result comes from' })).closest('section') as HTMLElement
    // By hour of entry first.
    const nine = (await within(panel).findByRole('rowheader', { name: '09:00' })).closest('tr') as HTMLElement
    expect(within(nine).getByText('+5.10%')).toBeInTheDocument()
    expect(within(panel).getByText(/closed 3.70 points of percent under the best level it had reached. 2 of the 6 had been in profit/)).toBeInTheDocument()

    await user.click(within(panel).getByRole('button', { name: 'Left on' }))

    const stopped = within(panel).getByRole('rowheader', { name: 'Stoploss' }).closest('tr') as HTMLElement
    expect(within(stopped).getByText('−14.00%')).toBeInTheDocument()
    expect(within(panel).queryByRole('rowheader', { name: '09:00' })).not.toBeInTheDocument()
    expect(sent.map((request) => request.path)).toContain(`/api/backtests/${FIRST}/breakdowns?leverage=110`)
  })

  it('is named again and tagged from its page', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole({ note: 'first', tags: ['keep'], bytes: 48_200 }) } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/48.2 kB on disk/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Name and tags' }))
    const dialog = await screen.findByRole('dialog')
    const note = within(dialog).getByLabelText('What this run is for')
    expect(note).toHaveValue('first')
    await user.clear(note)
    await user.type(note, 'September, catch-up')
    await user.type(within(dialog).getByLabelText('Tags'), ', september')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(requests('PATCH')).toEqual([
      { method: 'PATCH', path: `/api/backtests/${FIRST}`, body: { note: 'September, catch-up', tags: ['keep', 'september'] } },
    ]))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('lists its trades and its days, each day leading to its chart', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    const day = await screen.findByRole('link', { name: '15 Sept 2026' })
    expect(day).toHaveAttribute('href', `/backtests/${FIRST}/days/2026-09-15?leverage=110`)
    const trade = (await screen.findAllByText('catch-up')).map((cell) => cell.closest('tr')).find(Boolean) as HTMLElement
    expect(within(trade).getByText('Short')).toBeInTheDocument()
    expect(within(trade).getAllByText('+1.70%').length).toBeGreaterThan(0)
    // The trade itself leads to its day with itself followed on it.
    expect(within(trade).getByRole('link')).toHaveAttribute(
      'href', `/backtests/${FIRST}/days/2026-09-15?leverage=110&trade=${encodeURIComponent(TRADE.entry_time)}`,
    )
  })

  it('reads the figures again for another leverage', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    await user.click(await screen.findByRole('button', { name: '140' }))

    await waitFor(() => expect(sent.map((request) => request.path)).toContain(`/api/backtests/${FIRST}/trades?leverage=140`))
    expect(screen.getByRole('button', { name: '140' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('says which day it is replaying while it runs, and can be stopped', async () => {
    const user = userEvent.setup()
    const running = whole({
      status: { state: 'running', created_at: '2026-10-12T22:19:47Z' }, summary: null, headline: null,
      progress: { day: '2026-09-17', days_begun: 2, bars: 9000 },
    })
    serve({ run: { [FIRST]: running } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/Replaying 17 Sept 2026/)).toBeInTheDocument()
    expect(screen.queryByText('Winning trades')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop' }))

    await waitFor(() => expect(requests('POST').map((request) => request.path)).toEqual([`/api/backtests/${FIRST}/cancel`]))
  })

  it('says how many are before it while it waits', async () => {
    const waiting = whole({ status: { state: 'queued' }, summary: null, headline: null, waiting_before: 2 })
    serve({ run: { [FIRST]: waiting } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/2 backtests will be made before it/)).toBeInTheDocument()
  })

  it('shows what the command said when it failed', async () => {
    const failed = whole({
      status: { state: 'failed', error: 'No bar of us100' }, summary: null, headline: null,
      output: ['No bar of us100 between 2026-10-14 and 2026-10-14 in var/lib/price_history.'],
    })
    serve({ run: { [FIRST]: failed } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText('The backtest failed')).toBeInTheDocument()
    expect(screen.getByText(/No bar of us100 between 2026-10-14/)).toBeInTheDocument()
  })

  it('is removed only once that was confirmed', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    await user.click(await screen.findByRole('button', { name: 'Remove' }))
    const dialog = await screen.findByRole('dialog')
    expect(requests('DELETE')).toEqual([])
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(address()).toBe('/backtests'))
    expect(requests('DELETE').map((request) => request.path)).toEqual([`/api/backtests/${FIRST}`])
  })

  it('offers to run it again with changes', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}`)

    await user.click(await screen.findByRole('button', { name: 'Run again with changes' }))

    expect(address()).toBe(`/backtests/new?from=${FIRST}`)
  })

  it('says so when there is no such run', async () => {
    serve()
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText('No such backtest')).toBeInTheDocument()
  })
})

describe('a sweep', () => {
  const sweep = whole({
    kind: 'sweep', replays: 'strategy', headline: null, summary: null, note: '',
    axes: SWEEP.axes, held_out: 0.3, turbo: { entry_price: 4.4, spread: 0.03, leverages: [110] }, sweep: SWEEP,
  })

  it('says what became of the best value on the days held out', async () => {
    serve({ run: { [FIRST]: sweep } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByRole('heading', { name: 'Sweep of day profit target × kernel bandwidth' })).toBeInTheDocument()
    expect(screen.getByText('Tried for day profit target:').parentElement).toHaveTextContent('1.7, 3')
    // The best of the first fourteen days lost on the six held out.
    expect(screen.getByText('The best value did not hold up')).toBeInTheDocument()
    expect(screen.getByText(/the best was day profit target 3, kernel bandwidth 8: \+9.60%\. On the 6 days held out it made −4.20%: number 4 of 4\. It did not hold up/)).toBeInTheDocument()
  })

  it('shows every combination on each part of the period, for the figure chosen', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: sweep } })
    renderAt(`/backtests/${FIRST}`)

    const chosenOn = await screen.findByRole('table', { name: 'Days a value may be chosen on (14)' })
    const heldOut = screen.getByRole('table', { name: 'Days held out (6)' })
    expect(within(chosenOn).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Day profit target', 'Kernel bandwidth 8', 'Kernel bandwidth 15',
    ])
    expect(within(chosenOn).getAllByRole('button').map((cell) => cell.textContent)).toEqual(['+4.10%', '+5.00%', '+9.60%', '−2.00%'])
    expect(within(heldOut).getAllByRole('button').map((cell) => cell.textContent)).toEqual(['+2.20%', '+3.10%', '−4.20%', '+1.50%'])

    await user.click(screen.getByRole('button', { name: 'Trades' }))

    expect(within(chosenOn).getAllByRole('button').map((cell) => cell.textContent)).toEqual(['12', '12', '12', '12'])
    expect(within(heldOut).getAllByRole('button').map((cell) => cell.textContent)).toEqual(['5', '5', '5', '5'])
  })

  it('makes a backtest of its own of the combination selected', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: sweep } })
    renderAt(`/backtests/${FIRST}`)

    // The best of the first part is the one selected at first.
    expect(await screen.findByRole('heading', { name: 'Day profit target 3, kernel bandwidth 8' })).toBeInTheDocument()
    await user.click(within(screen.getByRole('table', { name: 'Days held out (6)' })).getByRole('button', { name: 'day profit target 1.7, kernel bandwidth 15: +3.10%' }))

    const detail = (await screen.findByRole('heading', { name: 'Day profit target 1.7, kernel bandwidth 15' })).closest('section') as HTMLElement
    const total = within(detail).getByRole('rowheader', { name: 'All days, one on the other' }).closest('tr') as HTMLElement
    expect(within(total).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['+5.00%', '+3.10%', '+8.30%'])
    await user.click(within(detail).getByRole('button', { name: 'Make it a backtest of its own' }))

    await waitFor(() => expect(address()).toBe(`/backtests/${SECOND}`))
    expect(requests('POST').map((request) => request.path)).toEqual([`/api/backtests/${FIRST}/cells/1/run`])
  })

  it('says so when the period had too few days to hold any out', async () => {
    const short = whole({
      ...sweep,
      sweep: {
        ...SWEEP, held_out: { share: 0.3, from: null, days: 0, tuning_days: 1 },
        cells: SWEEP.cells.map((cell) => ({ ...cell, held_out: null })),
      },
    })
    serve({ run: { [FIRST]: short } })
    renderAt(`/backtests/${FIRST}`)

    expect(await screen.findByText(/too few days to hold any out: nothing here says that value would do best again/)).toBeInTheDocument()
    expect(screen.getByText(/No day was held out/)).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: /Days held out/ })).not.toBeInTheDocument()
  })
})

describe('a day of a run', () => {
  it('draws the range bars with the kernel, the VWAP and its band, and marks the trade', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15`)

    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(2))
    const chart = theChart()
    expect(sent.map((request) => request.path)).toContain(`/api/backtests/${FIRST}/days/2026-09-15/chart?leverage=110`)
    // Four lines over the prices, in their panel: the two limits of the band, the VWAP, the kernel.
    expect(chart.series.filter((series) => series.type === 'Line').map((series) => series.pane)).toEqual([0, 0, 0, 0])
    expect(seriesOf(chart, 'Candlestick')?.markers.map((marker) => marker.text)).toEqual(['short · catch-up', '+1.70%'])
    expect(screen.getByRole('heading', { name: '15 Sept 2026' })).toBeInTheDocument()
  })

  it('carries the notice and the link the charting library asks for', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15`)
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(2))

    expect(screen.getByText(/TradingView Lightweight Charts™ Copyright \(c\) 2025 TradingView, Inc\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://www.tradingview.com/' })).toHaveAttribute('href', 'https://www.tradingview.com/')
    expect(theChart().options.layout.attributionLogo).toBe(true)
  })

  it('reads the last bar until one is pointed at', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15`)
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(2))

    expect(screen.getByText('Last bar')).toBeInTheDocument()
    expect(screen.getByText('29,001.0')).toBeInTheDocument()
    expect(screen.getByText('allowed')).toBeInTheDocument()
  })

  it('goes to the day after, and has none before the first', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15`)
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(2))

    expect(screen.getByRole('button', { name: 'Day before' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Day after' }))

    expect(address()).toBe(`/backtests/${FIRST}/days/2026-09-17?leverage=110`)
  })

  it('brings a trade into view and follows it on the turbo when asked to', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15?leverage=110&trade=${encodeURIComponent('2026-09-15T08:01:00Z')}`)

    const panel = (await screen.findByRole('heading', { name: 'This trade, on the turbo' })).closest('section') as HTMLElement
    expect(await within(panel).findByText('Daily profit target')).toBeInTheDocument()
    // The day had lost 5 % before it, and the stop and the take-profit are too far to be drawn with it.
    expect(within(panel).getByText(/The day was at −5.00% before it: the day's target asked \+7.05% of this trade/)).toBeInTheDocument()
    expect(within(panel).getByText(/Too far from what the trade did to be drawn with it: stop at −15.00%, take-profit at \+60.00%/)).toBeInTheDocument()
    expect(within(panel).getByText(/The trailing stop arms at \+5.00% and sells 8 points under the best level seen/)).toBeInTheDocument()
    expect(sent.map((request) => request.path)).toContain(
      `/api/backtests/${FIRST}/trades/path?leverage=110&entry=${encodeURIComponent('2026-09-15T08:01:00Z')}`,
    )
    // The chart shows the trade and the minutes around it, not the whole day.
    await waitFor(() => expect(theChart().range).not.toBeNull())
    const { from, to } = theChart().range as { from: number; to: number }
    expect(to - from).toBeGreaterThanOrEqual(1200)
    expect(to - from).toBeLessThan(3600)
  })

  it('follows a trade of the day from the list under the chart, and goes back to the whole day', async () => {
    const user = userEvent.setup()
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15?leverage=110`)

    expect(screen.queryByText('This trade, on the turbo')).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Follow it' }))

    expect(await screen.findByRole('heading', { name: 'This trade, on the turbo' })).toBeInTheDocument()
    expect(address()).toBe(`/backtests/${FIRST}/days/2026-09-15?leverage=110&trade=${encodeURIComponent('2026-09-15T08:01:00Z').replace(/%3A/g, '%3A')}`)
    await user.click(screen.getByRole('button', { name: 'Show the whole day' }))

    await waitFor(() => expect(screen.queryByText('This trade, on the turbo')).not.toBeInTheDocument())
  })

  it('lists what became of the decisions under the chart', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/${FIRST}/days/2026-09-15`)

    expect(await screen.findByText('on catch-up')).toBeInTheDocument()
    expect(screen.getByText('Daily profit target')).toBeInTheDocument()
    expect(screen.getByText('None.')).toBeInTheDocument()
  })
})

describe('runs side by side', () => {
  it('sets the measures of each beside the other, with what each changed', async () => {
    serve({ run: { [FIRST]: whole(), [SECOND]: CHANGED } })
    renderAt(`/backtests/compare?runs=${FIRST},${SECOND}`)

    const table = (await screen.findByText('Days with an entry')).closest('table') as HTMLElement
    const row = (label: string) => within(within(table).getByText(label).closest('tr') as HTMLElement).getAllByRole('cell').map((cell) => cell.textContent)
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual(['', 'Defaults and live rules', 'catch-up, target 3 %'])
    expect(row('Entry mode')).toEqual(['default', 'cross_catchup'])
    expect(row('Kernel bandwidth')).toEqual(['default', '8'])
    expect(row('Rule: day profit target')).toEqual(['live', '3'])
    expect(row('Trades')).toEqual(['2', '2'])
    expect(row('All days, one on the other')).toEqual(['+3.43%', '+3.43%'])
  })

  it('names each line of the chart, with where it ended', async () => {
    serve({ run: { [FIRST]: whole(), [SECOND]: CHANGED } })
    renderAt(`/backtests/compare?runs=${FIRST},${SECOND}`)

    const legend = await screen.findByRole('list', { name: 'Legend' })
    await waitFor(() => expect(within(legend).getAllByText('+3.43%')).toHaveLength(2))
    expect(within(legend).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Defaults and live rules+3.43%', 'catch-up, target 3 %+3.43%',
    ])
  })

  it('asks for two runs when it is given fewer', async () => {
    serve({ run: { [FIRST]: whole() } })
    renderAt(`/backtests/compare?runs=${FIRST}`)

    expect(screen.getByText(/Tick two runs or more/)).toBeInTheDocument()
    expect(sent).toEqual([])
  })

  it('leaves out a run that has no result, and says so', async () => {
    const unfinished = whole({ id: SECOND, status: { state: 'failed' }, summary: null, headline: null })
    serve({ run: { [FIRST]: whole(), [SECOND]: unfinished } })
    renderAt(`/backtests/compare?runs=${FIRST},${SECOND}`)

    expect(await screen.findByText(/1 of the runs chosen has no result/)).toBeInTheDocument()
    expect(await screen.findByText('Days with an entry')).toBeInTheDocument()
  })

  it('compares four runs at most, and says which were left out', async () => {
    const ids = ['a', 'b', 'c', 'd', 'e'].map((letter) => `20261012-22194${letter.charCodeAt(0) % 10}-00000${letter}`)
    serve({ run: Object.fromEntries(ids.map((id) => [id, whole({ id, note: id.slice(-1) })])) })
    renderAt(`/backtests/compare?runs=${ids.join(',')}`)

    expect(await screen.findByText(/4 runs at most are compared at once: the first 4 of the 5 chosen/)).toBeInTheDocument()
    expect(sent.filter((request) => /\/api\/backtests\/[^/]+$/.test(request.path))).toHaveLength(4)
  })
})
