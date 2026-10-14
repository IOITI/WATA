import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ThemeToggle } from '@/components/ThemeToggle'
import { AuthContext } from '@/features/auth/auth-context'
import type {
  AssetDayStatistics,
  DataAsset,
  InstrumentAvailability,
  JournalEntry,
  Position,
  PriceBarRow,
  PriceContext,
  PriceRangeRow,
  Session,
} from '@/lib/types'
import { charts, resetCharts, seriesOf, theChart } from '@/test/fakeCharts'
import { stubSystemTheme } from '@/test/systemTheme'

import { AssetPage } from './AssetPage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const SESSION: Session = {
  authenticated: true,
  version: '0.18.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

// Monday 12 October 2026, half past twelve in Paris.
const NOW = new Date('2026-10-12T10:30:00Z')
const TODAY = '2026-10-12'
const utc = (iso: string) => Date.parse(iso) / 1000

function saxo(id: number, changes: Partial<DataAsset>): DataAsset {
  return {
    provider: 'saxo', key: String(id), ref: `saxo/${id}`, id, uic: 40000000 + id, asset_type: 'WarrantOpenEndKnockOut',
    symbol: null, description: null, currency: 'EUR', role: 'turbo', indice: 'us100', underlying_uic: 1909050,
    direction: 'long', barrier: null, issuer: 'SG', first_t: utc('2026-10-12T06:00:00Z'), last_t: utc('2026-10-12T10:29:50Z'),
    last_bid: 8.45, last_ask: 8.5, status: 'recording', ...changes,
  }
}

const INDEX = saxo(1, {
  uic: 1909050, asset_type: 'StockIndex', symbol: 'NDX.I', description: 'NASDAQ 100', currency: 'USD', role: 'underlying',
  direction: null, issuer: null, last_bid: 25000.5, last_ask: 25000.5,
})
const LONG = saxo(2, { symbol: 'SG-NDX-L2', description: 'NASDAQ 100 Turbo Long 24150 SG', barrier: 24150 })
const SHORT = saxo(5, { symbol: 'SG-NDX-S5', description: 'NASDAQ 100 Turbo Short 25900 SG', direction: 'short', barrier: 25900 })
const HISTORY: DataAsset = {
  provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', id: null, uic: null, asset_type: 'Dukascopy',
  symbol: 'USATECHIDXUSD', description: 'US Tech 100 (Dukascopy)', currency: 'USD', role: 'underlying', indice: 'us100',
  underlying_uic: null, direction: null, barrier: null, issuer: null, first_t: utc('2026-10-05T00:00:00Z'),
  last_t: utc('2026-10-09T21:59:50Z'), last_bid: 25100, last_ask: 25101.5, status: 'downloaded', days: 5,
}
const ASSETS = [INDEX, LONG, SHORT, HISTORY]

/** Three bars from 10:00:00 Paris time, `step` seconds apart; the ask five cents above the bid. */
function bars(first: number, step = 10): PriceBarRow[] {
  return [0, 1, 2].map((index) => {
    const bid = first + index * 0.1
    return [utc('2026-10-12T08:00:00Z') + index * step, bid, bid + 0.04, bid - 0.02, bid + 0.02, bid + 0.05, bid + 0.09, bid + 0.03, bid + 0.07, 3 + index]
  })
}

const TRADE = {
  position_id: '5001', action: 'long', position_status: 'Closed', position_open_price: 8.45, position_close_price: 8.6,
  position_profit_loss: 39, position_total_performance_percent: 1.78, execution_time_open: '2026-10-12T08:00:03Z',
  execution_time_close: '2026-10-12T08:00:24Z', instrument_currency: 'EUR', instrument_name: 'Turbo Long 24150',
} as Position
const SIGNALS = [
  { signal_uuid: 'a', action: 'long', indice: 'us100', outcome: 'executed', decided_at: '2026-10-12T08:00:01Z' },
  { signal_uuid: 'b', action: 'long', indice: 'us100', outcome: 'refused', decided_at: '2026-10-12T08:00:12Z' },
] as JournalEntry[]
const GAP = { start: '2026-10-12T08:40:00Z', end: '2026-10-12T08:47:00Z', seconds: 420, reason: 'down' } as const

/** What was recorded of an instrument quoted from 08:00 to 22:00 in Paris, with nothing missing unless said. */
function recorded(missing: Partial<InstrumentAvailability> = {}): InstrumentAvailability {
  return {
    bars: 5040, updates: 12840, first_bar: '2026-10-12T06:00:00Z', last_bar: '2026-10-12T19:59:50Z',
    quoted_seconds: 50400, missing_seconds: 0, holes: 0, hole_seconds: 0, stops: [], stretches: [], stretch_count: 0,
    knocked_out_at: null, without_ask: 0, delayed_minutes: 0, issues: [], ...missing,
  }
}
/** Twelve minutes without a quote from 11:00 in Paris. */
const HOLED = recorded({
  bars: 4968, quoted_seconds: 49680, missing_seconds: 720, holes: 1, hole_seconds: 720, stretch_count: 1, issues: ['holes'],
  stretches: [{ start: '2026-10-12T09:00:00Z', end: '2026-10-12T09:12:00Z', seconds: 720, reason: 'unquoted' }],
})

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

function day(changes: Partial<AssetDayStatistics>): AssetDayStatistics {
  return {
    day: TODAY, first: '2026-10-12T06:00:00Z', last: '2026-10-12T19:59:50Z', bars: 5040, updates: 12840,
    open: 8.4, high: 8.9, low: 8.1, close: 8.6, change: 0.2, change_percent: 2.38, range: 0.8, range_percent: 9.52,
    spread_average: 0.05, spread_widest: 0.09, without_ask: 0, trading_seconds: 50400, covered: 1, volume: null,
    ...changes,
  }
}

interface Server {
  context?: Partial<PriceContext>
  /** An answer other than the bars, for the bars route. */
  barsError?: { status: number; detail: string }
  /** Sizes of range bars the server refuses, with what it says. */
  rangeRefused?: { size: number; detail: string }
  /** The assets have no ask: range bars of the ask are none. */
  noAsk?: boolean
  /** What is missing from each instrument, by id; null: not worked out for the period. */
  availability?: Record<number, InstrumentAvailability> | null
  /** The last day with data of each asset, by ref: today by default. */
  lastDays?: Record<string, string>
  statistics?: AssetDayStatistics[]
}

let requests: string[] = []

function serve({
  context = {},
  barsError,
  rangeRefused,
  noAsk,
  availability = {},
  lastDays = {},
  statistics = [day({})],
}: Server = {}) {
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
      if (url.pathname === '/api/data/providers/saxo/availability') {
        return json({ from, to, timezone: 'Europe/Paris', max_days: 7, items: availability })
      }
      if (url.pathname === '/api/backtests/prices') {
        return json({
          from, to, label: url.searchParams.get('label') ?? 'us100', symbol: 'USATECHIDXUSD',
          days: [{ day: '2026-10-09', state: 'ready', hours: 24, hours_expected: 24 }],
          ready: { count: 1, first: '2026-10-09', last: '2026-10-09' }, fetch: { state: 'idle', lines: [] },
          keep: { enabled: false, days: 120, state: 'off', missing: 0, settled: 0, failures: 0, next_at: null, limits: { days: 730 } },
        })
      }
      const match = url.pathname.match(/^\/api\/data\/assets\/(saxo|dukascopy)\/([^/]+)(?:\/(.+))?$/)
      const asset = match ? ASSETS.find((item) => item.ref === `${match[1]}/${match[2]}`) : undefined
      if (!match) throw new Error(`Unexpected request: ${path}`)
      if (!asset) return json({ detail: `No recorded instrument '${match[2]}'` }, 404)
      const lastDay = lastDays[asset.ref] ?? TODAY
      switch (match[3]) {
        case undefined:
          return json({
            asset, timezone: 'Europe/Paris', first_day: '2026-09-14', last_day: lastDay,
            index: asset.role === 'turbo' ? INDEX : null,
            recording:
              asset.provider === 'saxo'
                ? { first_seen_at: '2026-10-12T05:59:00Z', last_seen_at: '2026-10-12T10:29:00Z', retention_months: 0, record_volume: false, refresh_interval_seconds: 300 }
                : { symbol: 'USATECHIDXUSD', label: 'us100', fetchable: true, days: 5, first_file: '2026-10-05', last_file: '2026-10-09', bytes: 1500000, location: 'dukascopy/bars/us100', source: 'https://datafeed.dukascopy.com/datafeed', price_scale: 1000 },
            max_days:
              asset.provider === 'saxo'
                ? { chart: 366, range: 31, statistics: 31, availability: 7, gaps: 31 }
                : { chart: 31, range: 31, statistics: 31, availability: 0, gaps: 0 },
          })
        case 'bars': {
          if (barsError) return json({ detail: barsError.detail }, barsError.status)
          const interval = Number(url.searchParams.get('interval'))
          const first = asset.role === 'underlying' ? 25000 : 8.4
          return json({ instrument: asset, from, to, timezone: 'Europe/Paris', interval, columns: [], bars: bars(first, interval) })
        }
        case 'range-bars': {
          const asked = url.searchParams.get('size')
          const turbo = asset.role === 'turbo'
          // Left to the server: twenty points on the index, twenty cents on a turbo.
          const size = asked === null ? (turbo ? 0.2 : 20) : Number(asked)
          if (rangeRefused && size === rangeRefused.size) return json({ detail: rangeRefused.detail }, 422)
          const under = ASSETS.find((item) => item.id !== null && item.id === Number(url.searchParams.get('under'))) ?? null
          return json({
            instrument: asset, under, from, to, timezone: 'Europe/Paris', side: url.searchParams.get('side'),
            tick: 0.01, decimals: 2, ticks: Math.round(size * 100), size, auto: asked === null,
            columns: [], bars: noAsk && url.searchParams.get('side') === 'ask' ? [] : rangeRows(turbo ? 8.4 : 25000, under !== null),
          })
        }
        case 'context':
          return json({ instrument: asset, trades: [], signals: [], gaps: asset.provider === 'saxo' ? [] : null, volume: null, ...context })
        case 'statistics':
          return json({
            instrument: asset, from, to, timezone: 'Europe/Paris', spread: asset.role === 'turbo' || asset.provider === 'dukascopy',
            days: statistics,
            spread_by_time: [
              { slot: '08:00', bars: 180, spread_average: 0.06, spread_widest: 0.09, without_ask: 0 },
              { slot: '15:30', bars: 180, spread_average: 0.12, spread_widest: 0.2, without_ask: 2 },
            ],
          })
        default:
          throw new Error(`Unexpected request: ${path}`)
      }
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

let queryClient: QueryClient

function renderPage(address = '/data/assets/saxo/1') {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={[address]}>
          <Routes>
            <Route
              path="data/assets/:provider/:key"
              element={
                <>
                  <AssetPage />
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

const barRequests = () => requests.filter((path) => path.includes('/bars?'))
const rangeRequests = () => requests.filter((path) => path.includes('/range-bars?'))
const rangeIsDrawn = () => waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(4))
const ticksField = () => screen.getByLabelText('Range in ticks (R)') as HTMLInputElement
const sizeField = () => screen.getByLabelText(/Range as a price move/) as HTMLInputElement
const chartIsDrawn = () => waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data.length).toBeGreaterThan(0))
const address = () => screen.getByLabelText('address').textContent
const title = (name: string) => screen.getByRole('heading', { level: 1, name })

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

describe('opening an asset', () => {
  it('shows its last day with data, in ten-second bars of the bid', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(requests).toContain('/api/data/assets/saxo/1')
    expect(barRequests()).toEqual([`/api/data/assets/saxo/1/bars?from=${TODAY}&to=${TODAY}&interval=10`])
    expect(requests).toContain(`/api/data/assets/saxo/1/context?from=${TODAY}&to=${TODAY}`)
    for (const name of ['Today', '10 s', 'Bid']) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-pressed', 'true')
    }
    expect(seriesOf(theChart(), 'Candlestick')?.data[0]).toEqual({
      // Ten in the morning in Paris, which is how the chart must be given 08:00 UTC.
      time: utc('2026-10-12T10:00:00Z'), open: 25000, high: 25000.04, low: 24999.98, close: 25000.02,
    })
    expect(seriesOf(theChart(), 'Line')).toBeUndefined()
    expect(address()).toBe('/data/assets/saxo/1')
  })

  it('is named in the tab and in its title', async () => {
    serve()
    renderPage()
    await chartIsDrawn()

    expect(document.title).toBe('NASDAQ 100 · WATA Admin')
    expect(title('NASDAQ 100')).toBeInTheDocument()
  })

  it('says where it is, from the dashboard to itself', async () => {
    serve()
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()

    const trail = within(screen.getByRole('navigation', { name: 'Breadcrumb' }))
    expect(trail.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(trail.getByRole('link', { name: 'Assets View' })).toHaveAttribute('href', '/data/assets')
    // The section has no page of its own; the asset is where the reader is.
    expect(trail.queryByRole('link', { name: 'Data' })).not.toBeInTheDocument()
    expect(trail.getByText('Long 24,150 · SG').closest('[aria-current="page"]')).not.toBeNull()
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
    renderPage('/data/assets/saxo/5?from=2026-10-06&to=2026-10-12&interval=300&side=ask')
    await chartIsDrawn()

    expect(barRequests()).toContain('/api/data/assets/saxo/5/bars?from=2026-10-06&to=2026-10-12&interval=300')
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '5 min' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-pressed', 'true')
    expect(title('Short 25,900 · SG')).toBeInTheDocument()
  })

  it('opens an asset no longer quoted on its last day with data, and counts the periods back from it', async () => {
    serve({ lastDays: { 'saxo/2': '2026-10-09' } })
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()

    expect(barRequests()).toContain('/api/data/assets/saxo/2/bars?from=2026-10-09&to=2026-10-09&interval=10')
    expect(screen.getByRole('button', { name: 'Last day' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Day before' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Today' })).not.toBeInTheDocument()
    expect(screen.getByText(/Its last day with data is 09 Oct 2026/)).toBeInTheDocument()
    // Nothing of it is read again by itself: its day is over.
    expect(screen.queryByText(/Read again every 30 seconds/)).not.toBeInTheDocument()
    expect(address()).toBe('/data/assets/saxo/2')
  })

  it('says so when the asset is not stored, and where every asset is', async () => {
    serve()
    renderPage('/data/assets/saxo/99')

    expect(await screen.findByRole('heading', { name: 'This asset is not stored' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Every asset stored' })).toHaveAttribute('href', '/data/assets')
    expect(barRequests()).toEqual([])
  })
})

describe('choosing what to look at', () => {
  it('puts the index under a turbo, the trades on the turbo and the signals on the index', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    renderPage('/data/assets/saxo/2')

    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))
    expect(barRequests()).toEqual(
      expect.arrayContaining([
        `/api/data/assets/saxo/2/bars?from=${TODAY}&to=${TODAY}&interval=10`,
        `/api/data/assets/saxo/1/bars?from=${TODAY}&to=${TODAY}&interval=10`,
      ]),
    )
    expect(seriesOf(theChart(), 'Candlestick')?.data[0]).toMatchObject({ open: 8.4 })
    expect(seriesOf(theChart(), 'Line')?.pane).toBe(1)
    await waitFor(() =>
      expect(seriesOf(theChart(), 'Candlestick')?.markers.map((marker) => [marker.text, marker.price])).toEqual([
        ['Buy €8.45', 8.45],
        ['+1.78%', 8.6],
      ]),
    )
    expect(seriesOf(theChart(), 'Line')?.markers.map((marker) => marker.text)).toEqual(['Long', 'Long refused'])
    expect(within(screen.getByRole('list', { name: 'Legend' })).getByText('NASDAQ 100, lower panel')).toBeInTheDocument()
  })

  it('asks which index was recorded in another period', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: '7 days' }))

    await waitFor(() => expect(requests).toContain('/api/data/assets/saxo/2?from=2026-10-06&to=2026-10-12'))
    await waitFor(() => expect(barRequests()).toContain('/api/data/assets/saxo/1/bars?from=2026-10-06&to=2026-10-12&interval=60'))
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

    await waitFor(() => expect(barRequests()).toContain(`/api/data/assets/saxo/1/bars?from=${TODAY}&to=${TODAY}&interval=60`))
    expect(address()).toBe('/data/assets/saxo/1?interval=60')
    await waitFor(() => expect(theChart().timeScaleOptions.secondsVisible).toBe(false))
  })

  it('redraws the ask from what it already has', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()
    const asked = barRequests().length

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    // The ask opens five cents above the bid's 8.40.
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data[0].open).toBeCloseTo(8.45, 6))
    expect(seriesOf(theChart(), 'Candlestick')?.data[0].close).toBeCloseTo(8.47, 6)
    expect(barRequests()).toHaveLength(asked)
    expect(address()).toBe('/data/assets/saxo/2?side=ask')
  })

  it('takes another period, in bars that fit one chart, and no longer offers the shorter ones', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: '7 days' }))

    await waitFor(() => expect(barRequests()).toContain('/api/data/assets/saxo/1/bars?from=2026-10-06&to=2026-10-12&interval=60'))
    expect(address()).toBe('/data/assets/saxo/1?from=2026-10-06&to=2026-10-12')
    expect(screen.getByRole('button', { name: '10 s' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '30 s' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '1 min' })).toHaveAttribute('aria-pressed', 'true')
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

  it('keeps the tab in the address, and the period from one tab to the next', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/2?from=2026-10-06&to=2026-10-12')
    await chartIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Statistics' }))

    await waitFor(() => expect(address()).toBe('/data/assets/saxo/2?from=2026-10-06&to=2026-10-12&tab=statistics'))
    await waitFor(() => expect(requests).toContain('/api/data/assets/saxo/2/statistics?from=2026-10-06&to=2026-10-12'))
    await user.click(screen.getByRole('button', { name: 'Chart' }))
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/2?from=2026-10-06&to=2026-10-12'))
  })
})

describe('what is missing from the asset shown', () => {
  it('lists its holes with the recorder’s own gaps', async () => {
    serve({ context: { gaps: [GAP] }, availability: { 1: recorded(), 2: HOLED, 5: recorded() } })
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()

    const panel = (await screen.findByRole('heading', { name: 'Recording gaps' })).closest('section') as HTMLElement
    await within(panel).findByText('No quote for this instrument')
    const [, first, second] = within(panel).getAllByRole('row')
    expect(first).toHaveTextContent('Recorder not running')
    expect(second).toHaveTextContent('12 Oct, 11:00:00')
    expect(second).toHaveTextContent('11:12:00')
    expect(second).toHaveTextContent('12 min')
    expect(panel).toHaveTextContent('2 in trading hours, 19 min in all')
  })

  it('no longer says the recording was uninterrupted under an asset that has holes', async () => {
    serve({ context: { gaps: [] }, availability: { 1: recorded(), 2: HOLED, 5: recorded() } })
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()

    const panel = (await screen.findByRole('heading', { name: 'Recording gaps' })).closest('section') as HTMLElement
    await within(panel).findByText('No quote for this instrument')
    expect(screen.queryByText('Recorded without interruption during trading hours.')).not.toBeInTheDocument()
  })
})

describe('the signals on the chart', () => {
  const toggle = () => screen.getByRole('switch', { name: 'Signal marks' })
  const legend = () => within(screen.getByRole('list', { name: 'Legend' }))

  it('can be left out, the trades staying where they are', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.markers).toHaveLength(4))
    expect(toggle()).toBeChecked()
    expect(legend().getByText('Signal')).toBeInTheDocument()

    await user.click(toggle())

    expect(toggle()).not.toBeChecked()
    await waitFor(() =>
      expect(seriesOf(theChart(), 'Candlestick')?.markers.map((marker) => marker.text)).toEqual(['Long', 'Long +1.78%']),
    )
    expect(legend().queryByText('Signal')).not.toBeInTheDocument()
    expect(legend().getByText('Bought')).toBeInTheDocument()

    await user.click(toggle())

    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.markers).toHaveLength(4))
  })

  it('are left off the index line under a turbo too', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/2')
    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.markers).toHaveLength(2))

    await user.click(toggle())

    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.markers).toEqual([]))
    expect(seriesOf(theChart(), 'Candlestick')?.markers.map((marker) => marker.text)).toEqual(['Buy €8.45', '+1.78%'])
  })

  it('stay hidden for the next visit, and out of the address', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    const user = userEvent.setup()
    renderPage()
    await chartIsDrawn()
    await user.click(toggle())
    expect(address()).toBe('/data/assets/saxo/1')

    cleanup()
    resetCharts()
    renderPage()
    await chartIsDrawn()

    expect(toggle()).not.toBeChecked()
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.markers).toHaveLength(2))
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

    expect(rangeRequests()).toEqual([`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=bid`])
    expect(address()).toBe('/data/assets/saxo/1?range=auto')
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
    renderPage('/data/assets/saxo/1?range=auto')
    await rangeIsDrawn()

    await user.clear(ticksField())
    await user.type(ticksField(), '1500{Enter}')

    await waitFor(() =>
      expect(rangeRequests()).toContain(`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=bid&size=15`),
    )
    expect(address()).toBe('/data/assets/saxo/1?range=15')
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
    renderPage('/data/assets/saxo/1?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Larger range' }))

    await waitFor(() => expect(address()).toBe('/data/assets/saxo/1?range=20'))
    await waitFor(() => expect(rangeRequests().at(-1)).toContain('size=20'))
  })

  it('are counted in points for an index and in the currency of a turbo', async () => {
    serve()
    renderPage('/data/assets/saxo/1?range=auto')
    await rangeIsDrawn()
    expect(screen.getByLabelText('Range as a price move, in pts')).toHaveValue('20.00')

    cleanup()
    resetCharts()
    renderPage('/data/assets/saxo/2?range=auto')

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
    renderPage('/data/assets/saxo/1?range=auto')
    await rangeIsDrawn()
    expect(rangeRequests()).toEqual([`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=bid`])

    // The thirty-second refresh.
    await queryClient.refetchQueries({ queryKey: ['asset-range'] })

    expect(rangeRequests().at(-1)).toBe(`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=bid&size=20`)
    expect(address()).toBe('/data/assets/saxo/1?range=auto')
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true')

    // Asking for Auto again is asking for the choice to be made again.
    await user.click(screen.getByRole('button', { name: 'Auto' }))

    await waitFor(() => expect(rangeRequests()).toHaveLength(3))
    expect(rangeRequests().at(-1)).toBe(`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=bid`)
  })

  it('of an ask that was never recorded say that it is the ask that is missing', async () => {
    serve({ noAsk: true })
    renderPage('/data/assets/saxo/2?range=0.2&side=ask')

    expect(
      await screen.findByText('No ask was recorded for Long 24,150 · SG in this period: the bid is what there is.'),
    ).toBeInTheDocument()
  })

  it('are read from an address that names a range', async () => {
    serve()
    renderPage('/data/assets/saxo/2?range=0.2&side=ask')
    await rangeIsDrawn()

    expect(rangeRequests()).toEqual([
      `/api/data/assets/saxo/2/range-bars?from=${TODAY}&to=${TODAY}&side=ask&size=0.2&under=1`,
    ])
    expect(barRequests()).toEqual([])
    expect(ticksField().value).toBe('20')
  })

  it('of a turbo come with its index, on the same times, and carry the trades and the signals', async () => {
    serve({ context: { trades: [TRADE], signals: SIGNALS } })
    renderPage('/data/assets/saxo/2?range=0.2')
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
    renderPage('/data/assets/saxo/1?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    await waitFor(() =>
      expect(rangeRequests()).toContain(`/api/data/assets/saxo/1/range-bars?from=${TODAY}&to=${TODAY}&side=ask&size=15`),
    )
  })

  it('keep one range for the index and another for the turbos', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/1?range=auto')
    await rangeIsDrawn()
    // Fifteen points suit the index.
    await user.clear(sizeField())
    await user.type(sizeField(), '15{Enter}')
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/1?range=15'))

    // They mean nothing on a turbo, which starts on a range of its own.
    cleanup()
    resetCharts()
    renderPage('/data/assets/saxo/2')
    await chartIsDrawn()
    await user.click(screen.getByRole('button', { name: 'Range' }))
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/2?range=auto'))
    await waitFor(() => expect(ticksField().value).toBe('20'))
    await user.clear(sizeField())
    await user.type(sizeField(), '0.1{Enter}')
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/2?range=0.1'))

    // Another turbo takes it up; the index has its own back.
    cleanup()
    resetCharts()
    renderPage('/data/assets/saxo/5')
    await chartIsDrawn()
    await user.click(screen.getByRole('button', { name: 'Range' }))
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/5?range=0.1'))

    cleanup()
    resetCharts()
    renderPage('/data/assets/saxo/1')
    await chartIsDrawn()
    await user.click(screen.getByRole('button', { name: 'Range' }))
    await waitFor(() => expect(address()).toBe('/data/assets/saxo/1?range=15'))
  })

  it('give way to bars of a fixed length again', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets/saxo/1?range=15')
    await rangeIsDrawn()

    await user.click(screen.getByRole('button', { name: '1 min' }))

    await waitFor(() => expect(barRequests()).toContain(`/api/data/assets/saxo/1/bars?from=${TODAY}&to=${TODAY}&interval=60`))
    expect(address()).toBe('/data/assets/saxo/1?interval=60')
    expect(screen.queryByLabelText('Range in ticks (R)')).not.toBeInTheDocument()
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(3))
  })

  it('are written out: prices, how long the bar took, whether it is over, the index', async () => {
    serve()
    renderPage('/data/assets/saxo/2?range=0.2')
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
    renderPage('/data/assets/saxo/2?range=0.2')
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
    renderPage('/data/assets/saxo/1?range=15')
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
    renderPage('/data/assets/saxo/2?range=0.2')
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

    await waitFor(() => expect(address()).toBe('/data/assets/saxo/2?range=0.3'))
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
            <MemoryRouter initialEntries={['/data/assets/saxo/2']}>
              <ThemeToggle />
              <Routes>
                <Route path="data/assets/:provider/:key" element={<AssetPage />} />
              </Routes>
            </MemoryRouter>
          </AuthContext.Provider>
        </ThemeProvider>
      </QueryClientProvider>,
    )
    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))
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
    renderPage('/data/assets/saxo/2')
    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))

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
    renderPage('/data/assets/saxo/2')
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

  it('shows what the server answers when the bars cannot be given', async () => {
    serve({ barsError: { status: 422, detail: 'More than 12000 bars in this period at 10 seconds: choose a longer interval or a shorter period' } })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('More than 12000 bars in this period at 10 seconds')
    expect(() => theChart()).toThrow()
  })
})

describe('a Dukascopy history', () => {
  it('is drawn from its own files, with the days fetched in place of the recorder’s gaps', async () => {
    serve()
    renderPage('/data/assets/dukascopy/us100')
    await chartIsDrawn()

    expect(barRequests()).toEqual(['/api/data/assets/dukascopy/us100/bars?from=2026-10-12&to=2026-10-12&interval=10'])
    expect(title('US Tech 100 (Dukascopy)')).toBeInTheDocument()
    expect(seriesOf(theChart(), 'Line')).toBeUndefined()
    expect(await screen.findByRole('heading', { name: 'Days fetched' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Recording gaps' })).not.toBeInTheDocument()
    expect(requests).toContain('/api/backtests/prices?from=2026-10-12&to=2026-10-12&label=us100')
    // Nothing is recorded live: nothing is read again by itself.
    expect(screen.queryByText(/Read again every 30 seconds/)).not.toBeInTheDocument()
  })

  it('is read a month at a time at most', async () => {
    serve()
    renderPage('/data/assets/dukascopy/us100?from=2026-08-01&to=2026-10-09')

    expect(await screen.findByText(/read 31 days at a time at most/)).toBeInTheDocument()
    expect(barRequests()).toEqual([])
  })
})

describe('the statistics of a period', () => {
  it('sum it up, day by day and through the day', async () => {
    serve({
      statistics: [
        day({ day: '2026-10-08', open: 8.0, close: 8.3, change: 0.3, change_percent: 3.75, range: 0.6, range_percent: 7.5 }),
        day({ open: 8.3, close: 8.8, change: 0.5, change_percent: 6.02, range: 1.0, range_percent: 12.05, volume: 1200 }),
      ],
    })
    renderPage('/data/assets/saxo/2?tab=statistics')

    const changed = await screen.findByText('Change over the period')
    expect(changed.parentElement).toHaveTextContent('+0.80')
    expect(screen.getByText('Range of a day, on average').parentElement).toHaveTextContent('0.80')
    expect(screen.getByText('Spread, on average').parentElement).toHaveTextContent('0.05')
    // The days' table; the figures of the spread are a table of their own, under its chart.
    const [table] = screen.getAllByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText(/Dearest from 15:30/)).toBeInTheDocument()
    // The volume, recorded on one of the days, has a column.
    expect(within(table).getByText('1,200')).toBeInTheDocument()
  })

  it('write prices as the other pages do', async () => {
    serve({
      statistics: [
        day({ open: 25010.5, high: 25120.25, low: 24980, close: 25100.75, change: 90.25, change_percent: 0.36, range: 140.25, range_percent: 0.56 }),
      ],
    })
    renderPage('/data/assets/saxo/1?tab=statistics')

    const [table] = await screen.findAllByRole('table')
    expect(within(table).getByText('25,010.50')).toBeInTheDocument()
    expect(within(table).getByText('+90.25 (+0.36%)')).toBeInTheDocument()
  })

  it('keep the widest spread of each day for who asks: the widest of the period has its tile', async () => {
    serve({ statistics: [day({})] })
    renderPage('/data/assets/saxo/2?tab=statistics')

    const [table] = await screen.findAllByRole('table')
    expect(await within(table).findByText('Spread')).toBeInTheDocument()
    expect(within(table).queryByText('Widest spread')).not.toBeInTheDocument()
    expect(screen.getByText('Spread, on average').parentElement).toHaveTextContent('Widest 0.09')
  })

  it('are worked out for a month at most, without asking', async () => {
    serve()
    renderPage('/data/assets/saxo/2?from=2026-08-01&to=2026-10-12&tab=statistics')

    expect(await screen.findByText(/worked out for 31 days at most/)).toBeInTheDocument()
    expect(requests.some((path) => path.includes('/statistics'))).toBe(false)
  })

  it('say that the recorder’s index has no spread', async () => {
    serve()
    renderPage('/data/assets/saxo/1?tab=statistics')

    expect(await screen.findByText('Saxo quotes the index with one price')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Spread through the day' })).not.toBeInTheDocument()
  })
})

describe('the quality of the data', () => {
  it('says what was recorded of the asset and what is missing', async () => {
    const stopped = recorded({
      bars: 1800, quoted_seconds: 18000, missing_seconds: 32400, stretch_count: 1, issues: ['stopped'],
      stops: ['2026-10-12T11:00:00Z'], last_bar: '2026-10-12T10:59:50Z',
      stretches: [{ start: '2026-10-12T11:00:00Z', end: '2026-10-12T20:00:00Z', seconds: 32400, reason: 'stopped' }],
    })
    serve({ availability: { 1: recorded(), 2: stopped, 5: recorded() } })
    renderPage('/data/assets/saxo/2?tab=quality')

    const panel = (await screen.findByRole('heading', { name: 'What was recorded' })).closest('section') as HTMLElement
    const fact = async (label: string) => (await within(panel).findByText(label)).nextSibling
    expect(await fact('Stopped')).toHaveTextContent('No more quote after 13:00:00, while other turbos were still quoted')
    expect(await fact('Availability')).toHaveTextContent('35.7%')
    expect(await screen.findByRole('heading', { name: 'Trading time covered, day by day' })).toBeInTheDocument()
  })

  it('says of a long period that what is missing was not worked out', async () => {
    serve({ availability: null })
    renderPage('/data/assets/saxo/2?from=2026-09-01&to=2026-10-12&tab=quality')

    expect(await screen.findByText('Not worked out for a period of more than 7 days')).toBeInTheDocument()
  })

  it('says of a Dukascopy history which days were fetched', async () => {
    serve()
    renderPage('/data/assets/dukascopy/us100?tab=quality')

    expect(await screen.findByRole('list', { name: /Prices of Oct 2026/ })).toBeInTheDocument()
  })
})

describe('the trades on the asset', () => {
  it('are WATA’s positions, each a link to the Positions page', async () => {
    serve({ context: { trades: [TRADE] } })
    renderPage('/data/assets/saxo/2?tab=trades')

    expect(await screen.findByRole('link', { name: 'Position 5001 on the Positions page' })).toHaveAttribute(
      'href',
      '/positions?find=5001',
    )
    expect(screen.getByText('Won').parentElement).toHaveTextContent('1 of 1')
  })

  it('of an index are those on the turbos the recorder followed', async () => {
    serve({ context: { trades: [] } })
    renderPage('/data/assets/saxo/1?tab=trades')

    expect(await screen.findByText('On the turbos of us100 the recorder followed')).toBeInTheDocument()
    expect(screen.getByText('No position in this period')).toBeInTheDocument()
  })
})

describe('the details of the asset', () => {
  it('give everything stored, with a link to the index of a turbo', async () => {
    serve()
    renderPage('/data/assets/saxo/2?tab=details')

    expect(await screen.findByRole('link', { name: 'NASDAQ 100' })).toHaveAttribute('href', '/data/assets/saxo/1')
    expect(screen.getByText('Knock-out level').nextSibling).toHaveTextContent('24,150')
    expect(screen.getByText('Saxo UIC').nextSibling).toHaveTextContent('40000002')
    expect(screen.getByText('Kept').nextSibling).toHaveTextContent('For good')
  })

  it('of a Dukascopy history say where its files are', async () => {
    serve()
    renderPage('/data/assets/dukascopy/us100?tab=details')

    expect(await screen.findByText('Files')).toBeInTheDocument()
    expect(screen.getByText('Files').nextSibling).toHaveTextContent('dukascopy/bars/us100, 1.5 MB')
    expect(screen.queryByText('Saxo UIC')).not.toBeInTheDocument()
  })
})
