import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { IoitiProvider, PageHeader } from 'ioiti-ui'
import { RouterRoot } from 'ioiti-ui/react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Link, MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import { NotFoundPage } from '@/features/NotFoundPage'
import type { AgentState, Position, ReleaseSummary, Session } from '@/lib/types'
import { run, RUN_ID } from '@/test/backtests'
import { openPosition, overview } from '@/test/overview'

import { AppShell } from './AppShell'
import { APP_NAME } from './navigation'

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
  /** The services the control service names for a restart; null when it cannot say. */
  restartServices?: Session['restart_services']
  release?: ReleaseSummary | null
  agent?: Partial<AgentState>
  /** Signing out, as the session's provider does it. */
  logout?: () => Promise<void>
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
  restartServices = null,
  release = null,
  agent = { enabled: false },
  logout = async () => {},
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
    '/api/agent': { model: 'claude-sonnet-5-5', ...agent },
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
          session: { ...SESSION, restart_required: restartRequired, restart_services: restartServices, release },
          error: null,
          login: async () => {},
          logout,
        }}
      >
        {/* As App.tsx has them: the application's name, then RouterRoot at the root route. */}
        <IoitiProvider appName={APP_NAME}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route element={<RouterRoot />}>
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
              </Route>
            </Routes>
            <Address />
          </MemoryRouter>
        </IoitiProvider>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  // The palette keeps the entries chosen last in this browser: each test starts without them.
  window.localStorage.clear()
  // A test that chose the look leaves the page as the pre-paint script would have it, no more.
  for (const name of ['data-theme-storage-key', 'data-accent', 'data-material']) document.documentElement.removeAttribute(name)
})

/** The top bar's summary of the states not to miss, once the page has drawn it. */
const statusButton = () => screen.findByRole('button', { name: /^Status:/ })

/** Its panel, opened. */
async function openStatus(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await statusButton())
  return within(await screen.findByRole('dialog', { name: 'Status' }))
}

describe('the states not to miss, in the top bar', () => {
  it('stand out in nothing while everything is as usual, and say so once opened', async () => {
    const user = userEvent.setup()
    renderShell()

    const panel = await openStatus(user)
    // The health summary has arrived: every service up, Saxo's login with them.
    expect(await panel.findByRole('link', { name: /All services up/ })).toHaveAttribute('href', '/system')
    expect(panel.getByRole('link', { name: /Saxo: authorised/ })).toHaveAttribute('href', '/system')
    // jsdom has no event stream: the page polls, said in grey, asking nothing.
    expect(panel.getByText('Polling')).toBeInTheDocument()
    expect(panel.queryByText('Restart needed')).not.toBeInTheDocument()
    expect(panel.getByRole('link', { name: 'Every service, on the System page' })).toHaveAttribute('href', '/system')
    expect(screen.getByRole('button', { name: 'Status: Polling' })).toBeInTheDocument()
  })

  it('say on every page that Saxo needs a new authorisation, and where to give it', async () => {
    const user = userEvent.setup()
    renderShell({ saxo: 'down' })

    const button = await screen.findByRole('button', { name: 'Status: Saxo: authorise' })
    expect(button).toHaveTextContent('Saxo: authorise')
    const panel = await openStatus(user)
    const saxo = panel.getByRole('link', { name: /Saxo: authorise/ })
    expect(saxo).toHaveAttribute('href', '/system')
    expect(saxo).toHaveTextContent(/nothing can be traded/)
    // The services apart from it are up: the trouble is said once.
    expect(panel.getByRole('link', { name: /All services up/ })).toBeInTheDocument()

    // Followed, the page is the System's and the panel is gone.
    await user.click(saxo)
    expect(screen.getByRole('status', { name: 'address' })).toHaveTextContent('/system')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Status' })).not.toBeInTheDocument())
  })

  it('do not raise the alarm when the token state could not be read', async () => {
    const user = userEvent.setup()
    renderShell({ saxo: 'unknown' })

    const panel = await openStatus(user)
    expect(await panel.findByRole('link', { name: /All services up/ })).toBeInTheDocument()
    expect(panel.queryByText(/Saxo/)).not.toBeInTheDocument()
  })

  it('say that the services run an older configuration, and link to where it is dealt with', async () => {
    const user = userEvent.setup()
    renderShell({ restartRequired: true })

    expect(await screen.findByRole('button', { name: 'Status: Restart needed' })).toBeInTheDocument()
    const panel = await openStatus(user)
    expect(panel.getByRole('link', { name: /Restart needed/ })).toHaveAttribute('href', '/config')
    // The notice itself is on the configuration page, not across the top of every page.
    expect(screen.queryByText('Configuration changed since the services started')).not.toBeInTheDocument()
  })

  it('say a release waiting, and how many states stand out', async () => {
    const user = userEvent.setup()
    renderShell({ restartRequired: true, restartServices: [{ id: 'trader', name: 'Trader' }], release: { newest: '0.95.0', containers: {}, waiting: ['admin1'], applying: false } })

    // Named by the gravest state's words, then how many of each tone stand out (ioiti-ui 0.27).
    expect(await screen.findByRole('button', { name: /^Status: .+ \(2 to watch\)$/ })).toHaveTextContent('+1')
    const panel = await openStatus(user)
    expect(panel.getByRole('link', { name: /Release waiting/ })).toHaveAttribute('href', '/system#release')
    expect(panel.getByRole('link', { name: /Restart needed/ })).toHaveAttribute('href', '/system')
  })
})

describe('live trading in the top bar', () => {
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
})

describe("the operator's menu", () => {
  const operator = () => screen.findByRole('button', { name: /^Operator/ })

  it('signs out last', async () => {
    const user = userEvent.setup()
    const logout = vi.fn(async () => {})
    renderShell({ logout })

    const button = await operator()
    // Actions and settings allowed, as usual: nothing said under the name.
    expect(button).toHaveTextContent(/^O?Operator$/)
    await user.click(button)
    const menu = within(await screen.findByRole('menu', { name: 'Account' }))
    const items = menu.getAllByRole('menuitem')
    expect(items.at(-1)).toHaveTextContent('Sign out')
    await user.click(items.at(-1)!)
    expect(logout).toHaveBeenCalledOnce()
    // The sidebar no longer carries it.
    expect(within(screen.getAllByRole('complementary')[0]).queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  })

  it("chooses the theme, and this browser's accent and material, which are no part of the configuration", async () => {
    const user = userEvent.setup()
    // The key the pre-paint script is given (vite.config.ts), which the theme's provider reads from the page.
    document.documentElement.setAttribute('data-theme-storage-key', 'wata.theme')
    renderShell()

    await user.click(await operator())
    let menu = within(await screen.findByRole('menu', { name: 'Account' }))
    expect(menu.getByRole('menuitemradio', { name: /Dark/ })).toBeInTheDocument()
    await user.click(menu.getByRole('menuitem', { name: /Accent and material/ }))
    menu = within(await screen.findByRole('menu', { name: 'Account' }))
    await user.click(await menu.findByRole('menuitemradio', { name: /Violet/ }))

    expect(document.documentElement).toHaveAttribute('data-accent', 'violet')
    // Kept beside the theme, in this browser alone.
    expect(window.localStorage.getItem('wata.theme.accent')).toBe('violet')
    // No button of its own in the top bar any more.
    expect(screen.queryByRole('button', { name: /^Theme:/ })).not.toBeInTheDocument()
  })

  it('opens the list of keyboard shortcuts', async () => {
    const user = userEvent.setup()
    renderShell()

    await user.click(await operator())
    await user.click(within(await screen.findByRole('menu', { name: 'Account' })).getByRole('menuitem', { name: /Keyboard shortcuts/ }))
    expect(await screen.findByRole('dialog', { name: 'Keyboard shortcuts' })).toBeInTheDocument()
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
    for (const [label, path] of [['Dashboard', '/'], ['Positions', '/positions'], ['Orders', '/orders'], ['Signals', '/signals'], ['Watchlist', '/watchlist']]) {
      expect(menu.getByRole('link', { name: label })).toHaveAttribute('href', path)
    }
    // The AI agent at the sidebar's foot, apart from the sections.
    const agent = screen.getByRole('link', { name: 'AI agent' })
    expect(agent).toHaveAttribute('href', '/agent')
    expect(screen.getByRole('navigation', { name: 'Main' })).not.toContainElement(agent)
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

  it('folds its sections but Trading, and opens the one of the page shown, on its entry and its pages', async () => {
    const user = userEvent.setup()
    renderShell({ path: '/logs' })
    const menu = within(await screen.findByRole('navigation', { name: 'Main' }))

    expect(menu.getByRole('button', { name: 'Analysis' })).toHaveAttribute('aria-expanded', 'false')
    expect(menu.queryByRole('link', { name: 'Backtests' })).not.toBeInTheDocument()
    // The page shown is under the System, in Operations: both open.
    expect(menu.getByRole('button', { name: 'Operations' })).toHaveAttribute('aria-expanded', 'true')
    expect(menu.getByRole('link', { name: 'Logs' })).toHaveAttribute('aria-current', 'page')
    expect(menu.getByRole('link', { name: 'Disk' })).toHaveAttribute('href', '/system/disk')
    expect(menu.getByRole('link', { name: 'System' })).toHaveAttribute('href', '/system')

    // Unfolded, a section shows its entries; Paper trading is under the Backtests.
    await user.click(menu.getByRole('button', { name: 'Analysis' }))
    await user.click(await menu.findByRole('button', { name: 'The pages under Backtests' }))
    expect(await menu.findByRole('link', { name: 'Paper trading' })).toHaveAttribute('href', '/backtests/paper')
  })

  it('says on its entries what their pages have to show, and on a section folded over them', async () => {
    renderShell({
      saxo: 'down',
      openPositions: [openPosition('5002', 'TURBO LONG NASDAQ-100 KO 24150 SG')],
      agent: { enabled: true, under_way: [], asking: ['20261119-080000-a1b2c3'] },
    })
    const menu = within(await screen.findByRole('navigation', { name: 'Main' }))

    expect(await menu.findByRole('link', { name: 'Positions, 1' })).toHaveAttribute('href', '/positions')
    expect(await screen.findByRole('link', { name: 'AI agent, 1 question' })).toHaveAttribute('href', '/agent')
    // The System says Saxo's login, folded under Operations: its heading says something is wrong under it.
    expect(await menu.findByRole('button', { name: 'Operations, with something wrong' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('keeps the sidebar folded to its icons as the operator left it, in this browser', async () => {
    const user = userEvent.setup()
    renderShell()

    await user.click(await screen.findByRole('button', { name: 'Collapse sidebar' }))
    expect(window.localStorage.getItem('wata.sidebar.collapsed')).toBe('true')
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
    // The same page: the same frame, where it was scrolled.
    expect(screen.getByRole('main')).toBe(main)
    expect(main.scrollTop).toBe(600)

    await user.click(screen.getByRole('link', { name: 'To the positions' }))
    await screen.findByRole('heading', { name: 'Positions' })
    // Another page: a frame of its own (ioiti-ui 0.12), at its top.
    expect(screen.getByRole('main')).not.toBe(main)
    expect(screen.getByRole('main').scrollTop).toBe(0)
  })
})

describe('the bell', () => {
  it('keeps what the live events told, each leading to where it is dealt with', async () => {
    FakeEventSource.opened = []
    vi.stubGlobal('EventSource', FakeEventSource)
    window.sessionStorage.clear()
    const user = userEvent.setup()
    renderShell()
    await statusButton()

    act(() =>
      FakeEventSource.opened[0].send('position_closed', {
        position_id: '5098', instrument_name: 'TURBO LONG', profit_loss: 25.2, performance_percent: 2.26,
      }),
    )
    await user.click(await screen.findByRole('button', { name: '1 unread notification' }))

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
    '/api/ta/rules': { revision: 'r1', rules: [], indices: [], instruments: [], engine_history_days: 1, engine_restart_time: '21:15' },
    '/api/ta/scripts': { items: [] },
    '/api/data/assets?seen=all': { timezone: 'Europe/Paris', seen: 'all', items: [], truncated: false, max: 3000, saxo_error: null },
  }

  it('goes to a page or a run from the keyboard', async () => {
    const user = userEvent.setup()
    renderShell({ bodies: BODIES })
    await statusButton()

    // ⌘K on a Mac, Ctrl K elsewhere (ioiti-ui 0.16 reads the platform): jsdom names none.
    await user.keyboard('{Control>}k{/Control}')
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
    await statusButton()

    await user.click(screen.getByRole('button', { name: /^Go to a page/ }))
    const palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    expect(palette.queryByRole('option', { name: /pause|resume|close|go live/i })).not.toBeInTheDocument()
    await waitFor(() => expect(palette.getByRole('combobox', { name: 'Search' })).toHaveFocus())
    await user.keyboard('5098')
    expect(palette.getByRole('option', { name: /Position 5098/ })).toHaveAttribute('href', '/positions?find=5098')
    expect(palette.getByRole('option', { name: /Order 5098/ })).toHaveAttribute('href', '/orders?find=5098')
  })

  it('opens the list of one kind, searched alone, and goes back from it', async () => {
    const user = userEvent.setup()
    renderShell({ bodies: BODIES })
    await statusButton()

    await user.click(screen.getByRole('button', { name: /^Go to a page/ }))
    const palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    // With nothing typed: the lists, then the pages; no run of its own at the top.
    await user.click(await palette.findByRole('option', { name: /Backtest runs/ }))
    expect(await palette.findByRole('option', { name: /Smoke EMA cross/ })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    expect(palette.queryByRole('option', { name: /^Positions/ })).toBeNull()
    // Back at the top with Backspace in the empty field: the lists again.
    await user.keyboard('{Backspace}')
    expect(await palette.findByRole('option', { name: /Scripts.*0 scripts/ })).toBeInTheDocument()
  })

  it('offers the entries chosen last first, the next time it is opened', async () => {
    const user = userEvent.setup()
    renderShell({ bodies: BODIES })
    await statusButton()

    await user.keyboard('{Control>}k{/Control}')
    let palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    await waitFor(() => expect(palette.getByRole('combobox', { name: 'Search' })).toHaveFocus())
    await user.keyboard('smoke')
    await palette.findByRole('option', { name: /Smoke EMA cross/ })
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Go to' })).not.toBeInTheDocument())

    await user.keyboard('{Control>}k{/Control}')
    palette = within(await screen.findByRole('dialog', { name: 'Go to' }))
    expect(await palette.findByRole('option', { name: /Smoke EMA cross/ })).toHaveAttribute('href', `/backtests/${RUN_ID}`)
    expect(window.localStorage.getItem('ioiti.theme.spotlight.go-to')).toContain(RUN_ID)
  })
})
