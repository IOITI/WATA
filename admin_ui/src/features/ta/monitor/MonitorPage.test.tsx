import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { BacktestPaper, Session, TaEngine, TaMonitor, TaMonitorNow } from '@/lib/types'
import { monitorPeriod, NOW } from '@/test/monitor'
import { WithTrail } from '@/test/WithTrail'

import { MonitorPage } from './MonitorPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.29.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const ENGINE: TaEngine = { enabled: true, status: 'warning', detail: 'A run of “EMA cross” took 16 s, over half of its limit (30 s).', engine: null, rules: {} }

let now: TaMonitorNow = NOW
let period: TaMonitor = monitorPeriod()
let paper: BacktestPaper = { rules: {} }
let asked: string[] = []

function serve() {
  asked = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const url = new URL(path, 'http://admin.test')
      asked.push(`${url.pathname}${url.search}`)
      if (url.pathname === '/api/ta/monitor/now') return json(now)
      if (url.pathname === '/api/ta/monitor') {
        const hours = Number(url.searchParams.get('hours'))
        return json({ ...period, hours, rule: url.searchParams.get('rule') })
      }
      if (url.pathname === '/api/ta/engine') return json(ENGINE)
      if (url.pathname === '/api/backtests/paper') return json(paper)
      return json({ detail: `Not served in this test: ${url.pathname}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

function renderPage(address = '/ta/monitor', session: Session = SESSION) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/ta/monitor', element: <WithTrail><MonitorPage /><Address /></WithTrail> }],
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
  now = NOW
  period = monitorPeriod()
  paper = { rules: {} }
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Engine monitor', () => {
  it('says the checks of the last minutes, each leading to the part of the page that says more', async () => {
    renderPage()

    const checks = await screen.findByRole('list', { name: 'Checks of the last minutes' })
    expect(within(checks).getByRole('link', { name: 'Runs within their limits' })).toHaveAttribute('href', '#rules')
    expect(within(checks).getByRole('link', { name: 'Live signals sent' })).toHaveAttribute('href', '#delivery')
    expect(within(checks).getByText('A run of “EMA cross” took 16 s, over half of its limit (30 s).')).toBeInTheDocument()
    expect(within(checks).getByLabelText('Failed')).toBeInTheDocument()
    // The tiles against their limits, the notes beside them.
    expect(screen.getByText('EMA cross, on the live runner; its limit 30 s')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Memory, against its limit' })).toHaveAttribute('aria-valuenow', '51')
    expect(within(screen.getByRole('list', { name: 'Notes' })).getByText(/waited 4 ticks/)).toBeInTheDocument()
    expect(screen.getByText('Kept 7 days, since 15 Oct, 10:00, last written 22 Oct, 11:59:57.')).toBeInTheDocument()
  })

  it('offers its periods as links, and reads the one chosen', async () => {
    const user = userEvent.setup()
    renderPage()
    const week = await screen.findByRole('link', { name: '7 days' })

    expect(week).toHaveAttribute('href', '/ta/monitor?hours=168')
    expect(screen.getByRole('link', { name: '24 h' })).toHaveAttribute('aria-current', 'page')
    await user.click(screen.getByRole('link', { name: '6 h' }))

    await waitFor(() => expect(asked).toContain('/api/ta/monitor?hours=6'))
    expect(screen.getByTestId('address')).toHaveTextContent('/ta/monitor?hours=6')
  })

  it('narrows the page to one rule, from its select or a rule’s link', async () => {
    const user = userEvent.setup()
    renderPage('/ta/monitor?hours=6')
    const select = await screen.findByRole('combobox', { name: 'Rule' })
    // Its rules are those of the period, once read.
    await screen.findByRole('option', { name: 'Claude · cross' })

    await user.selectOptions(select, 'bbbbbbbbbbbb')

    await waitFor(() => expect(asked).toContain('/api/ta/monitor?hours=6&rule=bbbbbbbbbbbb'))
    expect(screen.getByTestId('address')).toHaveTextContent('/ta/monitor?hours=6&rule=bbbbbbbbbbbb')
    expect(screen.getByRole('link', { name: 'EMA cross' })).toHaveAttribute('href', '/ta/monitor?hours=6&rule=aaaaaaaaaaaa')
  })

  it('lists each rule with its runs against its limit, its failures and its signals', async () => {
    renderPage()

    expect(await screen.findByText('41 ms · 80 ms')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'The slowest run of Claude · cross against its limit' })).toHaveAttribute('aria-valuenow', '72')
    expect(screen.getByText("Undeclared identifier 'slow' (script, line 12)")).toBeInTheDocument()
    expect(screen.getByText('412 ticks')).toBeInTheDocument()
    expect(screen.getByText('journaled in shadow')).toBeInTheDocument()
  })

  it('sets out the delivery of the live signals step by step, and what became of them', async () => {
    renderPage()
    const steps = await screen.findByText("Bar close → the engine's tick")
    const table = steps.closest('table')!

    expect(within(table).getByText('Bar close → position confirmed')).toBeInTheDocument()
    // A step no signal went through is left out.
    expect(within(table).queryByText("Sent → the trader's outcome, without an order")).not.toBeInTheDocument()
    expect(within(table).getByText('4.4 s')).toBeInTheDocument()
    expect(screen.getByText('Not sent: its bar closed too long before')).toBeInTheDocument()
    expect(screen.getByText('The bar closed 75 s ago, more than 60 s')).toBeInTheDocument()
    expect(screen.getByText('Max trades per day reached')).toBeInTheDocument()
  })

  it('sets a live rule’s positions beside a shadow rule’s days on paper', async () => {
    paper = {
      rules: {
        bbbbbbbbbbbb: {
          days: [{ day: '2026-10-21', run_id: '20261022-010000-aaaaaa', state: 'done', day_percent: 0.4, trades: 2, outside: false }],
          reference: null,
        },
      },
    }
    renderPage()

    const link = await screen.findByRole('link', { name: /^Last, 21 Oct: \+0\.40%$/ })
    expect(link).toHaveAttribute('href', '/backtests/20261022-010000-aaaaaa')
    expect(screen.getByText('1 · 1')).toBeInTheDocument()
    expect(screen.getByText('+0.81% a position')).toBeInTheDocument()
  })

  it('says when nothing was measured yet, and when measures could not be written', async () => {
    now = { ...NOW, recording: { ...NOW.recording, since: null, written_at: null, dropped: 3, error: 'ConnectionError: refused' } }
    period = monitorPeriod({ recording: now.recording })
    renderPage()

    expect(await screen.findByText(/^Nothing measured yet\./)).toBeInTheDocument()
    expect(screen.getByText('3 ticks of measures could not be written since the engine started: ConnectionError: refused')).toBeInTheDocument()
  })

  it('is named in the top bar under Technical Analysis', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Engine monitor' })).toBeInTheDocument()
    const [trail] = screen.getAllByRole('navigation', { name: 'Breadcrumb' })
    expect(within(trail).getByText('Technical Analysis')).toBeInTheDocument()
    expect(within(trail).getByText('Engine monitor')).toBeInTheDocument()
  })
})
