import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { Experiment, Experiments, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { familyTrials, lockboxText } from './experiments'
import { ExperimentsPage } from './ExperimentsPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.23.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

function experiment(changes: Partial<Experiment> = {}): Experiment {
  return {
    id: '1a2b3c', title: 'EMA cross on 5 min', hypothesis: 'A cross of the fast average leads the next hour', family: 'ema-cross',
    plan: 'Run, sweep the lengths, stress the best', made_by: 'claude', opened_at: '2026-10-20T08:00:00Z',
    runs: [{ run: '20261020-080500-aaaaaa', kind: 'run', trials: 1, lockbox: false, note: '', at: '2026-10-20T08:05:00Z' }],
    elsewhere: [], trials: 1, verdict: null, notes: null, concluded_at: null, ...changes,
  }
}

describe('the experiment journal, read', () => {
  it('counts every configuration a family of ideas was tried with', () => {
    const trials = familyTrials([experiment({ trials: 13 }), experiment({ id: 'x', trials: 2 }), experiment({ id: 'y', family: 'rsi', trials: 4 })])

    expect([...trials]).toEqual([['ema-cross', 15], ['rsi', 4]])
  })

  it('says the lock box in words', () => {
    expect(lockboxText(null)).toMatch(/No lock box/)
    expect(lockboxText({ from: '2026-10-14', to: null, set_at: null, set_by: 'claude' })).toBe('Every day from 14 Oct 2026 is kept out.')
    expect(lockboxText({ from: '2026-10-14', to: '2026-10-16', set_at: null, set_by: null })).toBe('14 Oct 2026 to 16 Oct 2026 are kept out.')
  })
})

let journal: Experiments
let sent: { method: string; path: string; body: unknown }[] = []

function serve() {
  sent = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/research/experiments') return json(journal)
      if (path === '/api/research/lockbox') {
        const { start, end } = body as { start: string | null; end: string | null }
        journal = { ...journal, lockbox: start ? { from: start, to: end, set_at: '2026-10-20T09:00:00Z', set_by: 'operator' } : null }
        return json({ lockbox: journal.lockbox })
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/backtests/experiments', element: <WithTrail><ExperimentsPage /></WithTrail> }],
    { initialEntries: ['/backtests/experiments'] },
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
  journal = {
    experiments: [
      experiment({
        id: '4d5e6f', title: 'RSI bounce', family: 'rsi', runs: [], trials: 35,
        elsewhere: [{ trials: 35, note: 'A Python port screened on the Mac, 12 Jun to 16 Oct', at: '2026-10-20T09:30:00Z' }],
      }),
      experiment({ verdict: 'rejected', notes: 'Did not beat random entries (61 %)', concluded_at: '2026-10-20T10:00:00Z' }),
    ],
    lockbox: { from: '2026-10-14', to: null, set_at: '2026-10-19T20:00:00Z', set_by: 'claude' },
  }
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Experiments page', () => {
  it('lists each experiment with its runs and conclusion, under Backtests', async () => {
    renderPage()

    expect(await screen.findByText('RSI bounce')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '20261020-080500-aaaaaa' })).toHaveAttribute('href', '/backtests/20261020-080500-aaaaaa')
    const concluded = screen.getByText('Did not beat random entries (61 %)').closest('tr') as HTMLElement
    expect(within(concluded).getByText('Rejected')).toBeInTheDocument()
    expect(screen.getByText('Every day from 14 Oct 2026 is kept out.')).toBeInTheDocument()
    // Trials made outside the runs: said with what they were, without a link, and counted in the family.
    const elsewhere = screen.getByText('+35 tried elsewhere: A Python port screened on the Mac, 12 Jun to 16 Oct')
    expect(elsewhere.closest('a')).toBeNull()
    expect(within(elsewhere.closest('tr') as HTMLElement).getByText('35 tried in all')).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Analysis.*Backtests.*Experiments/)
  })

  it('lets the operator move the lock box, and clear it', async () => {
    const user = userEvent.setup()
    renderPage()

    const end = await screen.findByLabelText('To (empty: every day after)')
    await user.type(end, '2026-10-16')
    await user.click(screen.getByRole('button', { name: 'Save the lock box' }))
    expect(await screen.findByText('14 Oct 2026 to 16 Oct 2026 are kept out.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear it' }))

    await waitFor(() =>
      expect(sent.filter((request) => request.method === 'PUT').map((request) => request.body)).toEqual([
        { start: '2026-10-14', end: '2026-10-16' },
        { start: null, end: null },
      ]),
    )
    expect(await screen.findByText(/No lock box/)).toBeInTheDocument()
  })
})
