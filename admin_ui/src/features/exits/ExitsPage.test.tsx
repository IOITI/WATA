import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { Exits, GroupStats, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { ExitsPage } from './ExitsPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.32.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

function stats(trades: number, profit: number): GroupStats {
  return {
    trade_count: trades,
    win_count: profit > 0 ? trades : 0,
    loss_count: profit < 0 ? trades : 0,
    win_rate: profit > 0 ? 100 : 0,
    profit_loss: profit,
    average_percent: profit > 0 ? 1 : -1,
    average_best_percent: 1.5,
  }
}

const EXITS: Exits = {
  from: '2026-07-26',
  to: '2026-10-23',
  timezone: 'Europe/Paris',
  summary: { ...stats(3, 8), average_given_back: 1.2, turned_negative_count: 1, turned_negative_percent: 33.33 },
  by_reason: [{ reason: 'Trailing stop', ...stats(3, 8) }],
  by_hour: [{ hour: 9, ...stats(2, 10) }, { hour: 15, ...stats(1, -2) }],
  by_weekday_hour: [{ weekday: 1, hour: 9, ...stats(2, 10) }, { weekday: 4, hour: 15, ...stats(1, -2) }],
  by_holding_time: [{ bucket: 'Under 5 min', ...stats(3, 8) }],
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path.startsWith('/api/exits')) {
        return new Response(JSON.stringify(EXITS), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Exits page', () => {
  it('places the trades by the weekday and hour they were opened, in the trading time zone', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/exits']}>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <WithTrail>
              <ExitsPage />
            </WithTrail>
          </AuthContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const panel = (await screen.findByRole('heading', { name: 'By weekday and hour of entry' })).closest('section') as HTMLElement
    expect(panel).toHaveTextContent('Profit and loss of the trades opened in each hour of each weekday (Europe/Paris)')
    expect(within(panel).getByRole('img', { name: /^Profit and loss of the trades by weekday and hour of entry\./ })).toBeInTheDocument()
    expect(within(panel).getByText('+€8.00 over 3 trades')).toBeInTheDocument()
    expect(within(panel).getByRole('rowheader', { name: 'Monday, 09:00' })).toBeInTheDocument()
    expect(within(panel).getByRole('rowheader', { name: 'Thursday, 15:00' })).toBeInTheDocument()
  })
})
