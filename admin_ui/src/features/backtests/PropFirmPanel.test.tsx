import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/features/auth/auth-context'
import type { PropFirmKept, PropFirmState, Session } from '@/lib/types'
import { run, RUN_ID } from '@/test/backtests'

import { PropFirmPanel } from './PropFirmPanel'

const SESSION: Session = {
  authenticated: true, version: '0.71.0', timezone: 'Europe/Paris', actions_enabled: true, config_edit_enabled: true,
  backtests_enabled: true, totp_enabled: false, restart_required: false,
}

const FIGURES = {
  stoploss_percent: -10, trades: 42, days: 12, trades_per_day: 3.5, win_rate: 0.55, average_win_r: 0.62, average_loss_r: -0.48,
  r_series: [0.5, -1, 1.2], notes: ['42 trades: LuxAlgo advises a hundred or more for its bootstrap'],
}

const KEPT: PropFirmKept = {
  asked_at: '2026-10-31T09:00:00Z', bar: 0.5,
  asked: { tradesPerDay: 3.5, riskMode: 'percent-of-balance', riskValue: 0.75, productType: 'futures', accountSizeMin: 25_000, accountSizeMax: 100_000 },
  figures: FIGURES,
  answer: {
    bar: { minPassPerAttempt: 0.5 },
    passing: [{ name: 'Example Funding - 50K', accountSize: 50_000, currency: 'usd', passPerAttempt: 0.62, passCiLow: 0.6, passCiHigh: 0.64,
      fundedProbability: 0.41, expectedAttempts: 1.6, expectedCost: 552, evTotal: 830, meetsBar: true, flagIds: ['attempts-iid'] }],
    belowBar: [],
    disclaimer: 'Simulation, not prediction.',
  },
}

let state: PropFirmState = { kept: null, figures: FIGURES, refused: null }
let sent: { method: string; path: string; body: unknown }[] = []

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <PropFirmPanel run={run()} />
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  sent = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      sent.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined })
      if (path === `/api/backtests/${RUN_ID}/propfirms` && method === 'POST') return new Response(JSON.stringify(KEPT), { status: 200 })
      if (path === `/api/backtests/${RUN_ID}/propfirms`) return new Response(JSON.stringify(state), { status: 200 })
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => {
  state = { kept: null, figures: FIGURES, refused: null }
  vi.unstubAllGlobals()
})

describe('a run checked against funded challenges', () => {
  it('says what leaves for LuxAlgo before the click, sends the risk and the scope asked, and lists the answer', async () => {
    const user = userEvent.setup()
    renderPanel()
    expect(await screen.findByText(/^What leaves for LuxAlgo: this run's 42 results as R-multiples/)).toBeInTheDocument()
    expect(screen.getByText('42 trades: LuxAlgo advises a hundred or more for its bootstrap.')).toBeInTheDocument()

    const risk = screen.getByRole('spinbutton', { name: /Risk a trade/ })
    await user.clear(risk)
    await user.type(risk, '0.75')
    await user.click(screen.getByRole('button', { name: 'Check with LuxAlgo' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST')?.body).toEqual({
      risk_percent: 0.75, product_type: 'futures', account_min: 25_000, account_max: 100_000,
    }))
    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /Example Funding - 50K/ })
    expect(within(row).getByText('Meets the bar')).toBeInTheDocument()
    expect(within(row).getByText('95 %: 60% to 64%')).toBeInTheDocument()
    expect(screen.getByText(/The bar: a pass per attempt of 50% or more\./)).toBeInTheDocument()
    expect(screen.getByText('LuxAlgo: Simulation, not prediction.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Check again' })).toBeEnabled()
  })

  it('says why a run cannot be checked, and sends nothing', async () => {
    state = { kept: null, figures: null, refused: 'This run has no stop-loss: there is no risk to count its trades in R' }
    renderPanel()
    expect(await screen.findByText('Not checkable: This run has no stop-loss: there is no risk to count its trades in R.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Check with LuxAlgo' })).toBeNull()
    expect(sent.filter((request) => request.method === 'POST')).toEqual([])
  })

  it('holds a risk out of bounds back', async () => {
    const user = userEvent.setup()
    renderPanel()
    const risk = await screen.findByRole('spinbutton', { name: /Risk a trade/ })
    await user.clear(risk)
    await user.type(risk, '9')
    expect(screen.getByRole('button', { name: 'Check with LuxAlgo' })).toBeDisabled()
  })
})
