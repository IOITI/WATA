import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { ToastContext } from '@/components/toast-context'
import { AuthContext } from '@/features/auth/auth-context'
import type {
  DataProvider,
  DukascopyProviderDetails,
  RecorderConfig,
  RecorderSettings,
  SaxoProviderDetails,
  Session,
} from '@/lib/types'

import { ProviderPage } from './ProviderPage'
import { ProvidersPage } from './ProvidersPage'

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

const NOW = new Date('2026-10-12T10:30:00Z')
const utc = (iso: string) => Date.parse(iso) / 1000

const DEFAULTS: RecorderSettings = {
  enabled: false, record_volume: false, max_instruments: 150, refresh_interval_seconds: 300, refresh_rate_ms: 1000,
  retention_months: 0, alert_after_seconds: 300, instruments: [],
}
const CATALOG = [
  { id: 'saxo', name: 'Saxo Bank', kind: 'live' as const, available: true, description: 'Recorded from Saxo.' },
  { id: 'dukascopy', name: 'Dukascopy', kind: 'download' as const, available: true, description: 'Downloaded history.' },
  { id: 'capital', name: 'Capital.com', kind: 'live' as const, available: false, description: 'Not integrated yet.' },
]

function saxo(changes: Partial<SaxoProviderDetails> = {}): SaxoProviderDetails {
  return {
    ...CATALOG[0], id: 'saxo', status: 'ok', detail: 'Recording 43 instruments', enabled: true, active: true, assets: 7,
    first_t: utc('2026-09-14T06:00:00Z'), last_t: utc('2026-10-12T10:29:50Z'), first_day: '2026-09-14', last_day: '2026-10-12',
    configurable: true,
    heartbeat: { ts: '2026-10-12T10:29:50Z', feed_ok: true, instruments: 43, bars: 43 },
    settings: { ...DEFAULTS, enabled: true }, gaps_today: [], restart_required: false, ...changes,
  }
}

function dukascopy(changes: Partial<DukascopyProviderDetails> = {}): DukascopyProviderDetails {
  return {
    ...CATALOG[1], id: 'dukascopy', status: 'ok', detail: 'Fetched on demand', enabled: true, active: true, assets: 1,
    first_t: utc('2026-10-05T00:00:00Z'), last_t: utc('2026-10-09T23:59:50Z'), first_day: '2026-10-05', last_day: '2026-10-09',
    configurable: true,
    fetch: { state: 'idle', lines: [] },
    keep: { enabled: false, days: 120, state: 'off', missing: 0, settled: 0, failures: 0, next_at: null, limits: { days: 730 } },
    instruments: [
      { symbol: 'USATECHIDXUSD', label: 'us100', name: 'US Tech 100', currency: 'USD', built_in: true, days: 5, first_day: '2026-10-05', last_day: '2026-10-09', bytes: 1500000 },
      { symbol: 'DEUIDXEUR', label: 'de40', name: 'Germany 40', currency: 'EUR', built_in: false, days: 0, first_day: null, last_day: null, bytes: 0 },
    ],
    offered: [{ symbol: 'USA500IDXUSD', label: 'us500', name: 'US 500', currency: 'USD' }],
    storage: { runs: { count: 0, bytes: 0 }, prices: { days: 5, bars_bytes: 1500000, fetched_bytes: 3000000 } },
    source: 'https://datafeed.dukascopy.com/datafeed',
    ...changes,
  }
}

interface Server {
  providers?: DataProvider[]
  saxoDetails?: SaxoProviderDetails
  config?: Partial<RecorderConfig>
  /** How the save of the recorder's section is answered. */
  saved?: { status: number; detail?: string }
}

let requests: { method: string; path: string; body: unknown }[] = []

function serve({ providers, saxoDetails = saxo(), config = {}, saved = { status: 200 } }: Server = {}) {
  requests = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      requests.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined })
      const url = new URL(path, 'http://admin.test')
      if (url.pathname === '/api/data/providers') {
        return json({
          items: providers ?? [saxo(), dukascopy({ active: false, assets: 0, first_t: null, last_t: null, first_day: null, last_day: null })],
          catalog: CATALOG,
          timezone: 'Europe/Paris',
        })
      }
      if (url.pathname === '/api/data/providers/saxo') return json(saxoDetails)
      if (url.pathname === '/api/data/providers/dukascopy') return json(dukascopy())
      if (url.pathname === '/api/data/providers/saxo/config') {
        if (method === 'PUT') {
          if (saved.status !== 200) return json({ detail: saved.detail }, saved.status)
          return json({ revision: 'r2', backup: 'config.json.bak-20261012-123000', restart_required: true })
        }
        return json({
          section: { enabled: false }, defaults: DEFAULTS, asset_types: ['StockIndex', 'CfdOnIndex'], indices: ['us100'],
          revision: 'r1', editable: true, ...config,
        })
      }
      if (url.pathname === '/api/data/providers/dukascopy/instruments' && method === 'POST') {
        return json({ ...(init.body ? JSON.parse(String(init.body)) : {}), name: 'US 500', currency: 'USD', built_in: false }, 201)
      }
      if (url.pathname.startsWith('/api/data/providers/dukascopy/instruments/') && method === 'DELETE') return json(null, 204)
      if (url.pathname === '/api/backtests/prices') {
        return json({
          from: '2026-06-15', to: '2026-10-12', label: url.searchParams.get('label') ?? 'us100', symbol: 'USATECHIDXUSD',
          days: [], ready: { count: 0, first: null, last: null }, fetch: { state: 'idle', lines: [] },
          keep: { enabled: false, days: 120, state: 'off', missing: 0, settled: 0, failures: 0, next_at: null, limits: { days: 730 } },
        })
      }
      throw new Error(`Unexpected request: ${method} ${path}`)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

const toast = vi.fn()

function renderPage(address = '/data/providers', session: Session = SESSION) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={toast}>
        <AuthContext.Provider value={{ session, error: null, login: async () => {}, logout: async () => {} }}>
          <MemoryRouter initialEntries={[address]}>
            <Routes>
              <Route path="data/providers" element={<ProvidersPage />} />
              <Route path="data/providers/:providerId" element={<ProviderPage />} />
              <Route path="*" element={<Address />} />
            </Routes>
          </MemoryRouter>
        </AuthContext.Provider>
      </ToastContext.Provider>
    </QueryClientProvider>,
  )
}

const writes = () => requests.filter((request) => request.method !== 'GET')

beforeEach(() => {
  toast.mockClear()
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the providers', () => {
  it('are those switched on or holding data, with what each is doing', async () => {
    serve()
    renderPage()

    const table = await screen.findByRole('table')
    expect(within(table).getByRole('link', { name: 'Saxo Bank' })).toHaveAttribute('href', '/data/providers/saxo')
    expect(within(table).queryByText('Dukascopy')).not.toBeInTheDocument()
    expect(within(table).queryByText('Capital.com')).not.toBeInTheDocument()
    expect(within(table).getByText('Recording 43 instruments')).toBeInTheDocument()
    expect(within(table).getByText('Live stream')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: '7' })).toHaveAttribute('href', '/data/assets?provider=saxo')
    expect(within(table).getByRole('link', { name: 'View assets' })).toHaveAttribute('href', '/data/assets?provider=saxo')
    expect(within(table).getByRole('link', { name: 'View details' })).toHaveAttribute('href', '/data/providers/saxo')
    expect(document.title).toBe('Providers · WATA Admin')
    expect(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByText('Providers')).toBeInTheDocument()
  })

  it('are said to be none when nothing is switched on, with how to add one', async () => {
    serve({ providers: [saxo({ active: false, enabled: false }), dukascopy({ active: false })] })
    renderPage()

    expect(await screen.findByRole('heading', { name: 'No provider is switched on yet' })).toBeInTheDocument()
  })

  it('that can be added are offered in a catalog, Capital.com to come', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: 'Add provider' }))

    const dialog = await screen.findByRole('dialog', { name: 'Add a provider' })
    const entry = (name: string) => within(dialog).getByText(name, { exact: false, selector: 'p' }).closest('li') as HTMLElement
    expect(within(entry('Saxo Bank')).getByText('Active')).toBeInTheDocument()
    expect(within(entry('Dukascopy')).getByRole('button', { name: 'Set up' })).toBeInTheDocument()
    expect(within(entry('Capital.com')).getByText('Coming later')).toBeInTheDocument()
    expect(within(entry('Capital.com')).queryByRole('button', { name: 'Set up' })).not.toBeInTheDocument()
  })

  it('lead to the Dukascopy page to set it up', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Add provider' }))

    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Set up' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Dukascopy' })).toBeInTheDocument()
  })
})

describe('the price recorder’s settings', () => {
  async function openSettings() {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Edit configuration' }))
    const dialog = await screen.findByRole('dialog', { name: 'Price recorder settings' })
    await within(dialog).findByRole('switch', { name: "Record Saxo's prices" })
    return { user, dialog }
  }

  it('are saved by a slide after a review of what changes, through the configuration’s own save', async () => {
    serve()
    const { user, dialog } = await openSettings()

    await user.click(within(dialog).getByRole('switch', { name: "Record Saxo's prices" }))
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))

    expect(within(dialog).getByText('trade.config.price_recorder.enabled')).toBeInTheDocument()
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toEqual({
      method: 'PUT',
      path: '/api/data/providers/saxo/config',
      body: { section: { enabled: true }, revision: 'r1' },
    })
    await waitFor(() => expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: 'Price recorder settings saved' })))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('say what a retention deletes before it is saved', async () => {
    serve({ config: { section: { enabled: true } } })
    const { user, dialog } = await openSettings()

    const kept = within(dialog).getByLabelText(/^Keep/)
    await user.clear(kept)
    await user.type(kept, '6')
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))

    expect(within(dialog).getByText(/Only 6 months kept: .* for good\./)).toBeInTheDocument()
  })

  it('say why a save is refused, and that the file changed on the server', async () => {
    serve({ saved: { status: 409, detail: 'The configuration file changed on the server since it was loaded' } })
    const { user, dialog } = await openSettings()

    await user.click(within(dialog).getByRole('switch', { name: "Record Saxo's volume" }))
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('changed on the server since this dialog read it')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('show the services’ own validator refusing a value', async () => {
    serve({ saved: { status: 422, detail: "Config field 'trade.config.price_recorder.refresh_rate_ms' must be a whole number of at least 100" } })
    const { user, dialog } = await openSettings()

    const rate = within(dialog).getByLabelText(/^Quotes at most every/)
    await user.clear(rate)
    await user.type(rate, '50')
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('refresh_rate_ms')
  })

  it('refuse two instruments under one label before anything is sent', async () => {
    serve({
      config: {
        section: { enabled: true, instruments: [{ label: 'dax', uic: 1, asset_type: 'CfdOnIndex' }, { label: 'dax', uic: 2, asset_type: 'CfdOnIndex' }] },
      },
    })
    const { user, dialog } = await openSettings()

    await user.click(within(dialog).getByRole('switch', { name: "Record Saxo's volume" }))

    expect(within(dialog).getByRole('alert')).toHaveTextContent('Two instruments are labelled "dax"')
    expect(within(dialog).getByRole('button', { name: 'Review the changes' })).toBeDisabled()
  })

  it('are shown read-only where editing is switched off', async () => {
    serve({ config: { editable: false } })
    const { dialog } = await openSettings()

    expect(within(dialog).getByRole('switch', { name: "Record Saxo's prices" })).toBeDisabled()
    expect(within(dialog).getByText(/admin\.config_edit_enabled/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Review the changes' })).toBeDisabled()
  })
})

describe('the Saxo provider’s page', () => {
  it('says how it is set, what it records besides the turbos, and what to do on the server', async () => {
    serve({
      saxoDetails: saxo({
        settings: { ...DEFAULTS, enabled: true, retention_months: 6, instruments: [{ label: 'us100', uic: 4913, asset_type: 'CfdOnIndex' }] },
      }),
    })
    renderPage('/data/providers/saxo')

    expect(await screen.findByRole('heading', { level: 1, name: 'Saxo Bank' })).toBeInTheDocument()
    expect(screen.getByText('Kept').nextSibling).toHaveTextContent('6 months')
    expect(screen.getByText('UIC 4913 · CfdOnIndex')).toBeInTheDocument()
    expect(screen.getByText(/wataprices probe --uic/)).toBeInTheDocument()
    const trail = within(screen.getByRole('navigation', { name: 'Breadcrumb' }))
    expect(trail.getByRole('link', { name: 'Providers' })).toHaveAttribute('href', '/data/providers')
  })

  it('says that a recorder switched on and silent must be started on the server', async () => {
    serve({ saxoDetails: saxo({ status: 'down', detail: 'No sign of it for 12 min' }) })
    renderPage('/data/providers/saxo')

    expect(await screen.findByRole('alert')).toHaveTextContent('docker restart price_recorder1')
  })

  it('adds an instrument in the settings dialog, which waits for its UIC', async () => {
    serve({ config: { section: { enabled: true } } })
    const user = userEvent.setup()
    renderPage('/data/providers/saxo')
    await screen.findByRole('heading', { level: 1, name: 'Saxo Bank' })

    await user.click(screen.getByRole('button', { name: 'Add instrument' }))

    const dialog = await screen.findByRole('dialog', { name: 'Record another instrument' })
    const review = await within(dialog).findByRole('button', { name: 'Review the changes' })
    expect(review).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Label, row 1'), 'dax')
    await user.type(within(dialog).getByLabelText('UIC, row 1'), '4913')

    await waitFor(() => expect(review).toBeEnabled())
    await user.click(review)
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0].body).toEqual({
      section: { enabled: true, instruments: [{ label: 'dax', uic: 4913, asset_type: 'StockIndex' }] },
      revision: 'r1',
    })
  })
})

describe('the Dukascopy provider’s page', () => {
  it('lists its instruments, each with the days fetched, and reads the coverage of each', async () => {
    serve()
    renderPage('/data/providers/dukascopy')

    expect(await screen.findByRole('heading', { level: 1, name: 'Dukascopy' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View asset' })).toHaveAttribute('href', '/data/assets/dukascopy/us100')
    expect(screen.getByText('Nothing fetched yet')).toBeInTheDocument()
    await waitFor(() => expect(requests.map((request) => request.path)).toContain('/api/backtests/prices?label=de40'))
    // The history backtests read is asked for as the backtest pages ask for it.
    expect(requests.map((request) => request.path)).toContain('/api/backtests/prices')
  })

  it('adds an instrument and stops fetching another from its settings', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/providers/dukascopy')
    await screen.findByRole('heading', { level: 1, name: 'Dukascopy' })

    await user.click(screen.getByRole('button', { name: 'Edit configuration' }))
    const dialog = await screen.findByRole('dialog', { name: 'Dukascopy settings' })
    await user.click(await within(dialog).findByRole('button', { name: 'Add' }))

    await waitFor(() =>
      expect(writes()).toContainEqual({
        method: 'POST', path: '/api/data/providers/dukascopy/instruments', body: { symbol: 'USA500IDXUSD', label: 'us500' },
      }),
    )

    await user.click(within(dialog).getByRole('button', { name: 'Stop fetching Germany 40' }))

    await waitFor(() =>
      expect(writes()).toContainEqual({ method: 'DELETE', path: '/api/data/providers/dukascopy/instruments/de40', body: undefined }),
    )
  })
})

describe('a provider to come', () => {
  it('says that it is not integrated yet', async () => {
    serve()
    renderPage('/data/providers/capital')

    expect(await screen.findByRole('heading', { name: 'Capital.com is not integrated yet' })).toBeInTheDocument()
  })
})
