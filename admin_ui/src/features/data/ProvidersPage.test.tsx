import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { ToastContext } from '@/components/toast-context'
import { AuthContext } from '@/features/auth/auth-context'
import type {
  CapitalProviderDetails,
  CapitalSettings,
  DataProvider,
  DukascopyProviderDetails,
  RecorderConfig,
  RecorderSettings,
  SaxoProviderDetails,
  Session,
} from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

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
  { id: 'capital', name: 'Capital.com', kind: 'live' as const, available: true, description: 'The Nasdaq 100 streamed live.' },
]
const CAPITAL_DEFAULTS: CapitalSettings = {
  enabled: false, environment: 'demo', instruments: [{ epic: 'US100', label: 'us100' }], minute_volume: true, day_files: true,
  retention_months: 0, alert_after_seconds: 300,
}

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
    storage: { days: 5, bars_bytes: 1500000, fetched_bytes: 3000000 },
    source: 'https://datafeed.dukascopy.com/datafeed',
    ...changes,
  }
}

function capital(changes: Partial<CapitalProviderDetails> = {}): CapitalProviderDetails {
  return {
    ...CATALOG[2], id: 'capital', status: 'ok', detail: 'Streaming US100 · 4.1 quotes/s · delay 38 ms', enabled: true, active: true,
    assets: 1, first_t: utc('2026-10-08T00:00:00Z'), last_t: utc('2026-10-12T10:29:50Z'), first_day: '2026-10-08',
    last_day: '2026-10-12', configurable: true,
    feed: {
      updated_at: '2026-10-12T10:29:55Z', started_at: '2026-10-12T08:00:00Z', version: '0.20.0', environment: 'demo',
      state: 'streaming', detail: 'Streaming US100',
      figures: {
        instruments: [{ epic: 'US100', label: 'us100', name: 'US Tech 100', market: 'open', status: 'TRADEABLE',
                        last_quote_at: '2026-10-12T10:29:59Z', next_opening: null, subscription: 'PROCESSED' }],
        quotes_per_minute: 246, delay_ms: { p50: 38, p95: 95, max: 140, count: 246 },
        stream: { connected: true, since: '2026-10-12T08:00:05Z', connections: 1, last_error: null },
        live: { sent: 12000, dropped: 0, failing: false, last_error: null },
        minutes: { enabled: true, last: '2026-10-12T10:29:05Z', error: null },
        last_bar_at: '2026-10-12T10:29:50Z', pending_buckets: 0,
      },
    },
    settings: { ...CAPITAL_DEFAULTS, enabled: true }, gaps_today: [],
    day_files: [{ label: 'us100', days: 3, first_day: '2026-10-08', last_day: '2026-10-10', bytes: 900000, location: 'capital/bars/us100' }],
    credential_variables: ['CAPITAL_API_KEY', 'CAPITAL_IDENTIFIER', 'CAPITAL_PASSWORD'], restart_required: false,
    ...changes,
  }
}

interface Server {
  providers?: DataProvider[]
  saxoDetails?: SaxoProviderDetails
  capitalDetails?: CapitalProviderDetails
  config?: Partial<RecorderConfig>
  /** How the save of the recorder's section is answered. */
  saved?: { status: number; detail?: string }
}

let requests: { method: string; path: string; body: unknown }[] = []

function serve({ providers, saxoDetails = saxo(), capitalDetails = capital(), config = {}, saved = { status: 200 } }: Server = {}) {
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
      if (url.pathname === '/api/data/providers/capital') return json(capitalDetails)
      if (url.pathname === '/api/data/providers/capital/config') {
        if (method === 'PUT') return json({ revision: 'r2', backup: 'config.json.bak-20261012-123000', restart_required: true })
        return json({ section: {}, defaults: CAPITAL_DEFAULTS, environments: ['demo', 'live'], indices: ['us100'], revision: 'r1', editable: true })
      }
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
      if (url.pathname === '/api/data/providers/dukascopy/coverage') {
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

const trail = () => within(screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0])

function renderPage(address = '/data/providers', session: Session = SESSION) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ToastContext.Provider value={toast}>
        <AuthContext.Provider value={{ session, error: null, login: async () => {}, logout: async () => {} }}>
          <MemoryRouter initialEntries={[address]}>
            <WithTrail>
              <Routes>
                <Route path="data/providers" element={<ProvidersPage />} />
                <Route path="data/providers/:providerId" element={<ProviderPage />} />
                <Route path="*" element={<Address />} />
              </Routes>
            </WithTrail>
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
    expect(trail().getByText('Providers').closest('[aria-current="page"]')).not.toBeNull()
  })

  it('are said to be none when nothing is switched on, with how to add one', async () => {
    serve({ providers: [saxo({ active: false, enabled: false }), dukascopy({ active: false })] })
    renderPage()

    expect(await screen.findByRole('heading', { name: 'No provider is switched on yet' })).toBeInTheDocument()
  })

  it('that can be added are offered in a catalog', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')

    await user.click(screen.getByRole('button', { name: 'Add provider' }))

    const dialog = await screen.findByRole('dialog', { name: 'Add a provider' })
    // Its name comes first in its entry; another entry's text may name it too.
    const entry = (name: string) => within(dialog).getAllByText(name, { exact: false, selector: 'p' })[0].closest('li') as HTMLElement
    expect(within(entry('Saxo Bank')).getByText('Active')).toBeInTheDocument()
    expect(within(entry('Dukascopy')).getByRole('button', { name: 'Set up' })).toBeInTheDocument()
    expect(within(entry('Capital.com')).getByRole('button', { name: 'Set up' })).toBeInTheDocument()
    expect(within(entry('Capital.com')).getByText(/three/)).toHaveTextContent('CAPITAL_*')
  })

  it('switch Capital.com on from the catalog, through its settings', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Add provider' }))
    const catalog = await screen.findByRole('dialog', { name: 'Add a provider' })
    const entry = within(catalog).getAllByText('Capital.com', { exact: false, selector: 'p' })[0].closest('li') as HTMLElement

    await user.click(within(entry).getByRole('button', { name: 'Set up' }))

    const dialog = await screen.findByRole('dialog', { name: 'Capital.com feed settings' })
    expect(await within(dialog).findByRole('switch', { name: "Record Capital.com's prices" })).toBeChecked()
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))
    within(dialog).getByRole('slider').focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toEqual({
      method: 'PUT', path: '/api/data/providers/capital/config', body: { section: { enabled: true }, revision: 'r1' },
    })
  })

  it('lead to the Dukascopy page to set it up', async () => {
    serve()
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('table')
    await user.click(screen.getByRole('button', { name: 'Add provider' }))

    const catalog = await screen.findByRole('dialog')
    const entry = within(catalog).getAllByText('Dukascopy', { exact: false, selector: 'p' })[0].closest('li') as HTMLElement
    await user.click(within(entry).getByRole('button', { name: 'Set up' }))

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
    expect(trail().getByRole('link', { name: 'Providers' })).toHaveAttribute('href', '/data/providers')
    expect(trail().getByText('Saxo Bank').closest('[aria-current="page"]')).not.toBeNull()
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
    await waitFor(() => expect(requests.map((request) => request.path)).toContain('/api/data/providers/dukascopy/coverage?label=de40'))
    // The history kept up to date is asked for without a label, as every page asks for it.
    expect(requests.map((request) => request.path)).toContain('/api/data/providers/dukascopy/coverage')
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

describe('the Capital.com provider’s page', () => {
  it('says what the stream is doing, each instrument, the day files and how to set it up', async () => {
    serve()
    renderPage('/data/providers/capital')

    expect(await screen.findByRole('heading', { level: 1, name: 'Capital.com' })).toBeInTheDocument()
    const stream = screen.getByText('The stream now').closest('section') ?? document.body
    expect(within(stream).getByText('246 in the last minute')).toBeInTheDocument()
    expect(within(stream).getByText('Median 38 ms, 95 % under 95 ms, worst 140 ms')).toBeInTheDocument()
    expect(within(stream).getByText('12,000 sent through RabbitMQ')).toBeInTheDocument()
    expect(screen.getByText(/Market open/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View asset' })).toHaveAttribute('href', '/data/assets/capital/US100')
    expect(screen.getByText(/3 days, 08 Oct 2026 to 10 Oct 2026/)).toBeInTheDocument()
    // The names of the login's variables: the page never has their values.
    for (const name of ['CAPITAL_API_KEY', 'CAPITAL_IDENTIFIER', 'CAPITAL_PASSWORD']) {
      expect(screen.getByText(`${name}=…`)).toBeInTheDocument()
    }
  })

  it('says what to do on the server when the feed has no login', async () => {
    serve({
      capitalDetails: capital({
        status: 'down', detail: 'No Capital.com login: CAPITAL_API_KEY missing in .env.',
        feed: { ...capital().feed!, state: 'no_credentials', detail: 'No Capital.com login: CAPITAL_API_KEY missing in .env.', figures: {} },
      }),
    })
    renderPage('/data/providers/capital')

    expect(await screen.findByRole('alert')).toHaveTextContent('No Capital.com login: CAPITAL_API_KEY missing in .env.')
    expect(screen.getByRole('alert')).toHaveTextContent('docker restart capital_feed1')
  })

  it('refuses an instrument that is not an epic before anything is sent, and warns of the live account', async () => {
    serve()
    const user = userEvent.setup()
    renderPage('/data/providers/capital')
    await user.click(await screen.findByRole('button', { name: 'Edit configuration' }))
    const dialog = await screen.findByRole('dialog', { name: 'Capital.com feed settings' })
    await within(dialog).findByRole('switch', { name: "Record Capital.com's prices" })

    await user.click(within(dialog).getByRole('button', { name: 'Live' }))
    const epic = within(dialog).getByRole('textbox', { name: /Epic/ })
    await user.clear(epic)
    await user.type(epic, 'us100')

    expect(within(dialog).getByRole('alert')).toHaveTextContent("Instrument 1 needs an epic, Capital.com's name of it in capitals (US100)")
    expect(within(dialog).getByRole('button', { name: 'Review the changes' })).toBeDisabled()

    await user.clear(epic)
    await user.type(epic, 'US100')
    await user.click(within(dialog).getByRole('button', { name: 'Review the changes' }))
    expect(within(dialog).getByText(/logs into the live account/)).toBeInTheDocument()
  })
})

describe('a provider WATA has no code for', () => {
  it('is said to be none', async () => {
    serve()
    renderPage('/data/providers/elsewhere')

    expect(await screen.findByRole('heading', { level: 1, name: 'No such provider' })).toBeInTheDocument()
  })
})
