import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { AgentContext, AgentConversation, AgentConversationListed, AgentModel, AgentProposal, AgentState, Session } from '@/lib/types'
import { CATALOG, STOPLOSS } from '@/test/configCatalog'
import { WithTrail } from '@/test/WithTrail'

import { AgentActivityPage } from './AgentActivityPage'
import { AgentSheet } from './AgentSheet'
import { ConversationPage } from './ConversationPage'

const SESSION: Session = {
  authenticated: true, version: '0.54.0', timezone: 'Europe/Paris', actions_enabled: true, config_edit_enabled: true,
  backtests_enabled: true, totp_enabled: false, restart_required: false,
}
const RUN = '20261027-101010-abcdef'
const ID = '20261028-080000-a1b2c3'
const NEW_ID = '20261028-090000-d4e5f6'
const RUNNING_ID = '20261028-100000-0a0b0c'

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
const model = (id: string, label: string, reads: string[], changes: Partial<AgentModel> = {}): AgentModel => ({
  id, label, window: 1_000_000, max_output: 128_000, prices: { input: 4, output: 20, cache_read: 0.2 },
  efforts: [...EFFORTS], available: true, reads, ...changes,
})
const STATE: AgentState = {
  enabled: true, model: 'claude-opus-5-5', model_label: 'Claude Opus 5.5', effort: 'high', key: true, pause: null,
  started: true, month: { month: '2026-10', spent_usd: 3.2, budget_usd: 50 }, under_way: [], conversations: 1,
  limits: { tool_calls: 60, backtests: 8, request_usd: 5, running: 2 }, efforts: [...EFFORTS],
  tasks: { usd: 20, backtests: 40, hours: 12 },
  models: [
    model('claude-opus-5-5', 'Claude Opus 5.5', ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5']),
    model('claude-sonnet-5-5', 'Claude Sonnet 5.5', ['claude-sonnet-5-5', 'claude-haiku-5-5'], { prices: { input: 2, output: 10, cache_read: 0.2 } }),
    model('claude-haiku-5-5', 'Claude Haiku 5.5', ['claude-haiku-5-5']),
    model('claude-fable-5-1', 'Claude Fable 5.1', ['claude-fable-5-1', 'claude-opus-5-5'], { available: false }),
  ],
}

const LISTED: AgentConversationListed = {
  id: ID, title: 'Analyse this run', subject: { kind: 'run', id: RUN, label: 'Cross R60' }, status: 'idle',
  model: 'claude-opus-5-5', created_at: '2026-10-28T08:00:00Z', updated_at: '2026-10-28T08:05:00Z', requests: 1,
  cost_usd: 0.42, outcome: 'done', error: null,
}

const CONVERSATION: AgentConversation = {
  id: ID, title: 'Analyse this run', subject: LISTED.subject, status: 'idle', model: 'claude-opus-5-5', effort: 'high',
  created_at: LISTED.created_at, updated_at: LISTED.updated_at, usage: {}, cost_usd: 0.42, error: null, pending: null,
  context: { tokens: 84_213, model: 'claude-opus-5-5', at: '2026-10-28T08:05:00Z' },
  requests: [{
    model: 'claude-opus-5-5', effort: 'high',
    index: 0, prompt: 'Can this run be believed?', shared: LISTED.subject, asked_at: '2026-10-28T08:00:00Z',
    started_at: '2026-10-28T08:00:01Z', ended_at: '2026-10-28T08:05:00Z', status: 'done', detail: null,
    steps: [
      { kind: 'note', id: 'n0', at: '2026-10-28T08:00:02Z', text: 'Reading the run first.' },
      { kind: 'tool', id: 't1', tool: 'backtest_result', input: { run_id: RUN }, started_at: '2026-10-28T08:00:03Z',
        ended_at: '2026-10-28T08:00:04Z', state: 'done', output: '{"trades":42}' },
    ],
    answer: { text: 'It **holds** on [21 days](/backtests/20261027-101010-abcdef).', model: 'claude-opus-5-5', stop_reason: 'end_turn' },
    proposals: ['p0a1b2c3d'], runs: [], cost_usd: 0.42, feedback: null,
  }],
  proposals: [{
    id: 'p0a1b2c3d', kind: 'script', request: 0, created_at: '2026-10-28T08:04:00Z', status: 'proposed',
    title: 'A slower fast line', rationale: 'Run 20261028-0801 beat it on the same days.',
    script: { id: '8c2df05570d5', name: 'EMA cross', base_version: 1 },
    draft: { id: 'aaaabbbbcccc', name: 'EMA cross by Claude', version: 1 }, lines: { added: 1, removed: 1 },
  }],
}

// A conversation that proposed a change of settings and a new script.
const PROPOSING_ID = '20261028-110000-0c0d0e'
const SETTINGS: AgentProposal = {
  id: 'p1a2b3c4d', kind: 'settings', request: 0, created_at: '2026-10-28T11:04:00Z', status: 'proposed',
  title: 'A tighter stop', rationale: 'Held on 22 days.',
  settings: [{ setting: 'stoploss_percent', label: 'Stop-loss', unit: '%', group: 'exits', path: STOPLOSS, before: -7, after: -5 }],
  runs: [{ id: RUN, name: 'Stop at -5', headline: { total_percent: 12.5, trades: 41, win_rate: 55, worst_drawdown: -4, sample: 'fair',
    beaten: 97, days_tested: 22, profit_loss: 125, cells: null } }],
  same: ['trading_start_hour'],
}
const NEW_SCRIPT: AgentProposal = {
  id: 'p5e6f7a8b', kind: 'new_script', request: 0, created_at: '2026-10-28T11:05:00Z', status: 'proposed',
  title: 'A slower cross', rationale: 'Beat the cross on 22 days.',
  draft: { id: 'aaaabbbbcccc', name: 'Claude · cross', version: 2, kind: 'indicator' },
  new_script: { name: 'Cross, slow', description: 'The cross with a slower fast line.' }, lines: { added: 2, removed: 0 },
}
const PROPOSING: AgentConversation = {
  ...CONVERSATION, id: PROPOSING_ID, proposals: [SETTINGS, NEW_SCRIPT],
  requests: [{ ...CONVERSATION.requests[0], proposals: [SETTINGS.id, NEW_SCRIPT.id] }],
}

// A research task the agent ended, its conclusion said.
const TASK_ID = '20261028-120000-1a2b3c'
const TASK_CONVERSATION: AgentConversation = {
  ...CONVERSATION, id: TASK_ID, title: 'Find whether a slower line holds', subject: null, proposals: [],
  task: {
    goal: 'Find whether a slower line holds', budget: { usd: 5, backtests: 20, hours: 4 }, state: 'working',
    started_at: '2026-10-28T12:00:00Z', deadline: '2026-10-28T16:00:00Z', spent_usd: 1.25, backtests: 6, requests: 2,
    ended_at: null, ended_because: null, conclusion: null,
  },
  requests: [
    { ...CONVERSATION.requests[0], proposals: [], by: '10.8.0.2' },
    { ...CONVERSATION.requests[0], index: 1, proposals: [], by: 'task',
      prompt: 'The research task goes on. Spent so far: $0.80 of $5.00, 4 of 20 backtests.' },
  ],
}

const CONTEXT: AgentContext = {
  model: 'claude-opus-5-5', label: 'Claude Opus 5.5', window: 1_000_000, answer_room: 64_000, how: 'counted',
  total: 84_213, cleared: 12_400, clearing: { from_tokens: 150_000, keep_tool_uses: 8 }, unread: [],
  parts: [
    { id: 'system', tokens: 6_104 }, { id: 'tools', tokens: 18_410, count: 31 }, { id: 'asked', tokens: 1_920, estimated: true },
    { id: 'thinking', tokens: 21_500 }, { id: 'calls', tokens: 2_210, estimated: true },
    { id: 'results', tokens: 31_700, estimated: true }, { id: 'answers', tokens: 2_369, estimated: true },
  ],
  last: { at: '2026-10-28T08:05:00Z', model: 'claude-opus-5-5', usd: 0.12, prompt: 84_213, uncached: 103, cache_read: 78_010,
    cache_write: 6_100, output: 1_912, thinking: 1_400, cleared_tokens: null, cleared_tool_uses: null, dropped_notes: null },
}

let sent: { method: string; path: string; body: unknown }[] = []
let state: AgentState = STATE

function serve() {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    const url = new URL(path, 'http://admin.test')
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
    sent.push({ method, path: `${url.pathname}${url.search}`, body })
    const route = url.pathname
    if (route === '/api/agent') return json(state)
    if (route === '/api/agent/pause' || route === '/api/agent/resume') return json({ pause: route.endsWith('pause') ? { since: '2026-10-28T09:00:00Z', by: '10.8.0.2' } : null })
    if (route === '/api/agent/conversations' && method === 'GET') return json({ items: [LISTED] })
    if (route === '/api/agent/conversations' && method === 'POST') return json({ ...LISTED, id: NEW_ID, status: 'queued' }, 201)
    if (route === `/api/agent/conversations/${ID}`) return json(CONVERSATION)
    if (route === `/api/agent/conversations/${NEW_ID}`) {
      return json({ ...CONVERSATION, id: NEW_ID, status: 'queued', proposals: [],
        requests: [{ ...CONVERSATION.requests[0], status: 'queued', steps: [], answer: null, proposals: [] }] })
    }
    if (route === `/api/agent/conversations/${RUNNING_ID}`) {
      return json({ ...CONVERSATION, id: RUNNING_ID, status: 'running', proposals: [],
        requests: [{ ...CONVERSATION.requests[0], status: 'running', ended_at: null, answer: null, proposals: [] }] })
    }
    if (route === `/api/agent/conversations/${RUNNING_ID}/stop`) return json({ ...LISTED, id: RUNNING_ID, status: 'running' })
    if (route.endsWith('/proposals/p0a1b2c3d/apply')) {
      return json({ ...CONVERSATION.proposals[0], status: 'applied', result: { script_id: '8c2df05570d5', version: 2 } })
    }
    if (route === `/api/agent/conversations/${PROPOSING_ID}`) return json(PROPOSING)
    if (route.endsWith(`/proposals/${SETTINGS.id}/preview`)) {
      return json({ revision: 'r1', changes: [{ path: STOPLOSS, before: -7, after: -5, added: false, removed: false }],
        impact: { services: ['position_monitor'], consumers: [], risk: 'trading', unknown: [] }, warnings: [] })
    }
    if (route.endsWith(`/proposals/${SETTINGS.id}/apply`)) {
      return json({ ...SETTINGS, status: 'applied', settled_at: '2026-10-28T11:10:00Z',
        result: { revision: 'r2', backup: 'config.json.20261028-111000', services: ['position_monitor'] },
        impact: { services: ['position_monitor'], consumers: [], risk: 'trading', unknown: [] }, restart_required: true })
    }
    if (route.endsWith(`/proposals/${NEW_SCRIPT.id}/apply`)) {
      return json({ ...NEW_SCRIPT, status: 'applied', result: { script_id: 'ffff00001111', version: 1, name: 'Cross, slow' } })
    }
    if (route === '/api/config/catalog') return json(CATALOG)
    if (route === '/api/agent/tasks') return json({ ...LISTED, id: TASK_ID, status: 'queued', task: TASK_CONVERSATION.task }, 201)
    if (route === `/api/agent/conversations/${TASK_ID}`) return json(TASK_CONVERSATION)
    if (route === `/api/agent/conversations/${TASK_ID}/task/stop`) return json({ ...LISTED, id: TASK_ID })
    if (route.startsWith('/api/ta/scripts/')) return json({ number: 1, saved_at: 'x', size: 10, sha256: 'x', source: '//@version=6\nindicator("x")\n' })
    if (route === '/api/agent/artefacts') return json({ items: [] })
    if (route === `/api/agent/conversations/${ID}/context`) return json(CONTEXT)
    if (route === `/api/agent/conversations/${ID}/requests`) return json({ ...LISTED, status: 'queued' })
    return json({ detail: 'No such conversation' }, 404)
  }))
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
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
      { path: '/agent', element: page(<AgentActivityPage />) },
      { path: '/agent/:conversationId', element: page(<ConversationPage />) },
      { path: '/backtests/:runId', element: page(<AgentSheet subject={{ kind: 'run', id: RUN, label: 'Cross R60' }} />) },
      // A list's page: its rows ask about themselves through the address.
      { path: '/positions', element: page(<AgentSheet />) },
    ],
    { initialEntries: [address] },
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
}

const address = () => screen.getByRole('status', { name: 'address' }).textContent

describe('the AI agent', () => {
  beforeEach(() => {
    state = STATE
    serve()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('opens beside a run from its address, offers requests to start from, and follows the conversation it starts', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN}?agent=new`)

    expect(await screen.findByText('AI agent')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Analyse it' }))
    const field = screen.getByRole('textbox', { name: 'Ask the AI agent' })
    expect((field as HTMLTextAreaElement).value).toMatch(/^Can this run be believed\?/)
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(address()).toBe(`/backtests/${RUN}?agent=${NEW_ID}`))
    const started = sent.find((request) => request.method === 'POST' && request.path === '/api/agent/conversations')
    expect(started?.body).toMatchObject({ subject: { kind: 'run', id: RUN } })
    expect(await screen.findByText('Can this run be believed?')).toBeInTheDocument()
  })

  it('shows what the agent did, its answer and its proposal, which a click applies', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${ID}`)

    expect(await screen.findByText('Reading the run first.')).toBeInTheDocument()
    expect(screen.getByText(`Read run ${RUN}`)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '21 days' })).toHaveAttribute('href', `/backtests/${RUN}`)
    const proposal = screen.getByRole('region', { name: 'Proposal: A slower fast line' })
    await user.click(within(proposal).getByRole('button', { name: 'Show the change' }))
    expect(await within(proposal).findByTestId('version-diff')).toBeInTheDocument()
    await user.click(within(proposal).getByRole('button', { name: 'Save as version 2' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${ID}/proposals/p0a1b2c3d/apply`)).toBe(true))
  })

  it('previews a change of settings as a save of config.json, saves it with a slide, and says where it applies', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${PROPOSING_ID}`)

    const proposal = await screen.findByRole('region', { name: 'Proposal: A tighter stop' })
    const listed = within(proposal).getByRole('list', { name: 'In config.json' })
    expect(within(listed).getByRole('listitem')).toHaveTextContent(/^Stop-loss.*-7 %.*-5 %$/)
    expect(within(proposal).getByRole('link', { name: 'Stop at -5' })).toHaveAttribute('href', `/backtests/${RUN}`)
    expect(proposal).toHaveTextContent('+12.50 % · 41 trades · 22 days · beat 97 % of random runs')
    expect(proposal).toHaveTextContent('Left out, already at the value proposed: trading_start_hour.')

    await user.click(within(proposal).getByRole('button', { name: 'Preview and save' }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByText(/Applies at the next start of:/)
    expect(dialog).toHaveTextContent('Applies at the next start of: Position monitor.')
    within(dialog).getByRole('slider').focus()
    fireEvent.keyDown(within(dialog).getByRole('slider'), { key: 'Enter' })

    await waitFor(() => expect(sent.find((request) => request.path.endsWith(`/proposals/${SETTINGS.id}/apply`))?.body)
      .toEqual({ revision: 'r1' }))
  })

  it('shows a new script it proposes and creates it as the operator\'s', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${PROPOSING_ID}`)

    const proposal = await screen.findByRole('region', { name: 'Proposal: A slower cross' })
    expect(proposal).toHaveTextContent('Cross, slow: The cross with a slower fast line.')
    await user.click(within(proposal).getByRole('button', { name: 'Show the script' }))
    expect(await within(proposal).findByText(/indicator\("x"\)/)).toBeInTheDocument()
    await user.click(within(proposal).getByRole('button', { name: 'Create the script' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${PROPOSING_ID}/proposals/${NEW_SCRIPT.id}/apply`)).toBe(true))
  })

  it('stops a request under way from its conversation', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${RUNNING_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Stop' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${RUNNING_ID}/stop`)).toBe(true))
  })

  it("asks with the model and effort chosen, and says which model cannot read the conversation's notes", async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${ID}`)

    await user.click(await screen.findByRole('button', { name: 'Model: Claude Opus 5.5' }))
    const sonnet = await screen.findByRole('menuitemradio', { name: /Claude Sonnet 5\.5/ })
    expect(sonnet).toHaveTextContent("Reads none of Claude Opus 5.5's notes here")
    expect(screen.getByRole('menuitemradio', { name: /Claude Fable 5\.1/ })).toHaveAttribute('aria-disabled', 'true')
    await user.click(sonnet)
    expect(await screen.findByText(/Without the notes of Opus 5\.5/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Effort: high' }))
    await user.click(await screen.findByRole('menuitemradio', { name: /^low/ }))
    await user.type(screen.getByRole('textbox', { name: 'Ask the agent again' }), 'Go on')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${ID}/requests`)?.body)
      .toEqual({ prompt: 'Go on', model: 'claude-sonnet-5-5', effort: 'low' }))
  })

  it('shows the context window the conversation fills, counted by the API part by part', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${ID}`)

    const button = await screen.findByRole('button', { name: 'Context window: 84,213 of 1,000,000 tokens' })
    expect(button).toHaveTextContent('84.2k · 8%')
    expect(sent.some((request) => request.path.endsWith('/context'))).toBe(false)
    await user.click(button)

    const window = await screen.findByRole('dialog', { name: 'Context window' })
    expect(await within(window).findByText(/of 1,000,000 tokens/)).toBeInTheDocument()
    expect(within(window).getByRole('meter', { name: 'The context window of Claude Opus 5.5, used' })).toHaveAttribute('aria-valuenow', '8')
    const parts = within(window).getByRole('list', { name: 'What the context window holds' })
    expect(within(parts).getAllByRole('listitem')[0]).toHaveTextContent('Tool results')
    expect(parts).toHaveTextContent('Tool definitions (31)')
    expect(window).toHaveTextContent('Context editing leaves out 12,400 tokens of old tool results')
    expect(window).toHaveTextContent('78,010 read from the cache')
    expect(sent.some((request) => request.path === `/api/agent/conversations/${ID}/context?model=claude-opus-5-5`)).toBe(true)
  })

  it("opens beside a list on the row it is asked about, and starts a conversation about it", async () => {
    const user = userEvent.setup()
    renderAt('/positions?agent=new&about=position:5001')

    expect(await screen.findByText(/Position 5001: it reads it/)).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Why did it end so?' }))
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/agent/conversations')?.body)
      .toMatchObject({ subject: { kind: 'position', id: '5001' } }))
    // The conversation has its own subject: the address no longer carries one.
    await waitFor(() => expect(address()).toBe(`/positions?agent=${NEW_ID}`))
  })

  it('asks nothing without a key, and says why', async () => {
    state = { ...STATE, key: false }
    renderAt(`/backtests/${RUN}?agent=new`)

    const field = await screen.findByRole('textbox', { name: 'Ask the AI agent' })
    await waitFor(() => expect(field).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it("shows the month's spending and its conversations, pauses it, and hides a conversation removed until its Undo", async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    expect(await screen.findByText('$3.20')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'Analyse this run' })).toHaveAttribute('href', `/agent/${ID}`)
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/agent/pause')).toBe(true))

    await user.click(screen.getByRole('button', { name: 'Remove Analyse this run' }))
    expect(screen.queryByRole('link', { name: 'Analyse this run' })).toBeNull()
    await user.click(await screen.findByRole('button', { name: /Undo/ }))
    expect(await screen.findByRole('link', { name: 'Analyse this run' })).toBeInTheDocument()
    expect(sent.some((request) => request.method === 'DELETE')).toBe(false)
  })

  it('starts a research task with its goal and budget, and follows it from its conversation', async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    const form = await screen.findByRole('button', { name: 'Start the task' })
    expect(form).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Its goal' }), 'Find whether a slower line holds')
    await user.click(screen.getByRole('button', { name: 'Start the task' }))

    await waitFor(() => expect(address()).toBe(`/agent/${TASK_ID}`))
    expect(sent.find((request) => request.path === '/api/agent/tasks')?.body).toEqual({
      goal: 'Find whether a slower line holds', budget: { usd: 5, backtests: 20, hours: 4 },
      model: 'claude-opus-5-5', effort: 'high',
    })
    expect(await screen.findByText('$1.25 of $5.00')).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Backtests: 6 of 20' })).toBeInTheDocument()
    // Its next request was the admin's, said in a line.
    expect(screen.getByText(/^The research task goes on\. Spent so far: \$0\.80/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop the task' }))
    await waitFor(() => expect(sent.some((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${TASK_ID}/task/stop`)).toBe(true))
  })

  it('says how to switch it on while it is off', async () => {
    state = { enabled: false, model: 'claude-opus-5-5' }
    renderAt('/agent')

    expect(await screen.findByText(/The AI agent is off/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/config/agent')
  })

  it('answers an address of no conversation with a 404', async () => {
    renderAt('/agent/20261028-000000-ffffff')

    expect(await screen.findByRole('heading', { name: 'No such conversation' })).toBeInTheDocument()
  })
})
