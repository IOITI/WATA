import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { DailyPerformance, Performance, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { PerformancePage } from './PerformancePage'

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

function day(date: string, profit: number, trades: number, won: number): DailyPerformance {
  return {
    day_date: date,
    trade_count: trades,
    win_count: won,
    loss_count: trades - won,
    profit_loss: profit,
    cumulative_profit_loss: profit,
    performance_percent: profit / 10,
  }
}

const PERFORMANCE: Performance = {
  from: '2026-10-19',
  to: '2026-10-23',
  timezone: 'Europe/Paris',
  summary: {
    trade_count: 4,
    win_count: 2,
    loss_count: 2,
    win_rate: 50,
    profit_loss: 7.4,
    trading_days: 2,
    winning_days: 1,
    average_trade_percent: 0.4,
    best_day: { day_date: '2026-10-20', profit_loss: 12.4 },
    worst_day: { day_date: '2026-10-22', profit_loss: -5 },
  } as Performance['summary'],
  daily: [day('2026-10-20', 12.4, 3, 2), day('2026-10-22', -5, 1, 0)],
  by_action: [],
  by_instrument: [],
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path.startsWith('/api/performance')) {
        return new Response(JSON.stringify(PERFORMANCE), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Performance page', () => {
  it('lays the days of the period out as a calendar, by week', async () => {
    const { container } = render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/performance']}>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <WithTrail>
              <PerformancePage />
            </WithTrail>
          </AuthContext.Provider>
        </MemoryRouter>
      </QueryClientProvider>,
    )

    const panel = (await screen.findByRole('heading', { name: 'Calendar' })).closest('section') as HTMLElement
    expect(within(panel).getByRole('img', { name: /^Profit and loss of each day, as a calendar\./ })).toBeInTheDocument()
    expect(within(panel).getByText('1 day won, 1 lost')).toBeInTheDocument()
    // The five weekdays of the period's one week, the weekend after it not reached yet.
    expect(container.querySelectorAll('section rect.cj-cell')).toHaveLength(5)
  })
})
