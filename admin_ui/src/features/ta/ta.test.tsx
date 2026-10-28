import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { AgentConversation, AgentConversationListed, AgentState, Session, TaScript, TaScriptSummary } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { IndicatorsPage } from './IndicatorsPage'
import { ScriptEditorPage } from './ScriptEditorPage'

// CodeMirror needs a laid-out page: the editor is a plain text box here (the browser checks see the real one).
vi.mock('./editor/PineEditor', () => ({
  PineEditor: ({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) => (
    <textarea aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}))

// The worker compiles; a source containing "BROKEN" fails on its fourth line, as PineTS would say it.
vi.mock('./engine/pineWorkerClient', () => ({
  PineWorkerClient: class {
    compile = async (source: string) =>
      source.includes('BROKEN')
        ? { id: 1, ok: false, error: { message: 'Unexpected token at 4:3', line: 4, column: 3 } }
        : { id: 1, ok: true, op: 'compile', declaration: 'indicator', inputs: [] }
    run = async () => ({ id: 2, ok: true, op: 'run', milliseconds: 3, bars: 10, plots: [], alerts: [], warnings: [] })
    terminate() {}
  },
}))

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

const EMA_SOURCE = '//@version=6\nindicator("EMA")\nplot(ta.ema(close, 20))\n'

function summary(changes: Partial<TaScriptSummary>): TaScriptSummary {
  return {
    id: '0123456789ab',
    name: 'EMA',
    kind: 'indicator',
    description: 'An average',
    pine_version: 6,
    library_name: null,
    imports: [],
    version: 2,
    versions: [
      { number: 1, saved_at: '2026-10-18T10:00:00+00:00', size: 40, sha256: 'a' },
      { number: 2, saved_at: '2026-10-19T10:00:00+00:00', size: 44, sha256: 'b' },
    ],
    created_at: '2026-10-18T10:00:00+00:00',
    updated_at: '2026-10-19T10:00:00+00:00',
    ...changes,
  }
}

const EMA: TaScript = { ...summary({}), source: EMA_SOURCE }
const HELPERS = summary({
  id: 'aaaaaaaaaaaa', name: 'Helpers', kind: 'library', library_name: 'Helpers', description: '', version: 1,
})
// A script Claude made through the admin's MCP endpoint.
const DOUBLED = summary({
  id: 'bbbbbbbbbbbb', name: 'Doubled', description: '', version: 1,
  imports: [{ user: 'someone', name: 'Helpers', version: 1, alias: 'h' }], made_by: 'claude',
})

// A conversation of the AI agent about EMA: a new version of it and a change to a rule that runs it.
const CONVERSATION_ID = '20261028-080000-a1b2c3'
const AGENT: AgentState = {
  enabled: true, model: 'claude-opus-5-5', model_label: 'Claude Opus 5.5', key: true, pause: null, started: true,
  month: { month: '2026-10', spent_usd: 0.1, budget_usd: 50 }, under_way: [], conversations: 1,
  limits: { tool_calls: 60, backtests: 8, request_usd: 5, running: 2 },
}
const ABOUT_EMA = { kind: 'script', id: EMA.id, label: 'EMA', version: 2 } as const
const LISTED: AgentConversationListed = {
  id: CONVERSATION_ID, title: 'Improve it', subject: ABOUT_EMA, status: 'idle', model: 'claude-opus-5-5',
  created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:05:00Z', requests: 1, cost_usd: 0.1, outcome: 'done', error: null,
}
const PROPOSING: AgentConversation = {
  id: CONVERSATION_ID, title: 'Improve it', subject: ABOUT_EMA, status: 'idle', model: 'claude-opus-5-5',
  created_at: LISTED.created_at, updated_at: LISTED.updated_at, usage: {}, cost_usd: 0.1, error: null, pending: null,
  requests: [{
    index: 0, prompt: 'Improve it', shared: ABOUT_EMA, asked_at: '2026-10-28T08:00:00Z', started_at: '2026-10-28T08:00:01Z',
    ended_at: '2026-10-28T08:05:00Z', status: 'done', detail: null, steps: [],
    answer: { text: 'Two changes.', model: 'claude-opus-5-5', stop_reason: 'end_turn' },
    proposals: ['p0a1b2c3d', 'p1a1b2c3d'], runs: [], cost_usd: 0.1, feedback: null,
  }],
  proposals: [
    {
      id: 'p0a1b2c3d', kind: 'script', request: 0, created_at: '2026-10-28T08:04:00Z', status: 'proposed',
      title: 'A slower fast line', rationale: 'It traded less noise.', script: { id: EMA.id, name: 'EMA', base_version: 2 },
      draft: { id: 'eeeeeeeeeeee', name: 'EMA by Claude', version: 1 }, lines: { added: 1, removed: 1 },
    },
    {
      id: 'p1a1b2c3d', kind: 'rule', request: 0, created_at: '2026-10-28T08:04:30Z', status: 'proposed',
      title: 'A longer history', rationale: 'Its averages need it.',
      rule: { id: 'r1', name: 'EMA rule', mode: 'shadow', script_id: EMA.id, script_version: 2 },
      changes: { history_days: { before: null, after: 5 } },
    },
  ],
}

let sent: { method: string; path: string; body: unknown }[] = []
/** The assets the fake server lists (the editor's test offers them). */
let assets: unknown[] = []

function serve({ saveConflict = false, deleteConflict = false } = {}) {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const url = new URL(path, 'http://admin.test')
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path: url.pathname, body })
      if (url.pathname === '/api/ta/scripts' && method === 'GET') return json({ items: [EMA, HELPERS, DOUBLED] })
      if (url.pathname === '/api/ta/scripts' && method === 'POST') {
        return json({ ...EMA, id: 'cccccccccccc', name: (body as { name: string }).name, version: 1 }, 201)
      }
      if (url.pathname === `/api/ta/scripts/${EMA.id}` && method === 'GET') return json(EMA)
      const version = /^\/api\/ta\/scripts\/[0-9a-f]+\/versions\/(\d+)$/.exec(url.pathname)
      if (version && method === 'GET') {
        const number = Number(version[1])
        const entry = EMA.versions.find((candidate) => candidate.number === number)
        return json({ ...entry, source: number === EMA.version ? EMA_SOURCE : EMA_SOURCE.replace('20', '10') })
      }
      if (url.pathname === `/api/ta/scripts/${EMA.id}` && method === 'PUT') {
        if (saveConflict) {
          return json({ detail: 'The script was saved meanwhile (version 3, not 2): reload it, or save yours as a copy' }, 409)
        }
        const update = body as { source: string; name: string }
        return json({ ...EMA, ...update, version: 3 })
      }
      if (url.pathname === `/api/ta/scripts/${HELPERS.id}` && method === 'DELETE') {
        return deleteConflict ? json({ detail: "'Helpers' is used by the script 'Doubled'" }, 409) : json(null, 204)
      }
      if (url.pathname.endsWith('/duplicate')) return json({ ...EMA, id: 'dddddddddddd', name: (body as { name: string }).name }, 201)
      if (url.pathname.startsWith('/api/ta/libraries/')) return json({ detail: 'No library' }, 404)
      if (url.pathname === '/api/data/assets') return json({ timezone: 'Europe/Paris', seen: 'all', items: assets, truncated: false, max: 5000, saxo_error: null })
      if (url.pathname === '/api/agent') return json(AGENT)
      if (url.pathname === '/api/agent/conversations' && method === 'GET') return json({ items: [LISTED] })
      if (url.pathname === `/api/agent/conversations/${CONVERSATION_ID}`) return json(PROPOSING)
      return json({ detail: `Not served in this test: ${method} ${url.pathname}` }, 404)
    }),
  )
}

function Address() {
  const location = useLocation()
  return <output data-testid="address">{location.pathname}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const page = (element: React.ReactNode) => (
    <WithTrail>
      {element}
      <Address />
    </WithTrail>
  )
  const router = createMemoryRouter(
    [
      { path: '/ta/indicators', element: page(<IndicatorsPage />) },
      { path: '/ta/indicators/new', element: page(<ScriptEditorPage />) },
      { path: '/ta/indicators/:id', element: page(<ScriptEditorPage />) },
      { path: '/login', element: <p>Sign in</p> },
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

/** The row of the list whose script has this name (other rows may link to it). */
function rowOf(name: string): HTMLElement {
  const row = screen.getAllByRole('row').find((candidate) => candidate.querySelector('td a')?.textContent === name)
  if (!row) throw new Error(`No row for ${name}`)
  return row
}

beforeEach(() => {
  assets = []
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Indicators page', () => {
  it('lists the scripts, their kind, and which scripts use a library', async () => {
    renderAt('/ta/indicators')

    expect(await screen.findByRole('link', { name: 'EMA' })).toHaveAttribute('href', `/ta/indicators/${EMA.id}`)
    // A library says who imports it, and a script what it imports, each a link to the other.
    const helpers = rowOf('Helpers')
    expect(within(helpers).getByText('library("Helpers")')).toBeInTheDocument()
    expect(within(helpers).getByRole('link', { name: 'Doubled' })).toHaveAttribute('href', `/ta/indicators/${DOUBLED.id}`)
    expect(within(rowOf('Doubled')).getByRole('link', { name: 'Helpers' })).toHaveAttribute('href', `/ta/indicators/${HELPERS.id}`)
    expect(within(rowOf('EMA')).getByText('None')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Download EMA as a .pine file' })).toHaveAttribute(
      'href',
      `/api/ta/scripts/${EMA.id}/source.pine`,
    )
    expect(screen.getByRole('link', { name: 'Open EMA in Charts' })).toHaveAttribute('href', `/ta/charts?script=${EMA.id}`)
    expect(screen.getByRole('link', { name: 'Backtest EMA' })).toHaveAttribute('href', `/backtests/new?script=${EMA.id}&version=2`)
    // A library is not opened on a chart by itself, nor backtested.
    expect(screen.queryByRole('link', { name: 'Open Helpers in Charts' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Backtest Helpers' })).not.toBeInTheDocument()
    // Claude's script says so beside its name.
    expect(within(rowOf('Doubled')).getByText('Claude')).toBeInTheDocument()
    expect(within(rowOf('EMA')).queryByText('Claude')).toBeNull()
    // A new script is a page: its buttons are links, which can be opened in another tab.
    expect(screen.getByRole('link', { name: 'New indicator' })).toHaveAttribute('href', '/ta/indicators/new?kind=indicator')
    expect(screen.getByRole('link', { name: 'New strategy' })).toHaveAttribute('href', '/ta/indicators/new?kind=strategy')
    expect(screen.getByRole('link', { name: 'New library' })).toHaveAttribute('href', '/ta/indicators/new?kind=library')
  })

  it('asks before deleting, and says why a library still imported is kept', async () => {
    serve({ deleteConflict: true })
    const user = userEvent.setup()
    renderAt('/ta/indicators')

    await user.click(await screen.findByRole('button', { name: 'Delete Helpers' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete Helpers?' })
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await within(dialog).findByText("It cannot be deleted: 'Helpers' is used by the script 'Doubled'.")).toBeInTheDocument()
    expect(sent.some((request) => request.method === 'DELETE' && request.path === `/api/ta/scripts/${HELPERS.id}`)).toBe(true)
  })

  it('duplicates a script under the name given, and opens the copy', async () => {
    const user = userEvent.setup()
    renderAt('/ta/indicators')

    await user.click(await screen.findByRole('button', { name: 'Duplicate EMA' }))
    const dialog = await screen.findByRole('dialog', { name: 'Duplicate EMA' })
    const name = within(dialog).getByRole('textbox')
    await user.clear(name)
    await user.type(name, 'EMA fifty')
    await user.click(within(dialog).getByRole('button', { name: 'Duplicate' }))

    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/indicators/dddddddddddd'))
    expect(sent.find((request) => request.path.endsWith('/duplicate'))?.body).toEqual({ name: 'EMA fifty' })
  })

  it('imports .pine files dropped on it one after the other, libraries first, each with its outcome', async () => {
    renderAt('/ta/indicators')
    const zone = await screen.findByRole('button', { name: /^Drop \.pine files here/ })
    const script = new File(['//@version=6\nindicator("Mine")\nimport someone/Kit/1 as k'], 'mine.pine', { type: 'text/plain' })
    const library = new File(['//@version=6\nlibrary("Kit")'], 'kit.pine', { type: 'text/plain' })

    fireEvent.drop(zone, { dataTransfer: { files: [script, library] } })

    await waitFor(() => expect(screen.getAllByText('Imported')).toHaveLength(2))
    expect(sent.filter((request) => request.method === 'POST').map((request) => (request.body as { name: string }).name)).toEqual(['Kit', 'Mine'])
    expect(screen.getAllByRole('link', { name: 'Open' })[0]).toHaveAttribute('href', '/ta/indicators/cccccccccccc')
    // Several stay on the page, listed.
    expect(screen.getByTestId('address')).toHaveTextContent(/^\/ta\/indicators$/)
  })
})

describe('the script editor', () => {
  it('leads to the next steps of the script as saved: its chart, a backtest, a signal rule', async () => {
    renderAt(`/ta/indicators/${EMA.id}`)

    await screen.findByRole('textbox', { name: 'Pine source of EMA' })
    expect(screen.getByRole('link', { name: 'Open in Charts' })).toHaveAttribute('href', `/ta/charts?script=${EMA.id}`)
    expect(screen.getByRole('link', { name: 'Backtest v2' })).toHaveAttribute('href', `/backtests/new?script=${EMA.id}&version=2`)
    expect(screen.getByRole('link', { name: 'New signal rule' })).toHaveAttribute('href', `/ta/signals?new=${EMA.id}`)
  })

  it('saves a changed source as a new version, naming the version it started from', async () => {
    const user = userEvent.setup()
    renderAt(`/ta/indicators/${EMA.id}`)

    const editor = await screen.findByRole('textbox', { name: 'Pine source of EMA' })
    expect(screen.getByRole('button', { name: 'Saved' })).toBeDisabled()
    await user.type(editor, '// tuned')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'PUT')).toBe(true))
    expect(sent.find((request) => request.method === 'PUT')?.body).toMatchObject({
      base_version: 2,
      source: `${EMA_SOURCE}// tuned`,
      name: 'EMA',
    })
    expect(await screen.findByRole('button', { name: 'Saved' })).toBeDisabled()
  })

  it('asks before a script with changes is left, and lets a session that ended go to the sign-in', async () => {
    const user = userEvent.setup()
    const router = renderAt(`/ta/indicators/${EMA.id}`)

    await user.type(await screen.findByRole('textbox', { name: 'Pine source of EMA' }), '// tuned')
    await router.navigate('/ta/indicators')
    const dialog = await screen.findByRole('dialog', { name: 'Leave without saving?' })
    expect(dialog).toHaveTextContent('The script has changes. Nothing of it has been saved.')
    await user.click(within(dialog).getByRole('button', { name: 'Stay on this page' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByTestId('address')).toHaveTextContent(`/ta/indicators/${EMA.id}`)

    await router.navigate('/login')
    expect(await screen.findByText('Sign in')).toBeInTheDocument()
  })

  it("holds back a new version the AI agent proposed while the editor has changes, not a rule's change", async () => {
    const user = userEvent.setup()
    const router = renderAt(`/ta/indicators/${EMA.id}`)

    await user.type(await screen.findByRole('textbox', { name: 'Pine source of EMA' }), '// tuned')
    await router.navigate(`/ta/indicators/${EMA.id}?agent=${CONVERSATION_ID}`)

    const version = await screen.findByRole('region', { name: 'Proposal: A slower fast line' })
    expect(within(version).getByRole('button', { name: 'Save as version 3' })).toBeDisabled()
    const rule = screen.getByRole('region', { name: 'Proposal: A longer history' })
    expect(within(rule).getByRole('button', { name: 'Change the rule' })).toBeEnabled()
  })

  it('offers to reload or to save as a copy when another save came first', async () => {
    serve({ saveConflict: true })
    const user = userEvent.setup()
    renderAt(`/ta/indicators/${EMA.id}`)

    await user.type(await screen.findByRole('textbox', { name: 'Pine source of EMA' }), '// mine')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Changed elsewhere')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save these changes as a copy' }))

    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/indicators/cccccccccccc'))
    expect(sent.find((request) => request.method === 'POST')?.body).toMatchObject({
      name: 'EMA (copy)',
      source: `${EMA_SOURCE}// mine`,
    })
  })

  it('sets a version beside the text being edited, across the screen', async () => {
    const user = userEvent.setup()
    renderAt(`/ta/indicators/${EMA.id}`)

    await screen.findByRole('textbox', { name: 'Pine source of EMA' })
    await user.click(screen.getByRole('button', { name: 'History' }))
    const dialog = await screen.findByRole('dialog', { name: 'History of EMA' })
    // As wide as the screen allows: two scripts side by side, not 42rem for both and the list.
    expect(within(dialog).getByRole('heading', { name: 'History of EMA' }).closest('.max-w-\\[110rem\\]')).not.toBeNull()
    expect(await within(dialog).findByText('Version 2 is the text in the editor.')).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: /^v1/ }))
    expect(await within(dialog).findByTestId('version-diff')).toBeInTheDocument()
    expect(within(dialog).getByText('Version 1')).toBeInTheDocument()
    expect(within(dialog).getByText('In the editor')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Put version 1 in the editor' })).toBeEnabled()
  })

  it('shows where PineTS found a mistake', async () => {
    const user = userEvent.setup()
    renderAt(`/ta/indicators/${EMA.id}`)

    await user.type(await screen.findByRole('textbox', { name: 'Pine source of EMA' }), 'BROKEN')

    const problems = screen.getByRole('region', { name: 'Problems' })
    expect(await within(problems).findByText('Unexpected token at 4:3', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(within(problems).getByText('4:3')).toBeInTheDocument()
  })

  it('starts a new script from its kind\'s template, and opens it once created', async () => {
    const user = userEvent.setup()
    renderAt('/ta/indicators/new?kind=library')

    const editor = await screen.findByRole('textbox', { name: /Pine source of/ })
    expect((editor as HTMLTextAreaElement).value).toContain('library("MyLibrary")')
    await user.type(editor, '\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('/ta/indicators/cccccccccccc'))
    // No name typed: the one the library declares.
    expect(sent.find((request) => request.method === 'POST')?.body).toMatchObject({ name: 'MyLibrary' })
  })
})

describe('an address that names no script', () => {
  it('says there is no such script, with the way to the scripts', async () => {
    serve()
    renderAt('/ta/indicators/fedcba987654')

    expect(await screen.findByRole('heading', { level: 1, name: 'No such script' })).toBeInTheDocument()
    expect(document.title).toBe('No such script · WATA Admin')
    const suggestions = within(screen.getByRole('navigation', { name: 'Helpful links' }))
    expect(suggestions.getByRole('link', { name: /^Indicators/ })).toHaveAttribute('href', '/ta/indicators')
    expect(suggestions.getByRole('link', { name: /^New script/ })).toHaveAttribute('href', '/ta/indicators/new')
  })
})

describe("the editor's test", () => {
  it('finds a turbo among the assets by its knock-out level, as typed', async () => {
    const instrument = (changes: Record<string, unknown>) => ({
      provider: 'capital', key: 'US100', ref: 'capital/US100', id: null, uic: null, asset_type: 'INDICES', symbol: 'US100',
      description: 'US Tech 100', currency: 'USD', role: 'underlying', indice: 'us100', underlying_uic: null, direction: null,
      barrier: null, issuer: null, first_t: null, last_t: null, last_bid: null, last_ask: null, status: 'recording', ...changes,
    })
    assets = [
      instrument({}),
      instrument({ provider: 'saxo', key: '7', ref: 'saxo/7', id: 7, role: 'turbo', description: 'TURBO LONG NASDAQ-100 KO 24,150 SG', barrier: 24150, direction: 'long' }),
      instrument({ provider: 'saxo', key: '8', ref: 'saxo/8', id: 8, role: 'turbo', description: 'TURBO SHORT NASDAQ-100 KO 26,900 SG', barrier: 26900, direction: 'short' }),
    ]
    const user = userEvent.setup()
    renderAt(`/ta/indicators/${EMA.id}`)

    const field = await screen.findByRole('combobox', { name: 'Asset' })
    await waitFor(() => expect(field).toHaveTextContent('US Tech 100 · Capital.com'))
    await user.click(field)
    // Written without its separator, as one types it.
    await user.keyboard('24150')
    expect(within(screen.getByRole('listbox')).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'TURBO LONG NASDAQ-100 KO 24,150 SG · Saxo',
    ])
    await user.keyboard('{Enter}')

    expect(field).toHaveTextContent('TURBO LONG NASDAQ-100 KO 24,150 SG · Saxo')
  })
})
