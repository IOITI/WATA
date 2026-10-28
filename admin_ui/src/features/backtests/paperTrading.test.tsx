import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { BacktestPaper, BacktestRun, DataAsset, Session, TaRule } from '@/lib/types'
import { day, OPTIONS, PAPER_REPORT_ID, PAPER_RULE, paperReport, trade } from '@/test/backtests'
import { resetCharts } from '@/test/fakeCharts'
import { stubSystemTheme } from '@/test/systemTheme'
import { WithTrail } from '@/test/WithTrail'

import { BacktestsPage } from './BacktestsPage'
import { NewBacktestPage } from './NewBacktestPage'
import { PaperReportPage, PaperTradingPage } from './PaperTradingPage'
import { PendingRemovalsProvider } from './PendingRemovalsProvider'
import { RunPage } from './RunPage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)
// The form's script inputs, as PineTS reads them in the page's worker.
vi.mock('@/features/ta/engine/pineWorkerClient', () => ({
  PineWorkerClient: class {
    compile = async () => ({ id: 1, ok: true, op: 'compile', declaration: 'indicator', inputs: [] })
    terminate() {}
  },
}))

const SESSION: Session = {
  authenticated: true,
  version: '0.53.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
} as Session

const RULE = {
  id: PAPER_RULE.id, name: PAPER_RULE.name, mode: 'shadow', indice: 'us100', asset: { provider: 'capital', key: 'US100' }, side: 'bid',
  bars: { type: 'range', ticks: 60 }, script: { id: '0123456789ab', version: 4, sha256: 'x' }, libraries: [], inputs: [], alerts: [],
  alert_calls: 'ignore', confidence_plot: null, created_at: '2026-10-14T09:00:00Z', updated_at: '2026-10-17T09:00:00Z',
  mode_changed_at: '2026-10-14T09:00:00Z', script_name: 'Kernel VWAP', latest: { script: 4, libraries: {} },
} as TaRule

const REPORT = {
  run_id: PAPER_REPORT_ID, from: '2026-10-14', to: '2026-10-19', made_at: '2026-10-20T21:40:00Z', signals: 12, versions: [3, 4],
  headline: { total_percent: 1.18, trades: 9, win_rate: 55.5, worst_drawdown: -2.1, sample: 'few' as const, beaten: null, days_tested: 4, profit_loss: 11.8, cells: null },
}

const ASSETS = [{ ref: 'capital/US100', provider: 'capital', key: 'US100', name: 'US Tech 100', tick: 0.1 }] as unknown as DataAsset[]

let paper: BacktestPaper
let sent: { method: string; path: string }[]
let runs: Record<string, BacktestRun>

function serve() {
  sent = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      const route = url.pathname
      sent.push({ method, path: route })
      if (route === '/api/backtests/paper') return json(paper)
      const asked = /^\/api\/backtests\/paper\/([^/]+)\/report$/.exec(route)
      if (asked && method === 'POST') {
        const owner = asked[1]
        const next = { run_id: '20261021-090000-bbbbbb', from: '2026-10-14', to: '2026-10-20', state: 'queued' as const, error: null, position: 1 }
        paper = owner === 'live'
          ? { ...paper, live: { ...(paper.live ?? { days: [], reference: null }), next_report: next } }
          : { ...paper, rules: { ...paper.rules, [owner]: { ...paper.rules[owner], next_report: next } } }
        return json(paper, 202)
      }
      if (route === '/api/backtests/paper/live') return json({ days: [], reference: null, warning: null, report: paper.live?.report ?? null, next_report: null })
      if (route === '/api/backtests') return json({ items: [], storage: { runs: { count: 0, bytes: 0 }, former: { count: 0, bytes: 0 } } })
      if (route === '/api/backtests/options') return json(OPTIONS)
      if (route === '/api/backtests/execution') return json({ detail: 'Not measured here' }, 404)
      if (route === '/api/backtests/coverage') return json({ database: true, timezone: 'Europe/Paris', days: [] })
      if (route === '/api/research/experiments') return json({ experiments: [], lockbox: null })
      if (route === '/api/backtests/turbo-model') return json({ state: 'idle', error: null, asked: null, finished_at: null, measure: null, underlying: 'dukascopy/us100', max_days: 92 })
      if (route === '/api/backtests/research') return json({ enabled: false, keep_days: 30, state: 'off', days: [] })
      const found = /^\/api\/backtests\/([^/]+)(?:\/(.+))?$/.exec(route)
      if (found) {
        const shown = runs[found[1]]
        if (!shown) return json({ detail: 'No such backtest' }, 404)
        switch (found[2]) {
          case undefined:
            return json(shown)
          case 'days':
            return json({ items: [day()] })
          case 'trades':
            return json({ items: [trade()] })
          case 'signals':
            return json({ items: [] })
          case 'breakdowns':
            return json({
              trades: 1,
              groups: { hour: [], weekday: [], direction: [], exit: [], confidence: [], holding: [], turbo_price: [], issuer: [], weekday_hour: [] },
              given_back: { average_given_back: null, turned_negative_count: 0, turned_negative_percent: null },
            })
          default:
            return json({})
        }
      }
      if (route === '/api/ta/rules') return json({ revision: 'r1', rules: [RULE], indices: ['us100'], instruments: [], engine_history_days: 1, engine_restart_time: '21:15' })
      if (route === '/api/ta/scripts') return json({ items: [] })
      if (route === '/api/data/assets') return json({ timezone: 'Europe/Paris', seen: 'all', items: ASSETS, truncated: false, max: 3000, saxo_error: null })
      return json({ detail: `Not served in this test: ${method} ${route}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search + location.hash}</output>
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
      { path: '/backtests/paper', element: page(<PaperTradingPage />) },
      { path: '/backtests/paper/:owner', element: page(<PaperReportPage />) },
      { path: '/backtests/:runId', element: page(<RunPage />) },
    ],
    { initialEntries: [address] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <PendingRemovalsProvider duration={50}>
              <RouterProvider router={router} />
            </PendingRemovalsProvider>
          </AuthContext.Provider>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

const address = () => screen.getByRole('status', { name: 'address' }).textContent

beforeEach(() => {
  stubSystemTheme(false)
  resetCharts()
  paper = {
    rules: {
      [PAPER_RULE.id]: {
        days: [
          { day: '2026-10-19', run_id: '20261019-223000-cccccc', state: 'done', day_percent: 0.4, trades: 2, outside: false },
          { day: '2026-10-16', run_id: '20261016-223000-dddddd', state: 'done', day_percent: -6, trades: 3, outside: true },
        ],
        reference: { run_id: '20261012-100000-eeeeee', name: 'Kernel VWAP, 3 months', days: 60, low: -1.5, median: 0.2, high: 2.75 },
        report: REPORT,
        next_report: null,
      },
    },
    live: { days: [], reference: null, report: null, next_report: null },
  }
  runs = { [PAPER_REPORT_ID]: paperReport() }
  serve()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Paper trading', () => {
  it('is reached from the Backtests page', async () => {
    renderAt('/backtests')

    expect(await screen.findByRole('link', { name: 'Paper trading' })).toHaveAttribute('href', '/backtests/paper')
  })

  it('shows each rule\'s report since it was made and its days, each leading to its run', async () => {
    renderAt('/backtests/paper')

    const report = await screen.findByRole('region', { name: 'Report of Kernel VWAP' })
    expect(within(report).getByText('Report, 14 Oct to 19 Oct')).toBeInTheDocument()
    expect(within(report).getByText('+1.18%')).toBeInTheDocument()
    expect(within(report).getByText('9')).toBeInTheDocument()
    // The report whatever its run is tonight, through its own address.
    expect(within(report).getByRole('link', { name: 'Open the report' })).toHaveAttribute('href', `/backtests/paper/${PAPER_RULE.id}`)
    expect(within(report).getByText('Its signals were raised by v3 and v4 of the script: the rule changed meanwhile.')).toBeInTheDocument()

    const days = screen.getByRole('region', { name: 'Days on paper of Kernel VWAP' })
    expect(within(days).getByRole('link', { name: '19 Oct 2026' })).toHaveAttribute('href', '/backtests/20261019-223000-cccccc')
    expect(within(days).getByText('Outside')).toBeInTheDocument()
    expect(within(days).getByRole('link', { name: 'Kernel VWAP, 3 months' })).toHaveAttribute('href', '/backtests/20261012-100000-eeeeee')
    // The signals WATA received have their place too.
    expect(screen.getByText('The signals WATA received')).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Analysis.*Backtests.*Paper trading/)
  })

  it('makes a report now when asked, and says it is being made', async () => {
    const user = userEvent.setup()
    renderAt('/backtests/paper')

    const report = await screen.findByRole('region', { name: 'Report of Kernel VWAP' })
    await user.click(within(report).getByRole('button', { name: 'Make it now' }))

    expect(sent).toContainEqual({ method: 'POST', path: `/api/backtests/paper/${PAPER_RULE.id}/report` })
    expect(await within(report).findByText(/^Being made again to 20 Oct: waiting its turn, number 1 in line\./)).toBeInTheDocument()
    expect(within(report).getByRole('button', { name: 'Make it now' })).toBeDisabled()
  })

  it('says when a rule in shadow has no report yet, and how to give it a reference', async () => {
    paper = { rules: {}, live: null }
    renderAt('/backtests/paper')

    const report = await screen.findByRole('region', { name: 'Report of Kernel VWAP' })
    expect(within(report).getByText('None yet: the first is made after its first finished weekday, once the market is closed.')).toBeInTheDocument()
    expect(within(report).queryByRole('link', { name: 'Open the report' })).toBeNull()
    expect(screen.getByRole('link', { name: 'start a run from this rule' })).toHaveAttribute('href', `/backtests/new?rule=${PAPER_RULE.id}`)
  })
})

describe('a report', () => {
  it('opens its newest run from its own address', async () => {
    renderAt(`/backtests/paper/${PAPER_RULE.id}`)

    await waitFor(() => expect(address()).toBe(`/backtests/${PAPER_REPORT_ID}`))
  })

  it('names nothing for a rule that is not there', async () => {
    renderAt('/backtests/paper/0000000000ff')

    expect(await screen.findByRole('heading', { name: 'No such rule on paper' })).toBeInTheDocument()
  })

  it('is read as any backtest: the rule\'s signals named, under Paper trading, kept by WATA', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${PAPER_REPORT_ID}`)

    expect(await screen.findByRole('heading', { name: 'Paper: Kernel VWAP, since 2026-10-14' })).toBeInTheDocument()
    expect(screen.getByText(/^Signals Kernel VWAP raised · Capital\.com US100 · 1 min/)).toBeInTheDocument()
    expect(screen.getByText(/^Paper trading of Kernel VWAP: every day since 14 Oct/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Its days on paper' })).toHaveAttribute('href', `/backtests/paper#rule-${PAPER_RULE.id}`)
    expect(screen.getByText(/by v3 and v4 of its script/)).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Backtests.*Paper trading.*Paper: Kernel VWAP/)
    // Made again each night, the older removed: not the operator's to remove.
    await user.click(screen.getByRole('button', { name: 'More actions on this run' }))
    const menu = within(screen.getByRole('menu', { name: 'More actions on this run' }))
    expect(menu.queryByRole('menuitem', { name: /^Remove/ })).toBeNull()
    expect(screen.getByRole('link', { name: 'Run again with changes' })).toHaveAttribute('href', `/backtests/new?again=${PAPER_REPORT_ID}`)
  })

  it('is asked again with changes as a run of the same signals', async () => {
    renderAt(`/backtests/new?again=${PAPER_REPORT_ID}`)

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Next' }))

    expect(await screen.findByRole('radio', { name: /The signals a rule raised/ })).toBeChecked()
    expect(screen.getByRole('combobox', { name: 'The rule whose signals are traded' })).toHaveValue(PAPER_RULE.id)
  })
})
