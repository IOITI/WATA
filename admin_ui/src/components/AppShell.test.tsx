import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import { NotFoundPage } from '@/features/NotFoundPage'
import type { Position, Session } from '@/lib/types'
import { run, RUN_ID } from '@/test/backtests'
import { openPosition, overview } from '@/test/overview'

import { AppShell } from './AppShell'
import { MENU } from './navigation'
import { PageHeader } from './Panel'
import { ToastProvider } from './ToastProvider'

const SESSION: Session = {
  authenticated: true,
  version: '0.11.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

interface ServerState {
  /** More answers of the fake server, by path. */
  bodies?: Record<string, unknown>
  saxo?: 'ok' | 'down' | 'unknown'
  pausedSince?: string | null
  openPositions?: Position[]
  restartRequired?: boolean
  /** The page shown. */
  path?: string
}

/** A stand-in for the browser's EventSource: the test plays the server's messages. */
class FakeEventSource {
  static opened: FakeEventSource[] = []
  private listeners = new Map<string, ((event: MessageEvent) => void)[]>()

  constructor() {
    FakeEventSource.opened.push(this)
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {}

  send(type: string, data: Record<string, unknown>) {
    for (const listener of this.listeners.get(type) ?? []) listener(new MessageEvent(type, { data: JSON.stringify({ type, ...data }) }))
  }
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

function renderShell({
  bodies: more = {},
  saxo = 'ok',
  pausedSince = null,
  openPositions = [],
  restartRequired = false,
  path = '/positions',
}: ServerState = {}) {
  const bodies: Record<string, unknown> = {
    ...more,
    '/api/system/health': {
      status: saxo === 'down' ? 'down' : 'ok',
      services: [
        { id: 'saxo', name: 'Saxo connection', status: saxo, detail: '' },
        { id: 'web_server', name: 'Web server', status: 'ok', detail: '' },
      ],
    },
    '/api/overview': overview({ entries_paused_since: pausedSince, open_positions: openPositions }),
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (!(path in bodies)) throw new Error(`Unexpected request: ${path}`)
      return new Response(JSON.stringify(bodies[path]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider
        value={{
          session: { ...SESSION, restart_required: restartRequired },
          error: null,
          login: async () => {},
          logout: async () => {},
        }}
      >
        <ToastProvider>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<PageHeader title="Dashboard" />} />
                <Route path="positions" element={<h1>Positions</h1>} />
                <Route
                  path="data/assets/:provider/:key"
                  element={
                    <>
                      <PageHeader title="Long 24,150 · SG" />
                      <Link to="/positions">To the positions</Link>
                      <Link to="?side=ask">The ask</Link>
                    </>
                  }
                />
                <Route
                  path="backtests/:runId/days/:day"
                  element={<PageHeader title="9 Oct 2026" parents={[{ label: 'Morning breakout', href: '/backtests/r1' }]} />}
                />
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Routes>
            <Address />
          </MemoryRouter>
        </ToastProvider>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('trading state in the top bar', () => {
  it('shows nothing while everything is as usual', async () => {
    renderShell()

    // The health summary has arrived: the chips had what they need to appear.
    expect(await screen.findByText('Up')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Saxo: authorise/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Entries paused/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Restart needed/ })).not.toBeInTheDocument()
  })

  it('says on every page that Saxo needs a new authorisation, and where to give it', async () => {
    renderShell({ saxo: 'down' })

    const chip = await screen.findByRole('link', { name: /Saxo: authorise/ })
    expect(chip).toHaveAttribute('href', '/system')
    // Named by what it is, described by what it means (its hint, on hover, focus and a tap).
    expect(chip).toHaveAccessibleName('Saxo: authorise')
    expect(chip).toHaveAccessibleDescription(/nothing can be traded/)
    // The summary beside it agrees.
    expect(screen.getByText('Down')).toBeInTheDocument()
  })

  it('does not raise the alarm when the token state could not be read', async () => {
    renderShell({ saxo: 'unknown' })

    expect(await screen.findByText('Up')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Saxo: authorise/ })).not.toBeInTheDocument()
  })

  it('says on every page that new entries are paused, and where to resume them', async () => {
    const user = userEvent.setup()
    renderShell({ pausedSince: '2026-10-12T05:09:22Z' })

    await user.click(await screen.findByRole('button', { name: 'Live trading: Entries paused' }))
    const island = screen.getByRole('region', { name: 'Live trading' })
    expect(within(island).getByRole('link', { name: 'Resume entries on the dashboard' })).toHaveAttribute('href', '/')
    expect(within(island).getByText(/paused/i, { selector: 'li' })).toBeInTheDocument()
  })

  it('gives the open positions’ live result on every page, and their Close, which the island keeps open', async () => {
    const user = userEvent.setup()
    renderShell({ openPositions: [openPosition('5002', 'TURBO LONG NASDAQ-100 KO 24150 SG')] })

    const pill = await screen.findByRole('button', { name: /^Live trading: 1 open/ })
    expect(pill).toHaveTextContent('+€20.80')
    await user.click(pill)
    const island = screen.getByRole('region', { name: 'Live trading' })
    expect(within(island).getByText('TURBO LONG NASDAQ-100 KO 24150 SG')).toBeInTheDocument()

    // Its confirmation over the page takes the clicks and Escape: the island stays behind it.
    await user.click(within(island).getByRole('button', { name: 'Close' }))
    const dialog = await screen.findByRole('dialog', { name: 'Close this position' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('region', { name: 'Live trading' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.getByRole('button', { name: /^Live trading: 1 open/ })).toHaveAttribute('aria-expanded', 'false'))
    // Back on the pill. (The island shrinks into it: jsdom, which measures nothing, never ends that animation.)
    expect(screen.getByRole('button', { name: /^Live trading: 1 open/ })).toHaveFocus()
  })

  it('says that the services run an older configuration, and links to it', async () => {
    renderShell({ restartRequired: true })

    expect(await screen.findByRole('link', { name: /Restart needed/ })).toHaveAttribute('href', '/config')
    // The notice itself is on the configuration page, not across the top of every page.
    expect(screen.queryByText('Configuration changed since the services started')).not.toBeInTheDocument()
  })

  it('carries the button that chooses the theme, on every page', async () => {
    renderShell()

    expect(await screen.findByRole('button', { name: /^Theme:/ })).toBeInTheDocument()
  })

})

/** Both forms of the trail are rendered here (no CSS): the whole one, then its last two steps. */
const trails = () => screen.getAllByRole('navigation', { name: 'Breadcrumb' })
const wholeTrail = () => within(trails()[0])
const shortTrail = () => within(trails()[1])
const isCurrent = (element: HTMLElement) => element.closest('[aria-current="page"]') !== null

describe('where the page is, in the top bar', () => {
  it('is said on every page: the section, which is no link, then the page, from Home', async () => {
    renderShell()

    expect(await wholeTrail().findByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
    expect(wholeTrail().getByText('Trading')).toBeInTheDocument()
    expect(wholeTrail().queryByRole('link', { name: 'Trading' })).not.toBeInTheDocument()
    expect(isCurrent(wholeTrail().getByText('Positions'))).toBe(true)
    // A narrow screen has room for the last two steps.
    expect(shortTrail().getByText('Trading')).toBeInTheDocument()
    expect(isCurrent(shortTrail().getByText('Positions'))).toBe(true)
    expect(shortTrail().queryByText('Home')).not.toBeInTheDocument()
  })

  it('is Home alone on the dashboard', async () => {
    renderShell({ path: '/' })

    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    expect(wholeTrail().getAllByRole('listitem')).toHaveLength(1)
    expect(isCurrent(wholeTrail().getByText('Home'))).toBe(true)
  })

  it('leads from a deeper page back through the pages it names, to its page of the menu', async () => {
    renderShell({ path: '/backtests/r1/days/2026-10-09' })

    expect(isCurrent(await wholeTrail().findByText('9 Oct 2026'))).toBe(true)
    expect(wholeTrail().getByText('Analysis')).toBeInTheDocument()
    expect(wholeTrail().getByRole('link', { name: 'Backtests' })).toHaveAttribute('href', '/backtests')
    expect(wholeTrail().getByRole('link', { name: 'Morning breakout' })).toHaveAttribute('href', '/backtests/r1')
    // On a narrow screen, the way back to the run stays.
    expect(shortTrail().getByRole('link', { name: 'Morning breakout' })).toHaveAttribute('href', '/backtests/r1')
    expect(isCurrent(shortTrail().getByText('9 Oct 2026'))).toBe(true)
  })

  it('forgets the name of a page left behind', async () => {
    const user = userEvent.setup()
    renderShell({ path: '/data/assets/saxo/2' })
    expect(isCurrent(await wholeTrail().findByText('Long 24,150 · SG'))).toBe(true)
    expect(wholeTrail().getByRole('link', { name: 'Assets View' })).toHaveAttribute('href', '/data/assets')

    await user.click(screen.getByRole('link', { name: 'To the positions' }))

    expect(isCurrent(await wholeTrail().findByText('Positions'))).toBe(true)
    expect(screen.queryByText('Long 24,150 · SG')).not.toBeInTheDocument()
  })

  it('says of an address that is no page that there is none', async () => {
    renderShell({ path: '/positions/nothing' })

    expect(isCurrent(await wholeTrail().findByText('Page not found'))).toBe(true)
    expect(wholeTrail().getByRole('link', { name: 'Positions' })).toHaveAttribute('href', '/positions')
    expect(screen.getByRole('heading', { level: 1, name: 'There is no page here.' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'There is no page here.' })).getByText('/positions/nothing')).toBeInTheDocument()
    expect(document.title).toBe('Page not found · WATA Admin')
    // The way out is a link too, and so is the page of the menu the address was under.
    expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/')
    const suggestions = within(screen.getByRole('navigation', { name: 'Helpful links' }))
    expect(suggestions.getByRole('link', { name: /^Positions/ })).toHaveAttribute('href', '/positions')
  })

  it('suggests nothing for an address under no page of the menu', async () => {
    renderShell({ path: '/nowhere' })

    expect(await screen.findByRole('heading', { level: 1, name: 'There is no page here.' })).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Helpful links' })).not.toBeInTheDocument()
  })
})

describe('the menu', () => {
  it('is made of links, each opening its page here, or in another tab', async () => {
    const user = userEvent.setup()
    renderShell({ path: '/' })
    const menu = within(await screen.findByRole('navigation', { name: 'Main' }))

    // An address each: what a right click offers to open in another tab.
    for (const entry of MENU) expect(menu.getByRole('link', { name: entry.label })).toHaveAttribute('href', entry.path)
    expect(menu.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')

    // A click with Ctrl or ⌘ is the browser's, for another tab: this one stays where it is.
    await user.keyboard('{Control>}')
    await user.click(menu.getByRole('link', { name: 'Positions' }))
    await user.keyboard('{/Control}')
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()

    await user.click(menu.getByRole('link', { name: 'Positions' }))
    expect(await screen.findByRole('heading', { name: 'Positions' })).toBeInTheDocument()
    expect(menu.getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
  })
})

describe('scrolling', () => {
  it('opens another page at its top, and leaves a page where it is when only its query changes', async () => {
    const user = userEvent.setup()
    renderShell({ path: '/data/assets/saxo/2' })
    await screen.findByRole('heading', { name: 'Long 24,150 · SG' })
    const main = screen.getByRole('main')

    main.scrollTop = 600
    await user.click(screen.getByRole('link', { name: 'The ask' }))
    expect(main.scrollTop).toBe(600)

    await user.click(screen.getByRole('link', { name: 'To the positions' }))
    await screen.findByRole('heading', { name: 'Positions' })
    expect(main.scrollTop).toBe(0)
  })
})

describe('the bell', () => {
  it('keeps what the live events told, each leading to where it is dealt with', async () => {
    FakeEventSource.opened = []
    vi.stubGlobal('EventSource', FakeEventSource)
    window.sessionStorage.clear()
    const user = userEvent.setup()
    renderShell()
    await screen.findByText('Up')

    act(() =>
      FakeEventSource.opened[0].send('position_closed', {
        position_id: '5098', instrument_name: 'TURBO LONG', profit_loss: 25.2, performance_percent: 2.26,
      }),
    )
    await user.click(await screen.findByRole('button', { name: '1 unread notifications' }))

    const panel = within(screen.getByRole('dialog', { name: 'Notifications' }))
    expect(panel.getByRole('link', { name: 'Position closed' })).toHaveAttribute('href', '/positions?find=5098')
    expect(panel.getByText('TURBO LONG · +€25.20 (+2.26%)')).toBeInTheDocument()
    // Kept for the tab: a reload of the page finds it.
    expect(window.sessionStorage.getItem('wata-admin-events')).toContain('Position closed')
    await user.click(panel.getByRole('button', { name: 'Read all' }))
    expect(screen.getByRole('button', { name: 'Notifications' })).toBeInTheDocument()

    await user.click(panel.getByRole('link', { name: 'Position closed' }))
    expect(screen.getByRole('status', { name: 'address' })).toHaveTextContent('/positions?find=5098')
    // Followed: the panel is closed.
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Notifications' })).not.toBeInTheDocument())
  })
})

describe('the palette (⌘K)', () => {
  const BODIES = {
    '/api/backtests': { items: [run()], storage: { runs: { count: 1, bytes: 1 }, former: { count: 0, bytes: 0 } } },
    '/api/ta/rules': { revision: 'r1', rules: [], indices: [], instruments: [], engine_history_days: 1 },
    '/api/ta/scripts': { items: [] },
    '/api/data/assets?seen=all': { timezone: 'Europe/Paris', seen: 'all', items: [], truncated: false, max: 3000, saxo_error: null },
  }

  it('goes to a page or a run from the keyboard', async () => {
    const user = userEvent.setup()
    renderShell({ bodies: BODIES })
    await screen.findByText('Up')

    await user.keyboard('{Meta>}k{/Meta}')
    const palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    await waitFor(() => expect(palette.getByRole('combobox', { name: 'Search' })).toHaveFocus())
    await user.keyboard('smoke')
    expect(await palette.findByRole('option', { name: /Smoke EMA cross/ })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    await user.keyboard('{Enter}')

    expect(screen.getByRole('status', { name: 'address' })).toHaveTextContent(`/backtests/${RUN_ID}`)
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Go to' })).not.toBeInTheDocument())
  })

  it('finds a position or an order by its number, and lists nothing that trades', async () => {
    const user = userEvent.setup()
    renderShell({ bodies: BODIES })
    await screen.findByText('Up')

    await user.click(screen.getByRole('button', { name: /^Go to a page/ }))
    const palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    expect(palette.queryByRole('option', { name: /pause|resume|close|go live/i })).not.toBeInTheDocument()
    await waitFor(() => expect(palette.getByRole('combobox', { name: 'Search' })).toHaveFocus())
    await user.keyboard('5098')
    expect(palette.getByRole('option', { name: /Position 5098/ })).toHaveAttribute('href', '/positions?find=5098')
    expect(palette.getByRole('option', { name: /Order 5098/ })).toHaveAttribute('href', '/orders?find=5098')
  })
})
