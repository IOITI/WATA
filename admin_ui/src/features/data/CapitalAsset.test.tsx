import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import { chartTime } from '@/features/prices/chartData'
import type { DataAsset, PriceBarRow, Session } from '@/lib/types'
import { resetCharts, seriesOf, theChart } from '@/test/fakeCharts'
import { WithTrail } from '@/test/WithTrail'

import { AssetPage } from './AssetPage'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const SESSION: Session = {
  authenticated: true,
  version: '0.20.0',
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
const T0 = utc('2026-10-12T10:29:00Z')
const ZONE = 'Europe/Paris'

const US100: DataAsset = {
  provider: 'capital', key: 'US100', ref: 'capital/US100', id: 3, uic: null, asset_type: 'Capital.com', symbol: 'US100',
  description: 'US Tech 100', currency: 'USD', role: 'underlying', indice: 'us100', underlying_uic: null, direction: null,
  barrier: null, issuer: null, first_t: utc('2026-10-08T00:00:00Z'), last_t: T0 + 20, last_bid: 25000.5, last_ask: 25001.5,
  status: 'recording', epic: 'US100', instrument_type: 'INDICES', decimals: 1,
}

function row(t: number, bid: number, updates = 2): PriceBarRow {
  return [t, bid, bid + 1, bid - 1, bid + 0.5, bid + 1, bid + 2, bid, bid + 1.5, updates]
}

const READ = [row(T0, 25000), row(T0 + 10, 25001), row(T0 + 20, 25002)]

/** A stand-in for the browser's EventSource: the test plays the server's messages. */
class FakeEventSource {
  static opened: FakeEventSource[] = []
  readonly url: string
  closed = false
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.opened.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
  }

  send(type: string, data: unknown) {
    for (const listener of this.listeners.get(type) ?? []) listener(new MessageEvent(type, { data: JSON.stringify(data) }))
  }
}

let barsRead = 0

function serve() {
  barsRead = 0
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const url = new URL(path, 'http://admin.test')
      if (url.pathname === '/api/data/assets/capital/US100') {
        return json({
          asset: US100, timezone: ZONE, first_day: '2026-10-08', last_day: TODAY, index: null,
          recording: {
            epic: 'US100', label: 'us100', environment: 'demo', feed_state: 'streaming', feed_detail: 'Streaming US100',
            feed_updated_at: '2026-10-12T10:29:55Z', market: 'open', next_opening: null, opening_hours: null,
            first_seen_at: '2026-10-08T00:00:00Z', last_seen_at: '2026-10-12T10:29:00Z', quotes_per_minute: 246,
            delay_ms: { p50: 38, p95: 95, max: 140, count: 246 }, minute_volume: true, retention_months: 0,
            day_files: { label: 'us100', days: 3, first_day: '2026-10-08', last_day: '2026-10-10', bytes: 900000, location: 'capital/bars/us100' },
            live: true,
          },
          max_days: { chart: 366, range: 31, statistics: 31, availability: 0, gaps: 31 },
        })
      }
      if (url.pathname === '/api/data/assets/capital/US100/bars') {
        barsRead += 1
        return json({
          instrument: US100, from: TODAY, to: TODAY, timezone: ZONE, interval: Number(url.searchParams.get('interval')),
          columns: [], bars: READ, through: T0 + 20,
        })
      }
      if (url.pathname === '/api/data/assets/capital/US100/context') {
        return json({ instrument: US100, trades: [], signals: [], gaps: [], volume: 1234 })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
}

function renderPage(address = '/data/assets/capital/US100') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={[address]}>
          <WithTrail>
            <Routes>
              <Route path="data/assets/:provider/:key" element={<AssetPage />} />
            </Routes>
          </WithTrail>
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  resetCharts()
  FakeEventSource.opened = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function opened(): Promise<FakeEventSource> {
  await waitFor(() => expect(FakeEventSource.opened).toHaveLength(1))
  await waitFor(() => expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(3))
  return FakeEventSource.opened[0]
}

describe('a Capital.com asset followed live', () => {
  it('opens its stream and moves the last candle at each quote, without reading the bars again', async () => {
    serve()
    renderPage()
    const stream = await opened()
    expect(stream.url).toBe('/api/data/assets/capital/US100/live')

    stream.send('snapshot', { server_ms: NOW.getTime(), live: true, detail: null, quote: null, state: { type: 'state', ref: 'capital/US100', market: 'open' } })
    stream.send('quote', { type: 'quote', ref: 'capital/US100', t: NOW.getTime() - 42, rx: NOW.getTime(), bid: 25004, ask: 25005, bar: row(T0 + 30, 25003, 1) })

    const candles = seriesOf(theChart(), 'Candlestick')!
    expect(candles.updates.at(-1)).toEqual({ time: chartTime(T0 + 30, ZONE), open: 25003, high: 25004, low: 25002, close: 25003.5 })
    expect(barsRead).toBe(1)
    expect(await screen.findByText('Live · 42 ms')).toBeInTheDocument()
  })

  it('never draws again what the bars read already hold', async () => {
    serve()
    renderPage()
    const stream = await opened()

    // Ten seconds already in the bars read: a late quote of them changes nothing.
    stream.send('quote', { type: 'quote', ref: 'capital/US100', t: null, rx: 0, bid: 1, ask: 2, bar: row(T0 + 20, 1, 9) })

    const candles = seriesOf(theChart(), 'Candlestick')!
    expect(candles.data.at(-1)).toEqual({ time: chartTime(T0 + 20, ZONE), open: 25002, high: 25003, low: 25001, close: 25002.5 })
    expect(candles.updates.every((candle) => candle.open === 25002)).toBe(true)
  })

  it('shows the quote changes as volume, the closed ten seconds as recorded', async () => {
    serve()
    renderPage()
    const stream = await opened()
    const volume = seriesOf(theChart(), 'Histogram')!
    expect(volume.data).toEqual(READ.map((bar) => ({ time: chartTime(bar[0], ZONE), value: bar[9] })))
    expect(volume.scaleOptions).toEqual({ scaleMargins: { top: 0.8, bottom: 0 } })

    stream.send('bar', { type: 'bar', ref: 'capital/US100', bar: row(T0 + 30, 25003, 7) })

    expect(volume.updates.at(-1)).toEqual({ time: chartTime(T0 + 30, ZONE), value: 7 })
  })

  it('reads the bars again when the stream asks it to, and closes it when the page is left', async () => {
    serve()
    const page = renderPage()
    const stream = await opened()

    stream.send('resync', { type: 'resync', ref: 'capital/US100' })
    await waitFor(() => expect(barsRead).toBe(2))

    page.unmount()
    expect(stream.closed).toBe(true)
  })

  it('says when the prices are not live, and why', async () => {
    serve()
    renderPage()
    const stream = await opened()

    stream.send('snapshot', { server_ms: NOW.getTime(), live: false, detail: 'Live prices unavailable (ConnectionError)', quote: null, state: null })

    expect(await screen.findByText('Not live: Live prices unavailable (ConnectionError)')).toBeInTheDocument()
  })
})
