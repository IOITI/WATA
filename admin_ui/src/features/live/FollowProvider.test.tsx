import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { OrderRequest, Position, PositionDetail, Session } from '@/lib/types'

import { useFollow, type Followed } from './follow-context'
import { FollowProvider } from './FollowProvider'

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

const OPEN = {
  position_id: '5002',
  action: 'long',
  instrument_name: 'TURBO LONG NASDAQ-100 KO 24150 SG',
  instrument_currency: 'EUR',
  position_amount: 260,
  position_open_price: 8.42,
  position_status: 'Open',
  execution_time_open: '2026-10-23T12:30:00Z',
} as Position

const SOLD = {
  ...OPEN,
  position_status: 'Closed',
  position_close_price: 8.52,
  position_profit_loss: 26,
  position_total_performance_percent: 1.19,
  position_close_reason: 'Explicit Close (position 5002)',
  execution_time_close: new Date().toISOString(),
} as Position

function refusedSell(): OrderRequest {
  return {
    request_id: 'r1',
    requested_at: new Date().toISOString(),
    answered_at: new Date().toISOString(),
    recorded_by: 'position_monitor',
    operation: 'place',
    purpose: 'close',
    position_id: '5002',
    signal_uuid: null,
    action: 'long',
    reason: 'Explicit Close (position 5002)',
    instrument_uic: 1,
    asset_type: 'WarrantKnockOut',
    instrument_name: OPEN.instrument_name,
    instrument_symbol: null,
    instrument_currency: 'EUR',
    buy_sell: 'Sell',
    amount: 260,
    order_type: 'Market',
    price_seen: 8.5,
    outcome: 'refused',
    order_id: null,
    http_status: 400,
    error_code: 'OtherError',
    error_message: 'Instrument not tradable',
    request_body: null,
    response_body: null,
  }
}

/** What the server answers for the position: changed by the test between two reads. */
let served: PositionDetail = { position: OPEN, orders: [], requests: [] }

function serve() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path === '/api/positions/5002') {
        return new Response(JSON.stringify(served), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
}

function Asker({ followed }: { followed: Followed }) {
  const follow = useFollow()
  return (
    <button type="button" onClick={() => follow?.(followed)}>
      Ask
    </button>
  )
}

function renderShell() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const close: Followed = {
    kind: 'close',
    positionId: '5002',
    instrument: OPEN.instrument_name,
    amount: 260,
    askedAt: Date.now(),
  }
  render(
    <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <FollowProvider>
            <Asker followed={close} />
          </FollowProvider>
        </MemoryRouter>
      </QueryClientProvider>
    </AuthContext.Provider>,
  )
  return queryClient
}

afterEach(() => {
  vi.unstubAllGlobals()
  served = { position: OPEN, orders: [], requests: [] }
})

describe('the card that follows a close', () => {
  it('follows it from the ask to the sale, which a live event makes it read at once', async () => {
    serve()
    const user = userEvent.setup()
    const queryClient = renderShell()

    await user.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('Close asked')).toBeInTheDocument()
    expect(screen.getByText('260 × TURBO LONG NASDAQ-100 KO 24150 SG')).toBeInTheDocument()

    // What a "position closed" event does: the position's query is read again.
    served = { position: SOLD, orders: [], requests: [] }
    await queryClient.invalidateQueries({ queryKey: ['position'] })

    expect(await screen.findByText('Sold')).toBeInTheDocument()
    expect(screen.getByText('+€26.00 (+1.19%)')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Open the position/ })).toHaveAttribute('href', '/positions?find=5002')
  })

  it('gives Saxo’s refusal, as Saxo wrote it', async () => {
    served = { position: OPEN, orders: [], requests: [refusedSell()] }
    serve()
    const user = userEvent.setup()
    renderShell()

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(await screen.findByText('Saxo refused the sell')).toBeInTheDocument()
    expect(screen.getByText(/“Instrument not tradable” \(OtherError, HTTP 400\)/)).toBeInTheDocument()
  })

  it('is put away with its close button', async () => {
    serve()
    const user = userEvent.setup()
    renderShell()

    await user.click(screen.getByRole('button', { name: 'Ask' }))
    await screen.findByText('Close asked')
    await user.click(screen.getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(screen.queryByText('Close asked')).not.toBeInTheDocument())
  })
})
