import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import { forgetLiveSamples } from '@/features/positions/liveSamples'
import type { Health, Overview, Session } from '@/lib/types'
import { openPosition, overview } from '@/test/overview'
import { WithTrail } from '@/test/WithTrail'

import { DashboardPage } from './DashboardPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.33.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const HEALTH: Health = {
  version: '0.33.0',
  checked_at: '2026-10-23T14:00:00Z',
  status: 'ok',
  services: [],
  queues: [],
  watchlist: null,
  config_modified_at: '2026-10-23T12:00:00Z',
}

function serve(answer: Overview) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const body = path === '/api/overview' ? answer : path === '/api/health' ? HEALTH : path === '/api/signals' ? { items: [] } : null
      if (body === null) throw new Error(`Unexpected request: ${path}`)
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
}

function renderDashboard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <MemoryRouter>
            <WithTrail>
              <DashboardPage />
            </WithTrail>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>
    </AuthContext.Provider>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  forgetLiveSamples()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the dashboard', () => {
  it('draws each open position live, under the table', async () => {
    serve(overview({ open_positions: [openPosition('5002', 'TURBO LONG NASDAQ-100 KO 24150 SG'), openPosition('5003', 'TURBO LONG NASDAQ-100 KO 24200 SG')] }))
    renderDashboard()

    const charts = await screen.findAllByRole('figure')
    expect(charts).toHaveLength(2)
    expect(within(charts[0]).getByRole('img', { name: 'Performance of TURBO LONG NASDAQ-100 KO 24150 SG over the last 15 minutes' })).toBeInTheDocument()
    expect(within(charts[0]).getByRole('button', { name: 'Pause live updates' })).toBeInTheDocument()
  })

  it('gives the day’s profit with the days before it to scrub, today at the end', async () => {
    serve(overview())
    renderDashboard()

    const tile = await screen.findByRole('group', { name: 'Day profit and loss: +€4.20' })
    expect(within(tile).getByText('1 closed trade')).toBeInTheDocument()
    expect(within(tile).getByRole('img', { name: 'Day profit and loss trend from +€12.40 to +€4.20' })).toBeInTheDocument()
  })

  it('gives New York’s time beside the session’s, and the US open in the latter', async () => {
    serve(overview())
    renderDashboard()

    await screen.findByText('No open position.')
    const boards = screen.getAllByRole('region', { name: /^World times: \d\d:\d\d New York, \d\d:\d\d Paris$/ })
    expect(boards.length).toBeGreaterThan(0)
    expect(within(boards[0]).getByText(/^US OPEN \d\d:\d\d PARIS · [56] H APART$/)).toBeInTheDocument()
  })

  it('draws nothing live without an open position', async () => {
    serve(overview())
    renderDashboard()

    expect(await screen.findByText('No open position.')).toBeInTheDocument()
    expect(screen.queryByRole('figure')).not.toBeInTheDocument()
  })
})
