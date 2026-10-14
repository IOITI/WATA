import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { DataAsset, DataAssets, InstrumentAvailability, Session } from '@/lib/types'
import { resetCharts, seriesOf, theChart } from '@/test/fakeCharts'
import { WithTrail } from '@/test/WithTrail'

import { AssetsPage } from './AssetsPage'

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
    symbol: `SG-NDX-${id}`, description: null, currency: 'EUR', role: 'turbo', indice: 'us100', underlying_uic: 1909050,
    direction: 'long', barrier: null, issuer: 'SG', first_t: utc('2026-10-12T06:00:00Z'), last_t: utc('2026-10-12T10:29:50Z'),
    last_bid: 8.45, last_ask: 8.5, status: 'recording', ...changes,
  }
}

const INDEX = saxo(1, {
  uic: 1909050, asset_type: 'StockIndex', symbol: 'NDX.I', description: 'NASDAQ 100', currency: 'USD', role: 'underlying',
  direction: null, issuer: null, last_bid: 25000.5, last_ask: 25000.5,
})
const LONG = saxo(2, { barrier: 24150 })
const SHORT = saxo(5, { direction: 'short', barrier: 25900, issuer: 'BNP' })
// Knocked out last week: no longer named by the recorder.
const ENDED = saxo(9, { barrier: 23800, status: 'ended', last_t: utc('2026-10-08T14:00:00Z'), first_t: utc('2026-10-08T06:00:00Z') })
const HISTORY: DataAsset = {
  provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', id: null, uic: null, asset_type: 'Dukascopy',
  symbol: 'USATECHIDXUSD', description: 'US Tech 100 (Dukascopy)', currency: 'USD', role: 'underlying', indice: 'us100',
  underlying_uic: null, direction: null, barrier: null, issuer: null, first_t: utc('2026-10-05T00:00:00Z'),
  // Its last file is a day of UTC: its last two hours are the next day in Paris.
  last_t: utc('2026-10-09T23:59:50Z'), last_bid: 25100, last_ask: 25101.5, status: 'downloaded', days: 5,
  first_day: '2026-10-05', last_day: '2026-10-09',
}
const ALL = [INDEX, LONG, SHORT, ENDED, HISTORY]

function recorded(missing: Partial<InstrumentAvailability> = {}): InstrumentAvailability {
  return {
    bars: 5040, updates: 12840, first_bar: '2026-10-12T06:00:00Z', last_bar: '2026-10-12T19:59:50Z',
    quoted_seconds: 50400, missing_seconds: 0, holes: 0, hole_seconds: 0, stops: [], stretches: [], stretch_count: 0,
    knocked_out_at: null, without_ask: 0, delayed_minutes: 0, issues: [], ...missing,
  }
}
const HOLED = recorded({
  bars: 4968, quoted_seconds: 49680, missing_seconds: 720, holes: 1, hole_seconds: 720, stretch_count: 1, issues: ['holes'],
  stretches: [{ start: '2026-10-12T09:00:00Z', end: '2026-10-12T09:12:00Z', seconds: 720, reason: 'unquoted' }],
})

let requests: string[] = []

function serve({
  items = ALL,
  answer = {},
  availability = { 1: recorded(), 2: HOLED, 5: recorded() },
  volume = null,
}: {
  items?: DataAsset[]
  answer?: Partial<DataAssets>
  availability?: Record<number, InstrumentAvailability> | null
  volume?: number | null
} = {}) {
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
      if (url.pathname === '/api/data/assets') {
        return json({ timezone: 'Europe/Paris', seen: url.searchParams.get('seen'), items, truncated: false, max: 5000, saxo_error: null, ...answer })
      }
      if (url.pathname === '/api/data/providers/saxo/availability') {
        return json({ from, to, timezone: 'Europe/Paris', max_days: 7, items: availability })
      }
      const match = url.pathname.match(/^\/api\/data\/assets\/(saxo|dukascopy)\/([^/]+)\/(bars|context)$/)
      const asset = match ? items.find((item) => item.ref === `${match[1]}/${match[2]}`) : undefined
      if (match?.[3] === 'bars' && asset) {
        const first = asset.role === 'underlying' ? 25000 : 8.4
        const t0 = utc(`${from}T08:00:00Z`)
        return json({
          instrument: asset, from, to, timezone: 'Europe/Paris', interval: Number(url.searchParams.get('interval')), columns: [],
          bars: [0, 1, 2].map((n) => [t0 + 10 * n, first, first + 0.04, first - 0.02, first + 0.02, first + 0.05, first + 0.09, first + 0.03, first + 0.07, 3]),
        })
      }
      if (match?.[3] === 'context' && asset) {
        return json({ instrument: asset, trades: [], signals: [], gaps: [], volume })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

const trail = () => within(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0])

function renderPage(address = '/data/assets') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={[address]}>
          {/* A provider's link, from elsewhere in the interface. */}
          <Link to="/data/assets?provider=dukascopy">Dukascopy's assets</Link>
          <WithTrail>
            <Routes>
              <Route
                path="data/assets"
                element={
                  <>
                    <AssetsPage />
                    <Address />
                  </>
                }
              />
              <Route path="*" element={<Address />} />
            </Routes>
          </WithTrail>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

const table = () => screen.getAllByRole('table')[0]
const names = () =>
  within(table())
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).queryAllByRole('link')[0]?.textContent)
    .filter(Boolean)
const listed = () => waitFor(() => expect(names().length).toBeGreaterThan(0))

beforeEach(() => {
  resetCharts()
  window.localStorage.clear()
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the assets stored', () => {
  it('are every one of every provider, each a link to its own page', async () => {
    serve()
    renderPage()
    await listed()

    expect(requests).toContain('/api/data/assets?seen=all')
    expect(names()).toEqual(['NASDAQ 100', 'Long 24,150 · SG', 'Short 25,900 · BNP', 'Long 23,800 · SG', 'US Tech 100 (Dukascopy)'])
    expect(within(table()).getByRole('link', { name: 'Long 24,150 · SG' })).toHaveAttribute('href', '/data/assets/saxo/2')
    expect(within(table()).getByRole('link', { name: 'US Tech 100 (Dukascopy)' })).toHaveAttribute('href', '/data/assets/dukascopy/us100')
    expect(document.title).toBe('Assets View · WATA Admin')
  })

  it('say where the page is', async () => {
    serve()
    renderPage()
    await listed()

    expect(trail().getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(trail().getByText('Assets View').closest('[aria-current="page"]')).not.toBeNull()
  })

  it('mark the turbos Saxo left without a quote today, and count them', async () => {
    serve()
    renderPage()
    await listed()

    const long = within(table()).getByRole('link', { name: 'Long 24,150 · SG' }).closest('tr') as HTMLElement
    await waitFor(() => expect(long).toHaveTextContent('Missing data: no quote for 12 min from 11:00:00'))
    expect(screen.getByText('5 assets · 1 with missing data today')).toBeInTheDocument()
    // What is missing is read for today alone: the ended turbo and the history are not in it.
    expect(requests).toContain(`/api/data/providers/saxo/availability?from=${TODAY}&to=${TODAY}`)
  })

  it('are found by a few letters or digits', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.type(screen.getByPlaceholderText('Name, level, issuer, symbol…'), '25900')

    expect(names()).toEqual(['Short 25,900 · BNP'])
  })

  it('are filtered from the address, as a provider’s link asks', async () => {
    serve()
    renderPage('/data/assets?provider=dukascopy')
    await listed()

    expect(names()).toEqual(['US Tech 100 (Dukascopy)'])
  })

  it('are filtered again by a second link while the page is open', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/assets?status=ended')
    await listed()
    expect(names()).toEqual(['Long 23,800 · SG'])

    await user.click(screen.getByRole('link', { name: 'Dukascopy\'s assets' }))

    await waitFor(() => expect(names()).toEqual(['US Tech 100 (Dukascopy)']))
  })

  it('can be those recorded lately, said in the address', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.click(screen.getByRole('button', { name: '7 days' }))

    await waitFor(() => expect(requests).toContain('/api/data/assets?seen=7d'))
    expect(screen.getByLabelText('address')).toHaveTextContent('/data/assets?seen=7d')
    expect(screen.getByRole('button', { name: '7 days' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('are listed from the files while the database is down, and the page says what is missing', async () => {
    serve({ items: [HISTORY], answer: { saxo_error: 'The database is unavailable: the assets recorded from Saxo cannot be listed' } })
    renderPage()
    await listed()

    expect(screen.getByRole('alert')).toHaveTextContent('The database is unavailable')
    expect(names()).toEqual(['US Tech 100 (Dukascopy)'])
  })

  it('are said to be none, with where to switch a provider on', async () => {
    serve({ items: [] })
    renderPage()

    expect(await screen.findByRole('heading', { name: 'No price stored yet' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Providers' })).toHaveAttribute('href', '/data/providers')
  })
})

describe('a row opened', () => {
  it('opens on a click anywhere on it, to what is known of the asset and the chart of its last day', async () => {
    serve({ volume: 15300 })
    const user = userEvent.setup()
    renderPage()
    await listed()

    const row = within(table()).getByRole('link', { name: 'Long 24,150 · SG' }).closest('tr') as HTMLElement
    await user.click(within(row).getByText('SG'))

    const facts = await waitFor(() => {
      const found = document.getElementById('asset-facts-saxo/2')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    const fact = (label: string) => within(facts).getByText(label).nextSibling
    expect(fact('Last price')).toHaveTextContent('Bid 8.45 · Ask 8.50 · spread 0.05 at 12:29:50')
    await waitFor(() => expect(fact('Volume')).toHaveTextContent('15,300'))
    await waitFor(() => expect(fact('Holes')).toHaveTextContent('No quote for 12 min from 11:00:00'))
    expect(screen.getByRole('link', { name: 'Open its page' })).toHaveAttribute('href', '/data/assets/saxo/2')
    // The chart of its day, with the index under it.
    await waitFor(() => expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(3))
    expect(requests).toContain(`/api/data/assets/saxo/2/bars?from=${TODAY}&to=${TODAY}&interval=10`)
    expect(requests).toContain(`/api/data/assets/saxo/1/bars?from=${TODAY}&to=${TODAY}&interval=10`)
    expect(screen.getByRole('button', { name: 'Close Long 24,150 · SG' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('is the only one open: opening another closes it', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.click(screen.getByRole('button', { name: 'Open Long 24,150 · SG' }))
    await waitFor(() => expect(document.getElementById('asset-facts-saxo/2')).not.toBeNull())
    await user.click(screen.getByRole('button', { name: 'Open Short 25,900 · BNP' }))

    await waitFor(() => expect(document.getElementById('asset-facts-saxo/5')).not.toBeNull())
    expect(document.getElementById('asset-facts-saxo/2')).toBeNull()
    // One chart at a time.
    await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data.length).toBeGreaterThan(0))
  })

  it('of an ended turbo shows its last day, not an empty today', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.click(screen.getByRole('button', { name: 'Open Long 23,800 · SG' }))

    await waitFor(() => expect(requests).toContain('/api/data/assets/saxo/9/bars?from=2026-10-08&to=2026-10-08&interval=10'))
  })

  it('of a Dukascopy history says what was fetched of it', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.click(screen.getByRole('button', { name: 'Open US Tech 100 (Dukascopy)' }))

    const facts = await waitFor(() => {
      const found = document.getElementById('asset-facts-dukascopy/us100')
      expect(found).not.toBeNull()
      return found as HTMLElement
    })
    expect(within(facts).getByText('Fetched').nextSibling).toHaveTextContent('5 days, from 05 Oct 2026 to 09 Oct 2026')
    expect(within(facts).getByText('Instrument').nextSibling).toHaveTextContent('USATECHIDXUSD · USD · Dukascopy')
    await waitFor(() =>
      expect(requests).toContain('/api/data/assets/dukascopy/us100/bars?from=2026-10-09&to=2026-10-09&interval=10'),
    )
  })

  it('is not opened by a click meant for the link in it', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await listed()

    await user.click(within(table()).getByRole('link', { name: 'Long 24,150 · SG' }))

    await waitFor(() => expect(screen.getByLabelText('address')).toHaveTextContent('/data/assets/saxo/2'))
  })
})
