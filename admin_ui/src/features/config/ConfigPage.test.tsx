import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, Link, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { ConfigDocument, ConfigPreview, JsonObject, Session } from '@/lib/types'
import { CATALOG, configFile, FRESH, START } from '@/test/configCatalog'
import { WithTrail } from '@/test/WithTrail'

import { anchorOf } from './catalog'
import { ConfigPage } from './ConfigPage'
import { ConfigFileView, ConfigGroupPage, ConfigJsonView, ConfigOverview, ConfigVersionsView } from './ConfigViews'

const SESSION: Session = {
  authenticated: true,
  version: '0.41.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

let doc: ConfigDocument
let sent: { method: string; path: string; body: unknown }[] = []

const PREVIEW: ConfigPreview = {
  valid: true,
  changes: [{ path: START, before: 8, after: 9, added: false, removed: false }],
  impact: { services: ['trader', 'scheduler'], consumers: ['dashboard'], risk: 'trading', unknown: [] },
  warnings: [{ path: START, message: 'The close of the day comes after trading ends.' }],
}

function serve() {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/config/catalog') return json(CATALOG)
      if (path === '/api/config' && method === 'GET') return json(doc)
      if (path === '/api/config/history') return json({ items: [] })
      if (path === '/api/config/backups') return json({ items: [] })
      if (path === '/api/config/validate') return json(PREVIEW)
      if (path === '/api/config' && method === 'PUT') {
        doc = { ...doc, config: body.config as JsonObject, revision: 'r2' }
        return json({ revision: 'r2', backup: 'config.json.bak-20261024-120000', restart_required: true,
          changes: PREVIEW.changes, impact: PREVIEW.impact })
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPage(at: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/config',
        element: (
          <WithTrail>
            <ConfigPage />
          </WithTrail>
        ),
        children: [
          { index: true, element: <ConfigOverview /> },
          { path: 'json', element: <ConfigJsonView /> },
          { path: 'file', element: <ConfigFileView /> },
          { path: 'versions', element: <ConfigVersionsView /> },
          { path: ':group', element: <ConfigGroupPage /> },
        ],
      },
      { path: '/elsewhere', element: <p>Elsewhere</p> },
      { path: '/leave', element: <Link to="/elsewhere">Away</Link> },
    ],
    { initialEntries: [at] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
            <RouterProvider router={router} />
          </AuthContext.Provider>
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
  return router
}

beforeEach(() => {
  doc = {
    config: configFile(),
    revision: 'r1',
    editable_paths: ['trade.rules', 'trade.config', 'reporting', 'logging.level'],
    protected_paths: [],
    unknown: [{ path: 'trade.config.position_management.performance_thresholds.trailing_stopp', nearest: FRESH }],
    editable: true,
    restart_required: false,
    modified_at: '2026-10-24T08:00:00Z',
  }
  serve()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the overview of the configuration', () => {
  it('leads to each group with what it holds, and says the keys nothing reads', async () => {
    renderPage('/config')

    const hours = await screen.findByRole('link', { name: /Market hours and calendar/ })
    expect(hours).toHaveAttribute('href', '/config/hours')
    expect(hours).toHaveTextContent('6 parameters')
    const infrastructure = screen.getByRole('link', { name: /Infrastructure/ })
    expect(infrastructure).toHaveTextContent('1 set on the server')
    expect(infrastructure).toHaveTextContent('1 read by nothing')
    expect(screen.getByText('Keys WATA does not know')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: "Judge a new position on Saxo's next quote" })).toHaveAttribute(
      'href',
      `/config/exits#${anchorOf(FRESH)}`,
    )
  })

  it('finds a parameter by what it does, and leads to its row', async () => {
    const user = userEvent.setup()
    renderPage('/config')

    await user.type(await screen.findByRole('textbox', { name: 'Search the parameters' }), 'entries hour')

    const found = screen.getByRole('link', { name: /Trading starts at/ })
    expect(found).toHaveAttribute('href', `/config/hours#${anchorOf(START)}`)
  })
})

describe('a group of parameters', () => {
  it('shows each parameter with who reads it and what it changes', async () => {
    const user = userEvent.setup()
    renderPage('/config/hours')

    const row = await waitFor(() => {
      const element = document.getElementById(anchorOf(START))
      if (!element) throw new Error('not yet')
      return element
    })
    expect(within(row).getByRole('textbox', { name: /Trading starts at/ })).toHaveValue('8')
    expect(row).toHaveTextContent('Read by Trader, Scheduler')
    await user.click(within(row).getByRole('button', { name: 'What it changes' }))
    expect(row).toHaveTextContent('The trader takes entries from this hour.')
    expect(within(row).getByRole('link', { name: 'Trading ends at' })).toHaveAttribute('href', expect.stringContaining('/config/hours#'))
  })

  it('switches an optional rule on with its settings, and keeps the change through another group', async () => {
    const user = userEvent.setup()
    renderPage('/config/hours')

    const news = (await screen.findByText('No entry around US releases')).closest('section') as HTMLElement
    expect(news).toHaveTextContent('Off: none of its settings applies.')
    await user.click(within(news).getByRole('switch'))
    // The fields are drawn again for the rule added.
    expect(await screen.findByRole('textbox', { name: /From before a release/ })).toHaveValue('5')

    await user.click(screen.getByRole('link', { name: /^Exits/ }))
    expect(await screen.findByText('2 unsaved changes')).toBeInTheDocument()
  })

  it('refuses a value out of its bounds before anything is sent', async () => {
    const user = userEvent.setup()
    renderPage('/config/hours')

    const field = await screen.findByRole('textbox', { name: /Trading starts at/ })
    await user.clear(field)
    await user.type(field, '25')

    expect(screen.getByText(/At most 23\./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review and save' })).toBeDisabled()
  })

  it('says a group that does not exist', async () => {
    renderPage('/config/nowhere')

    expect(await screen.findByRole('heading', { name: 'No such group of parameters' })).toBeInTheDocument()
  })
})

describe('saving', () => {
  it('reviews what the server finds the change reaches, then saves and says where it applies', async () => {
    const user = userEvent.setup()
    renderPage('/config/hours')

    const field = await screen.findByRole('textbox', { name: /Trading starts at/ })
    await user.clear(field)
    await user.type(field, '9')
    await user.click(screen.getByRole('button', { name: 'Review and save' }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText('The close of the day comes after trading ends.')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Applies at the next start of: Trader, Scheduler.')
    expect(dialog).toHaveTextContent('8 → 9')
    const validated = sent.find((request) => request.path === '/api/config/validate')
    expect(validated?.body).toMatchObject({ config: { trade: { rules: [{}, { rule_config: { trading_start_hour: 9 } }] } } })

    // The slide, with the keyboard.
    const slider = within(dialog).getByRole('slider')
    slider.focus()
    await user.keyboard('{Enter}')

    expect(await screen.findByText('Saved: 1 change')).toBeInTheDocument()
    expect(screen.getByText('docker restart trader1 scheduler1')).toBeInTheDocument()
    expect(sent.some((request) => request.method === 'PUT' && request.path === '/api/config')).toBe(true)
  })

  it('lets the operator go from group to group with changes unsaved, and asks before leaving the page', async () => {
    const user = userEvent.setup()
    const router = renderPage('/config/hours')

    const field = await screen.findByRole('textbox', { name: /Trading starts at/ })
    await user.clear(field)
    await user.type(field, '9')
    await router.navigate('/config/exits')
    expect(router.state.location.pathname).toBe('/config/exits')

    await router.navigate('/elsewhere')
    expect(await screen.findByText('Leave without saving?')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/config/exits')
  })
})
