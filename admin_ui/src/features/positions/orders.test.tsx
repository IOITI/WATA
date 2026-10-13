import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { Order, Position, Session } from '@/lib/types'

import { OrdersPage } from './OrdersPage'
import { PositionsPage } from './PositionsPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.14.0',
  timezone: 'Europe/Paris',
  actions_enabled: false,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const POSITION = {
  position_id: '5098',
  action: 'long',
  position_status: 'Closed',
  position_kind: 'main',
  position_amount: 120,
  position_open_price: 9.31,
  position_close_price: 9.52,
  position_profit_loss: 25.2,
  position_total_open_price: 1117.2,
  position_total_close_price: 1142.4,
  position_total_performance_percent: 2.26,
  position_max_performance_percent: 3.4,
  position_close_reason: 'Performance (Trailing Stop)',
  execution_time_open: '2026-10-13T07:31:02.297Z',
  execution_time_close: '2026-10-13T08:02:11.270Z',
  order_id: '5012',
  related_order_id: [],
  instrument_name: 'TURBO LONG NASDAQ-100 KO 24150 SG',
  instrument_symbol: 'SG-NDX-L1',
  instrument_currency: 'EUR',
} as unknown as Position

const LINKED = {
  position_id: '5098',
  position_status: 'Closed',
  position_amount: 120,
  position_open_price: 9.31,
  position_close_price: 9.52,
  position_profit_loss: 25.2,
  position_total_performance_percent: 2.26,
  execution_time_open: POSITION.execution_time_open,
  execution_time_close: POSITION.execution_time_close,
  position_close_reason: 'Performance (Trailing Stop)',
}

const BUY = {
  order_id: '5012',
  action: 'long',
  buy_sell: 'Buy',
  order_amount: 120,
  order_type: 'Market',
  order_kind: 'main',
  order_time: '2026-10-13T07:31:02Z',
  position_id: '5098',
  instrument_name: POSITION.instrument_name,
  instrument_symbol: 'SG-NDX-L1',
  instrument_price: 9.3,
  instrument_currency: 'EUR',
  order_cost: 1.12,
  purpose: 'open',
  sent_at: '2026-10-13T07:31:02.120Z',
  accepted_at: '2026-10-13T07:31:02.310Z',
  confirmed_at: '2026-10-13T07:31:02.640Z',
  gave_up_at: null,
  lookups: 2,
  outcome: 'confirmed',
  cancel_accepted: null,
  detail: null,
  position: LINKED,
  signal: { signal_uuid: 'uuid-1', signal_timestamp: null, received_timestamp: '2026-10-13T07:31:01.950Z', decided_at: null },
} as unknown as Order

const SELL = {
  ...BUY,
  order_id: '5013',
  buy_sell: 'Sell',
  order_kind: 'close',
  order_time: '2026-10-13T08:02:11.100Z',
  instrument_price: 9.53,
  order_cost: null,
  purpose: 'close',
  sent_at: '2026-10-13T08:02:11.100Z',
  accepted_at: '2026-10-13T08:02:11.290Z',
  confirmed_at: '2026-10-13T08:02:11.600Z',
  lookups: 1,
  signal: null,
} as unknown as Order

const GIVEN_UP = {
  ...BUY,
  order_id: '5020',
  position_id: null,
  position: null,
  signal: null,
  order_time: '2026-10-13T09:15:00Z',
  sent_at: '2026-10-13T09:15:00.000Z',
  accepted_at: '2026-10-13T09:15:00.200Z',
  confirmed_at: null,
  gave_up_at: '2026-10-13T09:15:11.000Z',
  lookups: 14,
  outcome: 'gave_up',
  cancel_accepted: true,
} as unknown as Order

/** As WATA wrote orders before it kept their moments. */
const OLD = {
  ...BUY,
  order_id: '4001',
  order_time: '2026-09-01T07:31:03Z',
  purpose: null,
  sent_at: null,
  accepted_at: null,
  confirmed_at: null,
  lookups: null,
  outcome: null,
  signal: null,
} as unknown as Order

let requests: string[] = []

function serve(orders: Order[], { position = POSITION, positionOrders = [BUY, SELL] } = {}) {
  requests = []
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      requests.push(path)
      const url = new URL(path, 'http://admin.test')
      if (url.pathname === '/api/orders') return json({ items: orders, total: orders.length })
      if (url.pathname === '/api/positions') return json({ items: [position], total: 1 })
      if (url.pathname === `/api/positions/${position.position_id}`) return json({ position, orders: positionOrders })
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={[address]}>
          <Routes>
            <Route path="orders" element={<OrdersPage />} />
            <Route path="positions" element={<PositionsPage />} />
          </Routes>
          <Address />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

function rowOf(text: string) {
  return screen.getByText(text).closest('tr') as HTMLElement
}

function headers() {
  return screen.getAllByRole('columnheader').map((header) => header.textContent?.trim()).filter(Boolean)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the orders table', () => {
  it('says what came of each order and how long it took, with the position it gave as a link', async () => {
    serve([BUY, SELL, GIVEN_UP])
    renderAt('/orders')

    await screen.findByText('5012')
    const buy = rowOf('5012')
    expect(within(buy).getByText('Opened a position')).toBeInTheDocument()
    expect(within(buy).getByText('520 ms')).toBeInTheDocument()
    expect(within(buy).getByRole('link', { name: 'Position 5098' })).toHaveAttribute('href', '/positions?find=5098')

    const sell = rowOf('5013')
    expect(within(sell).getByText('Closed a position')).toBeInTheDocument()
    expect(within(sell).getByText('500 ms')).toBeInTheDocument()

    // What fits a 1440 px screen without cutting the order's id; side, type and cost are in the column list.
    expect(headers()).toEqual(['Time', 'Instrument', 'Direction', 'Result', 'Took', 'Quantity', 'Price', 'Order'])
  })

  it('shows an order that gave no position, how long the trader waited and that it was cancelled', async () => {
    serve([BUY, GIVEN_UP])
    renderAt('/orders')

    const row = await screen.findByText('5020').then((cell) => cell.closest('tr') as HTMLElement)
    expect(within(row).getByText('No position')).toBeInTheDocument()
    expect(within(row).getByText('cancelled')).toBeInTheDocument()
    expect(within(row).getByText('11.0 s')).toBeInTheDocument()
    expect(within(row).queryByRole('link')).not.toBeInTheDocument()
  })

  it('opens a row onto each moment of the order and the position it gave', async () => {
    const user = userEvent.setup()
    serve([BUY])
    renderAt('/orders')

    await user.click(await screen.findByRole('button', { name: 'Expand row details' }))

    const moments = screen.getByRole('list', { name: 'The moments of order 5012' })
    const lines = within(moments)
      .getAllByRole('listitem')
      .map((step) => [...step.children].map((part) => part.textContent))
    expect(lines).toEqual([
      ['Signal received', '09:31:01.950'],
      ['Order sent', '09:31:02.120', '170 ms after the signal'],
      ['Accepted by Saxo', '09:31:02.310', '190 ms after it left'],
      ['Filled at Saxo', '09:31:02.297', "Saxo's clock: 177 ms after the order left"],
      ['Seen by WATA', '09:31:02.640', '330 ms after Saxo accepted the order, at the 2nd look'],
    ])
    expect(screen.getByText('WATA saw the position 520 ms after the order left.')).toBeInTheDocument()
    expect(screen.getByText('Performance (Trailing Stop)')).toBeInTheDocument()
    expect(screen.getByText(/120 at €9.31 → €9.52/)).toBeInTheDocument()
  })

  it('opens a given-up order onto what Saxo answered to its cancellation', async () => {
    const user = userEvent.setup()
    serve([{ ...GIVEN_UP, cancel_accepted: false, detail: 'SaxoApiError: Order not found' } as Order])
    renderAt('/orders')

    expect(await screen.findByText('cancel refused')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Expand row details' }))

    expect(screen.getByText('Refused by Saxo: SaxoApiError: Order not found')).toBeInTheDocument()
    expect(screen.getByText(/check the account at Saxo/)).toBeInTheDocument()
  })

  it('gives the figures of the period for the entries whose moments were kept', async () => {
    serve([BUY, SELL, GIVEN_UP, OLD])
    renderAt('/orders')

    const typical = (await screen.findByText('Order to position')).parentElement as HTMLElement
    expect(within(typical).getByText('520 ms')).toBeInTheDocument()
    expect(within(typical).getByText(/2 entries in this period/)).toBeInTheDocument()
    const slowest = screen.getByText('Slowest entry').parentElement as HTMLElement
    expect(within(slowest).getByText('Order 5012')).toBeInTheDocument()
    const without = screen.getByText('Without a position').parentElement as HTMLElement
    expect(within(without).getByText('1')).toBeInTheDocument()
  })

  it('says that older orders have no moments instead of showing empty figures', async () => {
    serve([OLD])
    renderAt('/orders')

    expect(await screen.findByText(/No order of this period has its moments/)).toBeInTheDocument()
    expect(screen.queryByText('Order to position')).not.toBeInTheDocument()
    // Written only once its position was confirmed: it opened one.
    expect(within(rowOf('4001')).getByText('Opened a position')).toBeInTheDocument()
    expect(within(rowOf('4001')).getByText('—')).toBeInTheDocument()
  })

  it('keeps the type and the cost one click away', async () => {
    const user = userEvent.setup()
    serve([BUY])
    renderAt('/orders')

    await screen.findByText('5012')
    expect(screen.queryByText('Market · main')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Toggle visible columns' }))
    await user.click(screen.getByRole('checkbox', { name: /Type/ }))

    expect(screen.getByText('Market · main')).toBeInTheDocument()
  })
})

describe('a link from a position to its order', () => {
  it('finds the order whenever it was placed and shows it opened', async () => {
    serve([BUY, SELL, GIVEN_UP])
    renderAt('/orders?find=5013')

    expect(await screen.findByRole('list', { name: 'The moments of order 5013' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Search/)).toHaveValue('5013')
    expect(screen.queryByText('5020')).not.toBeInTheDocument()
    // All time: an order of last year is still one request away.
    expect(requests.filter((path) => path.startsWith('/api/orders'))).toEqual(['/api/orders?limit=2000'])
  })

  it('without one, the page starts on the last thirty days and nothing opened', async () => {
    serve([BUY])
    renderAt('/orders')

    await screen.findByText('5012')
    expect(requests[0]).toMatch(/^\/api\/orders\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}&limit=2000$/)
    expect(screen.queryByRole('list', { name: /The moments of order/ })).not.toBeInTheDocument()
  })
})

describe('a link from an order to its position', () => {
  it('goes to the position among all of them, with the order that opened it and the one that closed it', async () => {
    const user = userEvent.setup()
    serve([BUY])
    renderAt('/orders')

    await user.click(await screen.findByRole('link', { name: 'Position 5098' }))

    expect(screen.getByLabelText('address')).toHaveTextContent('/positions?find=5098')
    expect(await screen.findByText('Opened by order')).toBeInTheDocument()
    expect(screen.getByText('Closed by order')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'The moments of order 5012' })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'The moments of order 5013' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '5013' })).toHaveAttribute('href', '/orders?find=5013')
    // Open or closed: the position asked for must be there.
    expect(requests.some((path) => path.startsWith('/api/positions?') && !path.includes('status='))).toBe(true)
    expect(screen.getByPlaceholderText(/Search/)).toHaveValue('5098')
  })

  it('stops looking for it once another tab is chosen', async () => {
    const user = userEvent.setup()
    serve([BUY])
    renderAt('/positions?find=5098')

    await screen.findByText('Opened by order')
    // The tabs come before the table, which has a column of the same name.
    await user.click(screen.getAllByRole('button', { name: 'Closed' })[0])

    expect(screen.getByLabelText('address')).toHaveTextContent('/positions')
    expect(screen.getByLabelText('address')).not.toHaveTextContent('find')
    expect(screen.getByPlaceholderText(/Search/)).toHaveValue('')
  })
})

describe('the orders of a position', () => {
  async function open(position: Position, positionOrders: Order[]) {
    serve([], { position, positionOrders })
    renderAt(`/positions?find=${position.position_id}`)
    await screen.findByText('Position', { selector: 'dt' })
  }

  it('says that Saxo closed it when there is no sell order of WATA', async () => {
    await open({ ...POSITION, position_close_reason: 'SaxoAPI' } as Position, [BUY])

    expect(screen.getByText(/Closed at Saxo, not by a sell order WATA recorded/)).toBeInTheDocument()
    expect(screen.queryByText('Closed by order')).not.toBeInTheDocument()
  })

  it('says that sell orders were not kept for a position of before', async () => {
    await open(POSITION, [OLD])

    expect(screen.getByText(/Its sell order was not recorded/)).toBeInTheDocument()
    expect(screen.getByText(/Placed before WATA kept the moments of an order/)).toBeInTheDocument()
  })

  it('says nothing of a sell order while the position is open', async () => {
    await open({ ...POSITION, position_status: 'Open', execution_time_close: null } as Position, [BUY])

    expect(screen.getByText('Opened by order')).toBeInTheDocument()
    expect(screen.queryByText(/sell order/)).not.toBeInTheDocument()
  })

  it('says so when no order at all was recorded', async () => {
    await open(POSITION, [])

    expect(screen.getByText('No order recorded for this position.')).toBeInTheDocument()
  })
})
