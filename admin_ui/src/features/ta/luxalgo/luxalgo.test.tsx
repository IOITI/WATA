import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from 'ioiti-ui'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type {
  AgentState,
  DataAsset,
  LuxAlgoCompat,
  LuxAlgoIndicator,
  LuxAlgoKnown,
  LuxAlgoKnownShort,
  LuxAlgoSource,
  LuxAlgoStatus,
  Session,
  TaScriptSummary,
} from '@/lib/types'
import { engines, resetVela, velas } from '@/test/fakeVela'
import { WithTrail } from '@/test/WithTrail'

import { IndicatorsPage } from '../IndicatorsPage'
import { LuxAlgoConceptPage } from './LuxAlgoConceptPage'
import { LuxAlgoIndicatorPage } from './LuxAlgoIndicatorPage'
import { LuxAlgoPage } from './LuxAlgoPage'

vi.mock('@luxalgo/vela', async () => (await import('@/test/fakeVela')).fakeVelaCore)
vi.mock('@luxalgo/vela-pinets', async () => (await import('@/test/fakeVela')).fakeVelaPinets)
// CodeMirror needs a laid-out page: the editor is a plain text box here (the browser checks see the real one).
vi.mock('@/features/ta/editor/PineEditor', () => ({
  PineEditor: ({ value, label, readOnly }: { value: string; label: string; readOnly?: boolean }) => (
    <textarea aria-label={label} value={value} readOnly={readOnly} onChange={() => undefined} />
  ),
}))

const SESSION: Session = {
  authenticated: true,
  version: '0.70.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

const SIGNED_OUT: LuxAlgoStatus = { enabled: true, signed_in: false, account: null, expires_at: null, refused: null }
const HEADER = '// This work is licensed under a Attribution-NonCommercial-ShareAlike 4.0 International (CC BY-NC-SA 4.0)\n// © LuxAlgo\n'
const INDICATOR: LuxAlgoIndicator = {
  slug: 'tri-star',
  name: 'Tri-star',
  author: 'LuxAlgo',
  family: 'patterns',
  body_markdown: 'Three doji, the [tri-star](/library/concept/tri-star/). ![a chart](https://cdn.example/x.png)',
  date_displayed: '2026-04-19',
  url: 'https://www.luxalgo.com/library/indicator/tri-star/',
  concepts: [{ slug: 'tri-star', name: 'tri-star', primary: true }],
  image_url: 'https://luxalgo-production.s3.amazonaws.com/library/cards/tri-star-6cd8deca7e.png',
  code: { available: true },
  known: { slug: 'tri-star', scripts: [], verdict: null, compat: null, runs: [], rules: [] },
}
const PART: LuxAlgoSource = {
  slug: 'tri-star', name: 'Tri-star', available: true, complete: false, source: `${HEADER}//@version=6`, shown_lines: 3,
  total_lines: 201, reason: 'sign-in-for-full-source', chart_url: 'https://vela.luxalgo.com/chart?pineScriptCodeId=x',
  licence: 'CC BY-NC-SA 4.0', owner: 'LuxAlgo',
}
const WHOLE: LuxAlgoSource = { ...PART, complete: true, source: `${HEADER}//@version=6\nindicator("Tri-star")\nplot(close)` }

// The assets a chart can be drawn on: an index of two providers, and a turbo, which is not offered.
const ASSETS = [
  { provider: 'capital', key: 'US100', ref: 'capital/US100', role: 'underlying', status: 'live', description: 'US Tech 100', symbol: 'US100' },
  { provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', role: 'underlying', status: 'history', description: 'Nasdaq 100', symbol: 'us100' },
  { provider: 'saxo', key: '12', ref: 'saxo/12', role: 'turbo', status: 'live', description: 'Turbo long', symbol: 'T1' },
] as unknown as DataAsset[]

let status: LuxAlgoStatus = SIGNED_OUT
let source: LuxAlgoSource = PART
let indicator: LuxAlgoIndicator = INDICATOR
let sent: { method: string; path: string; body: unknown }[] = []
// What WATA knows of the indicators, what its checks found, and whether the AI agent is on.
let known: LuxAlgoKnownShort[] = []
let checked: LuxAlgoCompat[] = []
let agent: Partial<AgentState> | null = null

const CHECKED_AT = '2026-10-31T09:00:00Z'
const RUNS: LuxAlgoCompat = { slug: 'tri-star', name: 'Tri-star', state: 'runs', checked_at: CHECKED_AT, alertconditions: ['Up'] }
const KNOWN: LuxAlgoKnown = {
  slug: 'tri-star',
  scripts: [{ id: '0123456789ab', name: 'Tri-star, mine', made_by: 'claude' }],
  verdict: { tone: 'mixed', line: 'Holds on half the days.' },
  compat: RUNS,
  runs: [{
    id: '20261030-101010-abcdef', name: 'Tri-star, a week', script_id: '0123456789ab', state: 'done', verdict: null,
    headline: { total_percent: -1.5, trades: 31, win_rate: 48, worst_drawdown: -3, sample: 'fair', beaten: 40, days_tested: 5,
      profit_loss: -15, cells: null },
  }],
  rules: [{ id: 'r0a1b2c3d', name: 'Tri-star on US100', mode: 'shadow', script_id: '0123456789ab', paper: { days: 2, outside: 1, last: [] } }],
}

function serve() {
  sent = []
  const json = (body: unknown, code = 200) =>
    new Response(code === 204 ? null : JSON.stringify(body), { status: code, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path: `${url.pathname}${url.search}`, body })
      if (url.pathname === '/api/luxalgo/status') return json(status)
      if (url.pathname === '/api/luxalgo/families') {
        return json({ families: [{ key: 'trend', name: 'Trend' }, { key: 'patterns', name: 'Patterns' }] })
      }
      if (url.pathname === '/api/luxalgo/indicators') {
        const page = Number(url.searchParams.get('page') ?? 0)
        return json({
          total: 30, page, page_size: 24,
          indicators: [{
            slug: page ? 'older-one' : 'tri-star', name: page ? 'Older one' : 'Tri-star', family: 'patterns', description: 'Three doji.',
            image_url: page ? undefined : INDICATOR.image_url,
          }],
        })
      }
      if (url.pathname === '/api/luxalgo/search') {
        return json({ results: [
          { kind: 'concept', slug: 'supertrend', name: 'Supertrend', family: 'trend' },
          { kind: 'indicator', slug: 'ai-supertrend', name: 'AI Supertrend', family: 'trend', description: 'Clusters.' },
        ] })
      }
      if (url.pathname === '/api/data/assets') return json({ timezone: 'Europe/Paris', items: ASSETS })
      if (url.pathname === '/api/luxalgo/indicators/tri-star') return json(indicator)
      if (url.pathname === '/api/luxalgo/known') return json({ items: known })
      if (url.pathname === '/api/luxalgo/compat' && method === 'POST') {
        const slugs = (body as { slugs: string[] }).slugs
        return json({ available: true, queued: slugs, added: slugs.length })
      }
      if (url.pathname === '/api/luxalgo/compat') return json({ items: checked, checks: { available: true, queued: [], checking: null } })
      if (url.pathname === '/api/agent' && agent) return json(agent)
      if (url.pathname === '/api/agent/verdicts') return json({ items: {} })
      if (url.pathname === '/api/luxalgo/indicators/tri-star/source') return json(source)
      if (url.pathname === '/api/luxalgo/indicators/tri-star/import' && method === 'POST') {
        return json({ id: '0123456789ab', name: (body as { name: string }).name, version: 1, kind: 'indicator',
          origin: { provider: 'luxalgo', slug: 'tri-star', licence: 'CC BY-NC-SA 4.0' } }, 201)
      }
      if (url.pathname === '/api/luxalgo/concepts/tri-star') {
        return json({ slug: 'tri-star', name: 'Tri-star', family: 'patterns', cluster: 'Candlestick reversals',
          content_markdown: '# Tri-star\n\nThree doji in a row, as the [Tri-star](/library/indicator/tri-star/) finds them.' })
      }
      if (url.pathname === '/api/luxalgo/sign-in' && method === 'POST') {
        return json({ url: 'https://app.luxalgo.com/api/auth/oauth2/authorize?state=x' })
      }
      if (url.pathname === '/api/luxalgo/sign-in/pasted' && method === 'POST') {
        return json({ ...SIGNED_OUT, signed_in: true, account: 'me@example.com' })
      }
      if (url.pathname === '/api/ta/scripts') return json({ items: SCRIPTS })
      return json({ detail: `Unknown indicator slug '${url.pathname.split('/').at(-1)}'.` }, 404)
    }),
  )
}

const SCRIPTS: TaScriptSummary[] = [
  {
    id: '0123456789ab', name: 'Tri-star', kind: 'indicator', description: '', pine_version: 6, library_name: null, imports: [],
    version: 1, versions: [], created_at: '2026-10-31T08:00:00+00:00', updated_at: '2026-10-31T08:00:00+00:00',
    origin: { provider: 'luxalgo', slug: 'tri-star', name: 'Tri-star', licence: 'CC BY-NC-SA 4.0', owner: 'LuxAlgo' },
  },
]

function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

function renderAt(address: string) {
  serve()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (element: React.ReactNode) => (
    <WithTrail>
      {element}
      <Address />
    </WithTrail>
  )
  const router = createMemoryRouter(
    [
      { path: '/ta/luxalgo', element: page(<LuxAlgoPage />) },
      { path: '/ta/luxalgo/indicators/:slug', element: page(<LuxAlgoIndicatorPage />) },
      { path: '/ta/luxalgo/concepts/:slug', element: page(<LuxAlgoConceptPage />) },
      { path: '/ta/indicators', element: page(<IndicatorsPage />) },
      { path: '/ta/indicators/:id', element: page(<p>The editor</p>) },
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

afterEach(() => {
  status = SIGNED_OUT
  source = PART
  indicator = INDICATOR
  known = []
  checked = []
  agent = null
  resetVela()
  vi.unstubAllGlobals()
})

describe('the LuxAlgo Library page', () => {
  it("lists the Library's indicators a page at a time, by family", async () => {
    const user = userEvent.setup()
    renderAt('/ta/luxalgo')
    expect(await screen.findByRole('link', { name: 'Tri-star' })).toHaveAttribute('href', '/ta/luxalgo/indicators/tri-star')
    // LuxAlgo's picture of it, loaded from LuxAlgo's image host.
    const card = screen.getByRole('link', { name: 'Tri-star' }).closest('li')!
    expect(card.querySelector('img')).toHaveAttribute('src', INDICATOR.image_url)
    expect(card.querySelector('img')).toHaveAttribute('alt', "LuxAlgo's picture of Tri-star")
    expect(screen.getByText(/A free LuxAlgo account is enough/)).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Older' }))
    expect(await screen.findByRole('link', { name: 'Older one' })).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox'), 'trend')
    await waitFor(() => expect(sent.at(-1)?.path).toBe('/api/luxalgo/indicators?family=trend&page=0&page_size=24'))
    expect(screen.getByTestId('address')).toHaveTextContent('/ta/luxalgo?family=trend')
  })

  it('searches its concepts and indicators', async () => {
    const user = userEvent.setup()
    renderAt('/ta/luxalgo')
    await user.type(await screen.findByPlaceholderText(/Supertrend/), 'supertrend')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByRole('link', { name: 'Supertrend' })).toHaveAttribute('href', '/ta/luxalgo/concepts/supertrend')
    expect(screen.getByRole('link', { name: 'AI Supertrend' })).toHaveAttribute('href', '/ta/luxalgo/indicators/ai-supertrend')
    // A search names no picture: its card has a stand-in.
    expect(screen.getByRole('link', { name: 'AI Supertrend' }).closest('li')!.querySelector('img')).toBeNull()
    expect(sent.some((request) => request.path === '/api/luxalgo/search?query=supertrend&limit=40')).toBe(true)
  })

  it('marks the indicators WATA knows on their cards, and asks for the checks of a page', async () => {
    known = [{ slug: 'tri-star', scripts: KNOWN.scripts, verdict: { tone: 'negative', line: 'No edge.' }, compat: RUNS, runs: 1, rules: [] }]
    const user = userEvent.setup()
    renderAt('/ta/luxalgo')
    const card = (await screen.findByRole('link', { name: 'Tri-star' })).closest('li')!
    expect(await within(card).findByText('Taken')).toBeInTheDocument()
    expect(within(card).getByText('Tested: no')).toBeInTheDocument()
    expect(within(card).getByText('Runs on WATA')).toBeInTheDocument()

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check this page' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Check this page' }))
    expect(await screen.findByText('1 indicator to check')).toBeInTheDocument()
    expect(sent.find((request) => request.method === 'POST')).toEqual({ method: 'POST', path: '/api/luxalgo/compat', body: { slugs: ['tri-star'] } })
  })

  it('lists the indicators WATA checked, by what came of it', async () => {
    checked = [RUNS, { slug: 'broken-one', name: 'Broken one', state: 'fails', error: 'Unknown function ta.foo', checked_at: CHECKED_AT }]
    const user = userEvent.setup()
    renderAt('/ta/luxalgo')
    await user.click(await screen.findByRole('link', { name: 'Checked by WATA' }))
    expect(screen.getByTestId('address')).toHaveTextContent('/ta/luxalgo?view=checked')
    expect(await screen.findByRole('link', { name: 'Broken one' })).toHaveAttribute('href', '/ta/luxalgo/indicators/broken-one')
    expect(screen.getByText('It does not compile on WATA: Unknown function ta.foo.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Tri-star' })).toBeInTheDocument()

    await user.selectOptions(screen.getByRole('combobox', { name: /Showing/ }), 'fails')
    expect(screen.queryByRole('link', { name: 'Tri-star' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Broken one' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Library' }))
    expect(await screen.findByRole('link', { name: 'Tri-star' })).toHaveAttribute('href', '/ta/luxalgo/indicators/tri-star')
  })

  it('says when LuxAlgo is off, and nothing else', async () => {
    status = { ...SIGNED_OUT, enabled: false }
    renderAt('/ta/luxalgo')
    expect(await screen.findByText("LuxAlgo's tools are switched off")).toBeInTheDocument()
    expect(sent.some((request) => request.path.startsWith('/api/luxalgo/indicators'))).toBe(false)
  })

  it('opens the sign-in in another tab and takes the address pasted', async () => {
    const user = userEvent.setup()
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    renderAt('/ta/luxalgo?luxalgo=refused&why=access_denied')
    expect(await screen.findByText('The sign-in to LuxAlgo did not finish: access_denied.')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Sign in to LuxAlgo' }))
    expect(opened).toHaveBeenCalledWith('https://app.luxalgo.com/api/auth/oauth2/authorize?state=x', '_blank', 'noopener,noreferrer')
    await user.type(screen.getByLabelText('The address LuxAlgo sent the browser to'), 'https://admin.test/api/luxalgo/callback?code=c&state=s')
    await user.click(screen.getByRole('button', { name: 'Finish the sign-in' }))
    expect(await screen.findByText(/Signed in as me@example.com/)).toBeInTheDocument()
    expect(sent.find((request) => request.path === '/api/luxalgo/sign-in/pasted')?.body).toEqual({
      address: 'https://admin.test/api/luxalgo/callback?code=c&state=s',
    })
    opened.mockRestore()
  })
})

describe("an indicator's page", () => {
  it('shows its write-up with its links led here and its images left out', async () => {
    renderAt('/ta/luxalgo/indicators/tri-star')
    expect(await screen.findByRole('heading', { name: 'Tri-star', level: 1 })).toBeInTheDocument()
    const writeUp = screen.getByRole('article', { name: 'AI response' })
    expect(within(writeUp).getByRole('link', { name: 'tri-star' })).toHaveAttribute('href', '/ta/luxalgo/concepts/tri-star')
    expect(within(writeUp).queryByText(/a chart/)).toBeNull()
    expect(await screen.findByRole('link', { name: /Open on Vela/ })).toHaveAttribute('href', PART.chart_url)
  })

  it('refuses to take a part of its source, says why, and shows LuxAlgo\'s picture where the chart would be', async () => {
    const user = userEvent.setup()
    renderAt('/ta/luxalgo/indicators/tri-star')
    // In the hint of the button it disables.
    expect((await screen.findAllByText(/LuxAlgo gives 3 of its 201 lines to an admin not signed in/)).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Take as my script' })).toBeDisabled()
    const chart = screen.getByRole('heading', { name: 'On a chart' }).closest('section')!
    expect(within(chart).getByRole('img', { name: "LuxAlgo's picture of Tri-star" })).toHaveAttribute('src', INDICATOR.image_url)
    expect(within(chart).getByText(/Sign in to LuxAlgo to run it on WATA's bars/)).toBeInTheDocument()
    expect(velas).toHaveLength(0)
    const glance = screen.getByRole('heading', { name: 'At a glance' }).closest('section')!
    expect(within(glance).getByText('3 of 201 lines, signed out')).toBeInTheDocument()

    // Its first lines in the source's tab, read only, with the note (said in the hint already).
    const noted = screen.getAllByText(/sign in to LuxAlgo on the Library's page to read it whole/).length
    await user.click(screen.getByRole('link', { name: 'Pine source' }))
    expect(screen.getByTestId('address')).toHaveTextContent('/ta/luxalgo/indicators/tri-star?tab=source')
    const code = await screen.findByRole('textbox', { name: 'Pine source of Tri-star' })
    expect(code).toHaveValue(PART.source)
    expect(code).toHaveAttribute('readonly')
    expect(screen.getAllByText(/sign in to LuxAlgo on the Library's page to read it whole/)).toHaveLength(noted + 1)
  })

  it('runs a whole source on a chart of WATA\'s bars, taking nothing, and says where it fails', async () => {
    status = { ...SIGNED_OUT, signed_in: true, account: 'me@example.com' }
    source = WHOLE
    const user = userEvent.setup()
    renderAt('/ta/luxalgo/indicators/tri-star')
    await waitFor(() => expect(velas).toHaveLength(1))
    const [first] = velas
    // On the index the Charts page opens on, five-minute bars, not live.
    expect(first.options).toMatchObject({ symbol: 'capital:CAPITAL_US100', timeframe: '5', live: false })
    await waitFor(() => expect(first.indicators[0]?.source).toContain('indicator("Tri-star")'))
    expect(first.indicators[0].options).toMatchObject({ id: 'luxalgo', title: 'Tri-star' })
    // The indices alone are offered, no turbo.
    const asset = screen.getByRole('combobox', { name: /^Asset/ })
    expect(within(asset).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'US Tech 100 · Capital.com', 'Nasdaq 100 · Dukascopy',
    ])

    // Another length of bars: built again, the chart before ended with its worker.
    await user.selectOptions(screen.getByRole('combobox', { name: /^Bars/ }), '15')
    await waitFor(() => expect(velas).toHaveLength(2))
    expect(velas[1].options.timeframe).toBe('15')
    expect(first.destroyed).toBe(true)
    expect(engines[0].terminated).toBe(true)
    act(() => velas[1].emit('indicator:error', { error: new Error('Undeclared identifier') }))
    expect(await screen.findByText('It did not run on these bars: Undeclared identifier')).toBeInTheDocument()
    // Nothing was sent to take or save it.
    expect(sent.filter((request) => request.method !== 'GET')).toEqual([])
    const glance = screen.getByRole('heading', { name: 'At a glance' }).closest('section')!
    expect(within(glance).getByText('Whole, 201 lines')).toBeInTheDocument()
    expect(within(glance).getByRole('link', { name: /Open on Vela/ })).toHaveAttribute('href', WHOLE.chart_url)
  })

  it('names its place in the trail once', async () => {
    renderAt('/ta/luxalgo/indicators/tri-star')
    await screen.findByRole('heading', { name: 'Tri-star', level: 1 })
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Technical Analysis.*LuxAlgo Library.*Tri-star/)
    expect(within(trail).getAllByText('LuxAlgo Library')).toHaveLength(1)
  })

  it('takes a whole source as your script and opens it', async () => {
    status = { ...SIGNED_OUT, signed_in: true, account: 'me@example.com' }
    source = WHOLE
    const user = userEvent.setup()
    renderAt('/ta/luxalgo/indicators/tri-star')
    await screen.findByRole('button', { name: 'Take as my script' })
    // Once its whole source is read: the button is drawn again, out of its hint.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Take as my script' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Take as my script' }))
    const dialog = await screen.findByRole('dialog', { name: 'Take Tri-star as your script' })
    expect(within(dialog).getByText(/under CC BY-NC-SA 4.0: non-commercial use only/)).toBeInTheDocument()
    const name = within(dialog).getByLabelText('Name of your script')
    await user.clear(name)
    await user.type(name, 'Tri-star, mine')
    await user.click(within(dialog).getByRole('button', { name: 'Take it' }))
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/indicators/0123456789ab'))
    expect(sent.find((request) => request.method === 'POST')?.body).toEqual({ name: 'Tri-star, mine' })
  })

  it('says what WATA knows of it, and checks it again when asked', async () => {
    indicator = { ...INDICATOR, known: KNOWN }
    const user = userEvent.setup()
    renderAt('/ta/luxalgo/indicators/tri-star?tab=wata')
    const panel = (await screen.findByRole('heading', { name: 'In WATA' })).closest('section')!
    expect(within(panel).getByRole('link', { name: 'Tri-star, mine' })).toHaveAttribute('href', '/ta/indicators/0123456789ab')
    expect(within(panel).getByRole('link', { name: 'Tri-star, a week' })).toHaveAttribute('href', '/backtests/20261030-101010-abcdef')
    expect(within(panel).getByText(/2 days on paper, 1 outside its reference's range/)).toBeInTheDocument()
    // The verdict on the indicator is the page's, said once (AgentVerdict), not the panel's.
    expect(within(panel).queryByText('Holds on half the days.')).toBeNull()
    expect(within(panel).getByText(/It compiles on WATA and declares 1 alert \(Up\)/)).toBeInTheDocument()

    // Once the checks are known to run here: the button is drawn again, out of its hint.
    await waitFor(() => expect(within(panel).getByRole('button', { name: 'Check it again' })).toBeEnabled())
    await user.click(within(panel).getByRole('button', { name: 'Check it again' }))
    await waitFor(() => expect(sent.find((request) => request.method === 'POST')?.body).toEqual({ slugs: ['tri-star'] }))
  })

  it('leads to the AI agent, about it, while the agent is on', async () => {
    agent = { enabled: true }
    renderAt('/ta/luxalgo/indicators/tri-star')
    expect(await screen.findByRole('link', { name: 'Ask the AI agent about it' })).toHaveAttribute(
      'href', '/ta/luxalgo/indicators/tri-star?agent=new&about=luxalgo%3Atri-star',
    )
  })

  it('is the 404 for an indicator LuxAlgo does not have', async () => {
    renderAt('/ta/luxalgo/indicators/no-such-one')
    expect(await screen.findByRole('heading', { name: 'No such indicator' })).toBeInTheDocument()
  })
})

describe("a concept's page", () => {
  it('shows its write-up, and leads to its indicators', async () => {
    const user = userEvent.setup()
    renderAt('/ta/luxalgo/concepts/tri-star')
    expect(await screen.findByText(/Three doji in a row/)).toBeInTheDocument()
    expect(screen.getByText('A concept of Patterns (Candlestick reversals).')).toBeInTheDocument()
    // Its own title is the page's heading alone.
    expect(screen.getAllByRole('heading', { name: 'Tri-star' })).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Tri-star' })).toHaveAttribute('href', '/ta/luxalgo/indicators/tri-star')

    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Technical Analysis.*LuxAlgo Library.*Tri-star/)
    expect(within(trail).getAllByText('LuxAlgo Library')).toHaveLength(1)

    await user.click(screen.getByRole('link', { name: 'Its indicators' }))
    expect(await screen.findByText('Indicators of tri-star')).toBeInTheDocument()
    expect(sent.some((request) => request.path === '/api/luxalgo/indicators?concept=tri-star&page=0&page_size=24')).toBe(true)
  })
})

describe('a script taken from LuxAlgo', () => {
  it('is marked so among the Indicators', async () => {
    renderAt('/ta/indicators')
    expect(await screen.findByText('LuxAlgo')).toBeInTheDocument()
  })
})
