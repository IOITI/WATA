import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ThemeToggle } from '@/components/ThemeToggle'
import { AuthContext } from '@/features/auth/auth-context'
import type { JournalEntry, Position, PriceBarRow, PriceContext, PriceInstrument, PriceRangeRow, Session } from '@/lib/types'
import { charts, resetCharts, seriesOf, theChart } from '@/test/fakeCharts'
import { stubSystemTheme } from '@/test/systemTheme'

import { PricesPage } from './PricesPage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const SESSION: Session = {
  authenticated: true,
  version: '0.12.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

// Monday 12 October 2026, half past twelve in Paris.
const NOW = new Date('2026-10-12T10:30:00Z')
const TODAY = '2026-10-12'
const utc = (iso: string) => Date.parse(iso) / 1000

const INDEX = {
  id: 1, uic: 1909050, asset_type: 'StockIndex', symbol: 'NDX.I', description: 'NASDAQ 100', currency: 'USD',
  role: 'underlying', indice: 'us100', underlying_uic: 1909050, direction: null, barrier: null, issuer: null,
} as PriceInstrument
const LONG = {
  id: 2, uic: 40000002, asset_type: 'WarrantOpenEndKnockOut', symbol: 'SG-NDX-L2',
  description: 'NASDAQ 100 Turbo Long 24150 SG', currency: 'EUR', role: 'turbo', indice: 'us100',
  underlying_uic: 1909050, direction: 'long', barrier: 24150, issuer: 'SG',
} as PriceInstrument
const SHORT = { ...LONG, id: 5, uic: 40000005, symbol: 'SG-NDX-S5', description: 'NASDAQ 100 Turbo Short 25900 SG', direction: 'short', barrier: 25900 } as PriceInstrument

/** Three bars from 10:00:00 Paris time, `step` seconds apart; the ask five cents above the bid. */
function bars(first: number, step = 10): PriceBarRow[] {
  return [0, 1, 2].map((index) => {
    const bid = first + index * 0.1
    return [utc('2026-10-12T08:00:00Z') + index * step, bid, bid + 0.04, bid - 0.02, bid + 0.02, bid + 0.05, bid + 0.09, bid + 0.03, bid + 0.07, 3 + index]
  })
}

const TRADE = {
  position_id: '5001', action: 'long', position_open_price: 8.45, position_close_price: 8.6,
  position_total_performance_percent: 1.78, execution_time_open: '2026-10-12T08:00:03Z',
  execution_time_close: '2026-10-12T08:00:24Z', instrument_currency: 'EUR',
} as Position
const SIGNALS = [
  { signal_uuid: 'a', action: 'long', indice: 'us100', outcome: 'executed', decided_at: '2026-10-12T08:00:01Z' },
  { signal_uuid: 'b', action: 'long', indice: 'us100', outcome: 'refused', decided_at: '2026-10-12T08:00:12Z' },
] as JournalEntry[]
const GAP = { start: '2026-10-12T08:40:00Z', end: '2026-10-12T08:47:00Z', seconds: 420, reason: 'down' } as const

const T0 = utc('2026-10-12T08:00:00Z')

/**
 * Three range bars: one from 10:00:00 Paris time that lasted 30 s, the next from
 * 10:00:20, and after a pause one that is still growing at 11:00:00.
 */
function rangeRows(first: number, withIndex: boolean): PriceRangeRow[] {
  const index = (level: number) => (withIndex ? level : null)
  return [
    [T0, first, first + 0.1, first, first + 0.1, T0, T0 + 30, true, false, index(25000.5)],
    [T0 + 20, first + 0.1, first + 0.1, first, first, T0 + 20, T0 + 60, true, false, index(24990.25)],
    [T0 + 3600, first + 0.3, first + 0.34, first + 0.3, first + 0.32, T0 + 3600, T0 + 3610, false, true, index(25020)],
  ]
}

interface Server {
  instruments?: PriceInstrument[]
  context?: Partial<PriceContext>
  /** An answer other than the bars, for the bars route. */
  barsError?: { status: number; detail: string }
  /** Sizes of range bars the server refuses, with what it says. */
  rangeRefused?: { size: number; detail: string }
  /** The instruments have no ask: range bars of the ask are none. */
  noAsk?: boolean
}

let requests: string[] = []

function serve({ instruments = [INDEX, LONG, SHORT], context = {}, barsError, rangeRefused, noAsk }: Server = {}) {
  requests = []
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      requests.push(path)
      const url = new URL(path, 'http://admin.test')
      const from = url.searchParams.get('from')
      const to = url.searchParams.get('to')
      const instrument = instruments.find((item) => item.id === Number(url.searchParams.get('instrument_id')))
      if (url.pathname === '/api/prices/instruments') {
        return json({ from, to, timezone: 'Europe/Paris', items: instruments })
      }
      if (url.pathname === '/api/prices/bars') {
        if (barsError) return json({ detail: barsError.detail }, barsError.status)
        const interval = Number(url.searchParams.get('interval'))
        const first = instrument?.role === 'underlying' ? 25000 : 8.4
        return json({ instrument, from, to, timezone: 'Europe/Paris', interval, columns: [], bars: bars(first, interval) })
      }
      if (url.pathname === '/api/prices/range-bars') {
        const asked = url.searchParams.get('size')
        const turbo = instrument?.role === 'turbo'
        // Left to the server: twenty points on the index, twenty cents on a turbo.
        const size = asked === null ? (turbo ? 0.2 : 20) : Number(asked)
        if (rangeRefused && size === rangeRefused.size) return json({ detail: rangeRefused.detail }, 422)
        const under = instruments.find((item) => item.id === Number(url.searchParams.get('under'))) ?? null
        return json({
          instrument, under, from, to, timezone: 'Europe/Paris', side: url.searchParams.get('side'),
          tick: 0.01, decimals: 2, ticks: Math.round(size * 100), size, auto: asked === null,
          columns: [], bars: noAsk && url.searchParams.get('side') === 'ask' ? [] : rangeRows(turbo ? 8.4 : 25000, under !== null),
        })
      }
      if (url.pathname === '/api/prices/context') {
        return json({ instrument, trades: [], signals: [], gaps: [], ...context })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

let queryClient: QueryClient

function renderPage(address = '/prices') {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={[address]}>
          <Routes>
            <Route
              path="prices"
              element={
                <>
                  <PricesPage />
                  <Address />
                </>
              }
            />
            <Route path="*" element={<Address />} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

const barRequests = () => requests.filter((path) => path.startsWith('/api/prices/bars'))
const rangeRequests = () => requests.filter((path) => path.startsWith('/api/prices/range-bars'))
const rangeIsDrawn = () => waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(4))
const ticksField = () => screen.getByLabelText('Range in ticks (R)') as HTMLInputElement
const sizeField = () => screen.getByLabelText(/Range as a price move/) as HTMLInputElement
const chartIsDrawn = () => waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data.length).toBeGreaterThan(0))
const address = () => screen.getByLabelText('address').textContent

beforeEach(() => {
  resetCharts()
  window.localStorage.clear()
  // Only the clock: the page's own timers and those of the test tools keep running.
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('opening the page', () => {
  it('shows today, the index, in ten-second bars of the bid', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(requests).toContain(`/api/prices/instruments?from=${TODAY}&to=${TODAY}`)
    expect(barRequests()).toEqual([`/api/prices/bars?instrument_id=1&from=${TODAY}&to=${TODAY}&interval=10`])
    expect(requests).toContain(`/api/prices/context?instrument_id=1&from=${TODAY}&to=${TODAY}`)
    for (const name of ['Today', '10 s', 'Bid']) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    }
    expect(seriesOf(theChart(), 'Candlestick')?.data[0]).toEqual({
      // Ten in the morning in Paris, which is how the chart must be given 08:00 UTC.
      time: utc('2026-10-12T10:00:00Z'), open: 25000, high: 25000.04, low: 24999.98, close: 25000.02,
    })
    expect(seriesOf(theChart(), 'Line')).toBeUndefined()
  })

  it('names the page in the tab', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(document.title).toBe('Prices · WATA Admin')
  })

  it('carries the notice and the link the charting library asks for', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(screen.getByText(/TradingView Lightweight Charts™ Copyright \(c\) 2025 TradingView, Inc\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'https://www.tradingview.com/' })).toHaveAttribute('href', 'https://www.tradingview.com/')
  })

  it('reads a view from the address', async () => {
    serve()
    renderPage('/prices?instrument=5&from=2026-10-06&to=2026-10-12&interval=300&side=ask')
    await chartIsDrawn()

    expect(barRequests()).toContain('/api/prices/bars?instrument_id=5&from=2026-10-06&to=2026-10-12&interval=300')
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '5 min' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('heading', { name: 'Short 25,900 · SG' })).toBeInTheDocument()
  })

  it('shows the index when the instrument of the address was not recorded in the period', async () => {
    serve()
    renderPage('/prices?instrument=99')
    await chartIsDrawn()

    expect(screen.getByRole('heading', { name: 'NASDAQ 100' })).toBeInTheDocument()
  })
})

describe('choosing what to look at', () => {
  it('puts the index under a turbo, the trades on the turbo and the signals on the index', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: /Long 24,150 · SG/ }))

    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))
    expect(barRequests()).toContain(`/api/prices/bars?instrument_id=2&from=${TODAY}&to=${TODAY}&interval=10`)
    expect(seriesOf(theChart(), 'Candlestick')?.data[0]).toMatchObject({ open: 8.4 })
    expect(seriesOf(theChart(), 'Line')?.pane).toBe(1)
    await waitFor(() =>
      expect(seriesOf(theChart(), 'Candlestick')?.markers.map((marker) => [marker.text, marker.price])).toEqual([
        ['Buy €8.45', 8.45],
        ['+1.78%', 8.6],
      ]),
    )
    expect(seriesOf(theChart(), 'Line')?.markers.map((marker) => marker.text)).toEqual(['Long', 'Long refused'])
    expect(address()).toBe('/prices?instrument=2')
    expect(within(screen.getByRole('list', { name: 'Legend' })).getByText('NASDAQ 100, lower panel')).toBeInTheDocument()
  })

  it('asks for the index only once when going from one turbo to the next', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?instrument=2')
    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))

    await user.click(screen.getByRole('button', { name: /Short 25,900 · SG/ }))

    await screen.findByRole('heading', { name: 'Short 25,900 · SG' })
    await waitFor(() => expect(barRequests().filter((path) => path.includes('instrument_id=5'))).toHaveLength(1))
    expect(barRequests().filter((path) => path.includes('instrument_id=1'))).toHaveLength(1)
  })

  it('shows trades and signals together on the index, which has no panel under it', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    renderPage()
    await chartIsDrawn()

    await waitFor(() =>
      expect(seriesOf(theChart(), 'Candlestick')?.markers.map((marker) => marker.text)).toEqual([
        'Long',
        'Long',
        'Long refused',
        'Long +1.78%',
      ]),
    )
    // On the index a trade only says when: its prices are the turbo's.
    expect(seriesOf(theChart(), 'Candlestick')?.markers.every((marker) => marker.price === undefined)).toBe(true)
  })

  it('asks for longer bars, and says so in the address', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: '1 min' }))

    await waitFor(() => expect(barRequests()).toContain(`/api/prices/bars?instrument_id=1&from=${TODAY}&to=${TODAY}&interval=60`))
    expect(address()).toBe('/prices?interval=60')
    await waitFor(() => expect(theChart().timeScaleOptions.secondsVisible).toBe(false))
  })

  it('redraws the ask from what it already has', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?instrument=2')
    await chartIsDrawn()
    const asked = barRequests().length

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    // The ask opens five cents above the bid's 8.40.
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data[0].open).toBeCloseTo(8.45, 6))
    expect(seriesOf(theChart(), 'Candlestick')?.data[0].close).toBeCloseTo(8.47, 6)
    expect(barRequests()).toHaveLength(asked)
    expect(address()).toBe('/prices?instrument=2&side=ask')
  })

  it('takes another period, in bars that fit one chart, and no longer offers the shorter ones', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: '7 days' }))

    await waitFor(() => expect(barRequests()).toContain('/api/prices/bars?instrument_id=1&from=2026-10-06&to=2026-10-12&interval=60'))
    expect(address()).toBe('/prices?from=2026-10-06&to=2026-10-12')
    expect(screen.getByRole('button', { name: '10 s' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '30 s' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '1 min' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('finds an instrument by a few letters or digits', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.type(screen.getByRole('searchbox', { name: 'Search the instruments' }), '259')

    expect(screen.getByRole('button', { name: /Short 25,900 · SG/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Long 24,150 · SG/ })).not.toBeInTheDocument()

    await user.clear(screen.getByRole('searchbox', { name: 'Search the instruments' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search the instruments' }), 'zzz')

    expect(screen.getByText('No instrument matches “zzz”.')).toBeInTheDocument()
  })

  it('brings the whole period back on demand', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()
    const fits = theChart().fits

    await user.click(screen.getByRole('button', { name: 'Whole period' }))

    expect(theChart().fits).toBe(fits + 1)
  })
})

describe('range bars', () => {
  it('replace the bars of a fixed length, in a size the server chooses for the period', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Range' }))
    await rangeIsDrawn()

    expect(rangeRequests()).toEqual([`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=bid`])
    expect(address()).toBe('/prices?range=auto')
    expect(screen.getByRole('button', { name: 'Range' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '10 s' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')
    expect(ticksField().value).toBe('2000')
    expect(sizeField().value).toBe('20.00')
    // Ten in the morning in Paris; one empty slot where the data paused, just before the bar that follows.
    const shown = utc('2026-10-12T10:00:00Z')
    expect(seriesOf(theChart(), 'Candlestick')?.data).toEqual([
      { time: shown, open: 25000, high: 25000.1, low: 25000, close: 25000.1 },
      { time: shown + 20, open: 25000.1, high: 25000.1, low: 25000, close: 25000 },
      { time: shown + 3599 },
      { time: shown + 3600, open: 25000.3, high: 25000.34, low: 25000.3, close: 25000.32 },
    ])
    expect(theChart().timeScaleOptions.secondsVisible).toBe(true)
  })

  it('are rebuilt in the range typed, in ticks or as a price, and the address keeps the price', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=auto')
    await rangeIsDrawn()

    await user.clear(ticksField())
    await user.type(ticksField(), '1500{Enter}')

    await waitFor(() =>
      expect(rangeRequests()).toContain(`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=bid&size=15`),
    )
    expect(address()).toBe('/prices?range=15')
    await waitFor(() => expect(sizeField().value).toBe('15.00'))
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'false')
    expect(await screen.findByRole('heading', { name: 'Range bars of 15.00 (1500R)' })).toBeInTheDocument()
    // The answer came and the field is still the one being typed in: the next try needs no click.
    expect(ticksField()).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Apply' })).not.toBeInTheDocument()
  })

  it('step to a larger or a smaller range with one click', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Larger range' }))

    await waitFor(() => expect(address()).toBe('/prices?range=20'))
    await waitFor(() => expect(rangeRequests().at(-1)).toContain('size=20'))
  })

  it('are counted in the currency of a turbo and in points for an index', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=auto')
    await rangeIsDrawn()
    expect(screen.getByLabelText('Range as a price move, in pts')).toHaveValue('20.00')

    await user.click(screen.getByRole('button', { name: /Long 24,150 · SG/ }))

    expect(await screen.findByLabelText('Range as a price move, in EUR')).toBeInTheDocument()
    expect(await screen.findByText('1R = 0.01 EUR')).toBeInTheDocument()
  })

  it('leave the chart in place, dimmed, while the first of them are on their way', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Range' }))
    await rangeIsDrawn()

    // One chart from start to finish: it was never taken down for a grey block.
    expect(charts).toHaveLength(1)
    expect(charts[0].removed).toBe(false)
  })

  it('keep, in Auto, the range chosen the first time when the same view is read again', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=auto')
    await rangeIsDrawn()
    expect(rangeRequests()).toEqual([`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=bid`])

    // The thirty-second refresh.
    await queryClient.refetchQueries({ queryKey: ['prices', 'range'] })

    expect(rangeRequests().at(-1)).toBe(`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=bid&size=20`)
    expect(address()).toBe('/prices?range=auto')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')

    // Asking for Auto again is asking for the choice to be made again.
    await user.click(screen.getByRole('button', { name: 'Auto' }))

    await waitFor(() => expect(rangeRequests()).toHaveLength(3))
    expect(rangeRequests().at(-1)).toBe(`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=bid`)
  })

  it('of an ask that was never recorded say that it is the ask that is missing', async () => {
    serve({ noAsk: true })
    renderPage('/prices?instrument=2&range=0.2&side=ask')

    expect(
      await screen.findByText('No ask was recorded for Long 24,150 · SG in this period: the bid is what there is.'),
    ).toBeInTheDocument()
  })

  it('are read from an address that names a range', async () => {
    serve()
    renderPage('/prices?instrument=2&range=0.2&side=ask')
    await rangeIsDrawn()

    expect(rangeRequests()).toEqual([
      `/api/prices/range-bars?instrument_id=2&from=${TODAY}&to=${TODAY}&side=ask&size=0.2&under=1`,
    ])
    expect(barRequests()).toEqual([])
    expect(ticksField().value).toBe('20')
  })

  it('of a turbo come with its index, on the same times, and carry the trades and the signals', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    renderPage('/prices?instrument=2&range=0.2')
    await rangeIsDrawn()

    const candles = seriesOf(theChart(), 'Candlestick')
    const line = seriesOf(theChart(), 'Line')
    expect(line?.pane).toBe(1)
    expect(line?.data.map((point) => point.time)).toEqual(candles?.data.map((point) => point.time))
    expect(line?.data.map((point) => point.value)).toEqual([25000.5, 24990.25, undefined, 25020])
    // Bought at 10:00:03, in the first bar; sold at 10:00:24, in the second, which began at 10:00:20.
    const shown = utc('2026-10-12T10:00:00Z')
    await waitFor(() =>
      expect(candles?.markers.map((marker) => [marker.text, marker.time, marker.price])).toEqual([
        ['Buy €8.45', shown, 8.45],
        ['+1.78%', shown + 20, 8.6],
      ]),
    )
    expect(line?.markers.map((marker) => [marker.text, marker.time])).toEqual([['Long', shown], ['Long refused', shown]])
  })

  it('are built from the side shown', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    await waitFor(() =>
      expect(rangeRequests()).toContain(`/api/prices/range-bars?instrument_id=1&from=${TODAY}&to=${TODAY}&side=ask&size=15`),
    )
  })

  it('keep one range for the index and another for the turbos', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=auto')
    await rangeIsDrawn()
    // Fifteen points suit the index.
    await user.clear(sizeField())
    await user.type(sizeField(), '15{Enter}')
    await waitFor(() => expect(address()).toBe('/prices?range=15'))

    // They mean nothing on a turbo, which starts on a range of its own.
    await user.click(screen.getByRole('button', { name: /Long 24,150 · SG/ }))
    await waitFor(() => expect(address()).toBe('/prices?instrument=2&range=auto'))
    await waitFor(() => expect(ticksField().value).toBe('20'))
    await user.clear(sizeField())
    await user.type(sizeField(), '0.1{Enter}')
    await waitFor(() => expect(address()).toBe('/prices?instrument=2&range=0.1'))

    // Another turbo keeps it; the index gets its own back; so does the turbo after it.
    await user.click(screen.getByRole('button', { name: /Short 25,900 · SG/ }))
    await waitFor(() => expect(address()).toBe('/prices?instrument=5&range=0.1'))
    await user.click(screen.getByRole('button', { name: /NASDAQ 100\s*NDX\.I/ }))
    await waitFor(() => expect(address()).toBe('/prices?instrument=1&range=15'))
    await user.click(screen.getByRole('button', { name: /Long 24,150 · SG/ }))
    await waitFor(() => expect(address()).toBe('/prices?instrument=2&range=0.1'))
  })

  it('start on the range last used for that kind of instrument', async () => {
    window.localStorage.setItem('wata.prices.range.underlying', '15')
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Range' }))

    await waitFor(() => expect(address()).toBe('/prices?range=15'))
  })

  it('give way to bars of a fixed length again', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: '1 min' }))

    await waitFor(() => expect(barRequests()).toContain(`/api/prices/bars?instrument_id=1&from=${TODAY}&to=${TODAY}&interval=60`))
    expect(address()).toBe('/prices?interval=60')
    expect(screen.queryByLabelText('Range in ticks (R)')).not.toBeInTheDocument()
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(3))
  })

  it('are written out: prices, how long the bar took, whether it is over, the index', async () => {
    serve()
    renderPage('/prices?instrument=2&range=0.2')
    await rangeIsDrawn()

    const readout = screen.getByText('Last bar').closest('dl') as HTMLElement
    // When it really began and ended, to the ten seconds.
    expect(readout).toHaveTextContent('12 Oct, 11:00:00 → 11:00:10')
    expect(readout).toHaveTextContent('BidO8.70H8.74L8.70C8.72')
    expect(readout).toHaveTextContent('Lasted10 s')
    expect(readout).toHaveTextContent('In progress')
    expect(readout).toHaveTextContent('NASDAQ 10025,020.00')

    theChart().hover(utc('2026-10-12T10:00:00Z'))

    await waitFor(() => expect(readout).toHaveTextContent('12 Oct, 10:00:00 → 10:00:30'))
    expect(readout).toHaveTextContent('Lasted30 s')
    expect(readout).not.toHaveTextContent('In progress')
    expect(readout).toHaveTextContent('NASDAQ 10025,000.50')

    // The second bar is at 10:00:20 on the chart, and that is also when it began.
    theChart().hover(utc('2026-10-12T10:00:20Z'))

    await waitFor(() => expect(readout).toHaveTextContent('12 Oct, 10:00:20 → 10:01:00'))
    expect(readout).toHaveTextContent('Lasted40 s')

    // The slot left empty by the pause.
    theChart().hover(utc('2026-10-12T10:59:59Z'))

    await waitFor(() => expect(readout).toHaveTextContent('Nothing recorded'))
  })

  it('are listed in a table, the latest first, with what became of each', async () => {
    serve()
    renderPage('/prices?instrument=2&range=0.2')
    await rangeIsDrawn()

    const table = await screen.findByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent?.trim())
    expect(headers).toEqual(['Began', 'Ended', 'Open', 'High', 'Low', 'Close', 'Lasted', 'State', 'NASDAQ 100'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('12 Oct, 11:00:00')
    expect(rows[0]).toHaveTextContent('11:00:10')
    expect(rows[0]).toHaveTextContent('In progress')
    expect(rows[1]).toHaveTextContent('12 Oct, 10:00:20')
    expect(rows[1]).toHaveTextContent('10:01:00')
    expect(rows[1]).toHaveTextContent('40 s')
    // A complete bar is the rule and says nothing of it.
    expect(rows[1]).not.toHaveTextContent('Complete')
    expect(rows[2]).toHaveTextContent('25,000.50')
    expect(screen.getByRole('heading', { name: 'Range bars of 0.20 (20R)' })).toBeInTheDocument()
  })

  it('explain themselves in a note that a finger and a keyboard can open, closed until asked for', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/prices?range=15')
    await rangeIsDrawn()

    const summary = screen.getByText('About range bars')
    const note = summary.closest('details') as HTMLDetailsElement
    expect(note.open).toBe(false)

    await user.click(summary)

    expect(note.open).toBe(true)
    expect(note).toHaveTextContent('A range bar ends when the price has left a range of the size set')
    expect(note).toHaveTextContent('1R is one tick, the smallest step of these prices (0.01 pts)')
    expect(note).toHaveTextContent('Built from the ten-second bars of the bid. Inside ten seconds the low is taken to come before the high')
    expect(note).toHaveTextContent('Nothing is made up for a time that was not recorded')
  })

  it('are not there to explain when the bars are of a fixed length', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(screen.queryByText('About range bars')).not.toBeInTheDocument()
  })

  it('keep the range asked for in the fields when the server refuses it, so that another can be typed', async () => {
    serve({ rangeRefused: { size: 0.01, detail: 'More than 12000 range bars in this period: choose a larger range or a shorter period' } })
    const user = userEvent.setup()
    renderPage('/prices?instrument=2&range=0.2')
    await rangeIsDrawn()

    await user.clear(ticksField())
    await user.type(ticksField(), '1{Enter}')

    // Said beside the fields, in the server's words: an answer about what was typed, not a breakdown.
    expect(await screen.findByRole('alert')).toHaveTextContent('More than 12000 range bars in this period: choose a larger range or a shorter period')
    expect(screen.queryByText(/Could not/)).not.toBeInTheDocument()
    await waitFor(() => expect(ticksField().value).toBe('1'))
    expect(ticksField()).toBeEnabled()
    expect(ticksField()).toHaveFocus()
    // The chart that was there is still there.
    expect(charts).toHaveLength(1)
    expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(4)

    await user.clear(ticksField())
    await user.type(ticksField(), '30{Enter}')

    await waitFor(() => expect(address()).toBe('/prices?instrument=2&range=0.3'))
    await rangeIsDrawn()
    expect(screen.queryByText(/More than 12000 range bars/)).not.toBeInTheDocument()
  })
})

describe('another theme', () => {
  it('rebuilds the chart, which drew with the colours it read when it was made', async () => {
    serve()
    stubSystemTheme(true)
    const user = userEvent.setup()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <MemoryRouter initialEntries={['/prices?instrument=2']}>
              <ThemeToggle />
              <Routes>
                <Route path="prices" element={<PricesPage />} />
              </Routes>
            </MemoryRouter>
          </AuthContext.Provider>
        </ThemeProvider>
      </QueryClientProvider>,
    )
    await chartIsDrawn()
    const first = theChart()

    await user.click(screen.getByRole('button', { name: /^Theme:/ }))

    await waitFor(() => expect(first.removed).toBe(true))
    // The new one has the same bars, the index under them included.
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(3))
    expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3)
    expect(charts).toHaveLength(2)
  })
})

describe('reading the figures', () => {
  it('writes out the last bar, then the bar under the pointer', async () => {
    serve()
    renderPage('/prices?instrument=2')
    await chartIsDrawn()

    const readout = screen.getByText('Last bar').closest('dl') as HTMLElement
    expect(readout).toHaveTextContent('12 Oct, 10:00:20')
    expect(readout).toHaveTextContent('Spread0.05')
    expect(readout).toHaveTextContent('Quote changes5')
    expect(readout).toHaveTextContent('NASDAQ 10025,000.22')

    theChart().hover(utc('2026-10-12T10:00:00Z'))

    await waitFor(() => expect(readout).toHaveTextContent('12 Oct, 10:00:00'))
    expect(readout).not.toHaveTextContent('Last bar')
    expect(readout).toHaveTextContent('BidO8.40H8.44L8.38C8.42')
    expect(readout).toHaveTextContent('AskO8.45H8.49L8.43C8.47')
    expect(readout).toHaveTextContent('Quote changes3')
  })

  it('says that nothing was recorded where the pointer is on a gap', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    theChart().hover(utc('2026-10-12T10:45:00Z'))

    expect(await screen.findByText('Nothing recorded')).toBeInTheDocument()
  })

  it('lists the bars in a table, the latest first', async () => {
    serve()
    renderPage('/prices?instrument=2')
    await chartIsDrawn()

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('12 Oct, 10:00:20')
    expect(rows[2]).toHaveTextContent('12 Oct, 10:00:00')
    const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent?.trim())
    expect(headers).toEqual(['Time', 'Bid open', 'Bid high', 'Bid low', 'Bid close', 'Ask close', 'Spread', 'Quote changes'])
  })
})

describe('what was not recorded', () => {
  it('lists the gaps with how long they lasted and why', async () => {
    serve({ context: { gaps: [GAP, { ...GAP, start: '2026-10-12T09:10:00Z', end: '2026-10-12T09:12:00Z', seconds: 120, reason: 'silent' }] } })
    renderPage()
    await chartIsDrawn()

    const panel = (await screen.findByRole('heading', { name: 'Recording gaps' })).closest('section') as HTMLElement
    await within(panel).findByText('Recorder not running')
    expect(panel).toHaveTextContent('2 in trading hours, 9 min in all')
    const [, first, second] = within(panel).getAllByRole('row')
    expect(first).toHaveTextContent('12 Oct, 10:40:00')
    expect(first).toHaveTextContent('10:47:00')
    expect(first).toHaveTextContent('7 min')
    expect(second).toHaveTextContent('No quote arriving')
  })

  it('says so when the period was recorded without interruption', async () => {
    serve({ context: { gaps: [] } })
    renderPage()
    await chartIsDrawn()

    expect(await screen.findByText('Recorded without interruption during trading hours.')).toBeInTheDocument()
  })

  it('says that gaps are not worked out for a long period', async () => {
    serve({ context: { gaps: null } })
    renderPage()
    await chartIsDrawn()

    expect(await screen.findByText('Not worked out for a period longer than 31 days.')).toBeInTheDocument()
  })

  it('explains how to switch the recorder on when the period has nothing', async () => {
    serve({ instruments: [] })
    renderPage()

    expect(await screen.findByRole('heading', { name: 'No price recorded in this period' })).toBeInTheDocument()
    expect(screen.getByText('trade.config.price_recorder.enabled')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'configuration' })).toHaveAttribute('href', '/config')
    expect(screen.getByRole('link', { name: 'System page' })).toHaveAttribute('href', '/system')
    expect(barRequests()).toEqual([])
    expect(() => theChart()).toThrow()
  })

  it('shows what the server answers when the bars cannot be given', async () => {
    serve({ barsError: { status: 422, detail: 'More than 12000 bars in this period at 10 seconds: choose a longer interval or a shorter period' } })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('More than 12000 bars in this period at 10 seconds')
    expect(() => theChart()).toThrow()
  })
})
