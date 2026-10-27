import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { Session, TaScript } from '@/lib/types'
import { enginesMade, resetVela, workspaces } from '@/test/fakeVela'

import { ChartsPage } from './ChartsPage'

vi.mock('@luxalgo/vela/workspace', async () => (await import('@/test/fakeVela')).fakeVelaWorkspace)
vi.mock('@luxalgo/vela-pinets', async () => (await import('@/test/fakeVela')).fakeVelaPinets)

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

const script = (id: string, name: string, kind: TaScript['kind'], source: string) =>
  ({ id, name, kind, source, imports: [], versions: [], version: 1 }) as unknown as TaScript

const SCRIPTS = [
  script('aaaaaaaaaaaa', 'EMA cross', 'indicator', '//@version=6\nindicator("EMA cross")\nplot(ta.ema(close, 9))\n'),
  script('bbbbbbbbbbbb', 'Orphan', 'indicator', '//@version=6\nindicator("O")\nimport me/Missing/1 as m\nplot(close)\n'),
]

let saved: { key: string; value: string }[] = []
let layout: string | null = null

function serve() {
  saved = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      if (url.pathname === '/api/ta/workspaces/charts' && method === 'GET') return json({ value: layout })
      if (url.pathname === '/api/ta/workspaces/charts' && method === 'PUT') {
        saved.push({ key: 'charts', value: (JSON.parse(String(init.body)) as { value: string }).value })
        return json(null, 204)
      }
      if (url.pathname === '/api/data/assets') {
        return json({
          timezone: 'Europe/Paris', seen: 'all', truncated: false, max: 5000, saxo_error: null,
          items: [{ provider: 'capital', key: 'US100', ref: 'capital/US100', role: 'underlying', status: 'recording' }],
        })
      }
      if (url.pathname === '/api/ta/scripts') return json({ items: SCRIPTS })
      const read = SCRIPTS.find((entry) => url.pathname === `/api/ta/scripts/${entry.id}`)
      if (read) return json(read)
      if (url.pathname.startsWith('/api/ta/libraries/')) return json({ detail: 'No library' }, 404)
      return json({ detail: `Not served: ${method} ${url.pathname}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/ta/charts',
        element: (
          <>
            <ChartsPage />
            <Address />
          </>
        ),
      },
    ],
    { initialEntries: [address] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
          <RouterProvider router={router} />
        </AuthContext.Provider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
  return router
}

beforeEach(() => {
  resetVela()
  layout = null
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Charts page', () => {
  it('builds a live Vela on WATA\'s three providers, its Pine engine on the worker file the admin serves, run in full', async () => {
    renderAt('/ta/charts')

    await waitFor(() => expect(workspaces).toHaveLength(1))
    const options = workspaces[0].options
    expect(Object.keys(options.providers as object).sort()).toEqual(['capital', 'dukascopy', 'saxo'])
    // Without `live`, Vela draws the history and never asks the providers for what moves it.
    expect(options).toMatchObject({ layout: false, live: true, symbol: 'capital:CAPITAL_US100', timeframe: '1', persist: 'charts', timezone: 'Europe/Paris' })
    expect(options.timeframes).toEqual(['10S', '20S', '30S', '45S', '1', '5', '15', '60'])
    const engine = (options.engines as { pine: () => { language: string; capabilities: { streaming: boolean } } }).pine()
    expect(enginesMade[0].workerUrl).toMatch(/^\/assets\/vela-pine-worker-[0-9a-f]{12}\.js$/)
    // Never PineTS's live mode (chart/pineEngine.ts).
    expect(engine).toMatchObject({ language: 'pine', capabilities: { streaming: false } })
  })

  it('lists WATA\'s scripts in Vela\'s picker, and says which could not be prepared', async () => {
    renderAt('/ta/charts')

    await waitFor(() => expect(workspaces).toHaveLength(1))
    const manifest = await (workspaces[0].options.indicators as () => Promise<{ name: string; category: string }[]>)()
    expect(manifest.map((entry) => [entry.name, entry.category])).toEqual([['EMA cross', 'WATA indicators']])
    expect(await screen.findByText(/A script is not in the indicator list: Orphan \(No library "Missing"/)).toBeInTheDocument()
  })

  it('opens the saved layout with the address\'s symbol over it, and saves each change', async () => {
    layout = JSON.stringify({ version: 1, layout: '1', charts: [{ id: 'c1', symbol: 'capital:CAPITAL_US100', timeframe: '5' }] })
    renderAt('/ta/charts?symbol=DUKASCOPY_US100.R60')

    await waitFor(() => expect(workspaces).toHaveLength(1))
    const storage = workspaces[0].options.storage as { get: (key: string) => string | null; set: (key: string, value: string) => void }
    expect(JSON.parse(storage.get('charts') as string).charts[0]).toMatchObject({
      symbol: 'dukascopy:DUKASCOPY_US100.R60',
      timeframe: '5',
    })
    storage.set('charts', '{"version":1,"layout":"1","charts":[]}')
    await waitFor(() => expect(saved).toEqual([{ key: 'charts', value: '{"version":1,"layout":"1","charts":[]}' }]))
  })

  it('puts a script asked for on the chart once, then forgets it from the address', async () => {
    renderAt('/ta/charts?script=aaaaaaaaaaaa')

    await waitFor(() => expect(workspaces[0]?.active.added.map((entry) => entry.name)).toEqual(['EMA cross']))
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent(/^\/ta\/charts$/))
  })

  it('takes a length of bars typed above the chart, and refuses one WATA does not make', async () => {
    const user = userEvent.setup()
    renderAt('/ta/charts')
    await waitFor(() => expect(workspaces).toHaveLength(1))
    const field = () => screen.getByRole('textbox', { name: 'Length of the bars, as 45s, 2m or 1h' })
    expect(field()).toHaveValue('1m')

    await user.clear(field())
    await user.type(field(), '90s{Enter}')
    expect(workspaces[0].active.timeframe).toBe('90S')
    // The chart says where it went: the address and the field follow it.
    workspaces[0].active.emit('market:changed', { symbol: 'capital:CAPITAL_US100', timeframe: '90S' })
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/charts?symbol=CAPITAL_US100&tf=90S'))
    expect(field()).toHaveValue('90s')

    await user.clear(field())
    await user.type(field(), '5s{Enter}')
    expect(screen.getByText('WATA records ten-second bars: 10 seconds at least.')).toBeInTheDocument()
    expect(field()).toHaveAttribute('aria-invalid', 'true')
    expect(workspaces[0].active.timeframe).toBe('90S')
  })

  it('opens on bars of seconds the address names, and says what a 45-second bar holds', async () => {
    renderAt('/ta/charts?tf=45S')

    await waitFor(() => expect(workspaces).toHaveLength(1))
    expect(workspaces[0].options.timeframe).toBe('45S')
    expect(screen.getByRole('textbox', { name: 'Length of the bars, as 45s, 2m or 1h' })).toHaveValue('45s')
    expect(screen.getByText(/45 s is no multiple of the ten seconds WATA records/)).toBeInTheDocument()
    // Thirty seconds cut whole ten seconds: nothing to say.
    workspaces[0].active.emit('market:changed', { symbol: 'capital:CAPITAL_US100', timeframe: '30S' })
    await waitFor(() => expect(screen.queryByText(/is no multiple of the ten seconds/)).not.toBeInTheDocument())
  })

  it('keeps the address on the chart\'s symbol and timeframe, Vela\'s own pickers included', async () => {
    renderAt('/ta/charts')
    await waitFor(() => expect(workspaces).toHaveLength(1))

    workspaces[0].active.emit('market:changed', { symbol: 'saxo:SAXO_2', timeframe: '15' })

    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/charts?symbol=SAXO_2&tf=15'))
    // The chart is not told to go where it already is.
    expect(workspaces[0].active.symbols).toEqual([])
  })

  describe('full screen', () => {
    // jsdom has no full screen: the browser's, as it answers.
    const root = document.documentElement
    const setFullscreenElement = (element: Element | null) => {
      Object.defineProperty(document, 'fullscreenElement', { value: element, configurable: true })
      document.dispatchEvent(new Event('fullscreenchange'))
    }
    afterEach(() => {
      Reflect.deleteProperty(document, 'fullscreenElement')
      Reflect.deleteProperty(root, 'requestFullscreen')
      Reflect.deleteProperty(document, 'exitFullscreen')
    })

    it('shows the chart alone over the whole screen, until the browser\'s full screen is left', async () => {
      const requestFullscreen = vi.fn(async () => setFullscreenElement(root))
      const exitFullscreen = vi.fn(async () => setFullscreenElement(null))
      Object.assign(root, { requestFullscreen })
      Object.assign(document, { exitFullscreen })
      const user = userEvent.setup()
      renderAt('/ta/charts')
      await waitFor(() => expect(workspaces).toHaveLength(1))
      const chart = screen.getByTestId('ta-chart').parentElement as HTMLElement

      await user.click(screen.getByRole('button', { name: 'Full screen' }))
      expect(chart).toHaveAttribute('data-full-screen', 'true')
      expect(chart).toHaveClass('fixed', 'inset-0')
      // The page, not the chart: a dialog or a notice opened over it stays in sight.
      expect(requestFullscreen).toHaveBeenCalledTimes(1)

      // Escape is the browser's there: it leaves its full screen, and the chart goes back to the page.
      await act(() => exitFullscreen())
      expect(chart).not.toHaveAttribute('data-full-screen')
      expect(screen.getByRole('button', { name: 'Full screen' })).toBeInTheDocument()

      // The button leaves both.
      await user.click(screen.getByRole('button', { name: 'Full screen' }))
      await user.click(screen.getByRole('button', { name: 'Exit full screen' }))
      expect(chart).not.toHaveAttribute('data-full-screen')
      expect(exitFullscreen).toHaveBeenCalledTimes(2)
    })

    it('fills the window where the browser has no full screen for a page, and leaves it on Escape', async () => {
      const user = userEvent.setup()
      renderAt('/ta/charts')
      await waitFor(() => expect(workspaces).toHaveLength(1))
      const chart = screen.getByTestId('ta-chart').parentElement as HTMLElement

      await user.click(screen.getByRole('button', { name: 'Full screen' }))
      expect(chart).toHaveAttribute('data-full-screen', 'true')
      await user.keyboard('{Escape}')
      expect(chart).not.toHaveAttribute('data-full-screen')
    })
  })
})
