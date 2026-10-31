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
import { AgentVerdict } from './AgentVerdict'
import { ConversationPage } from './ConversationPage'
import { RoutinesPage } from './RoutinesPage'

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

// A conversation whose answer drew a chart and set a verdict on its run.
const CHARTED_ID = '20261028-130000-2b3c4d'
const CHARTED: AgentConversation = {
  ...CONVERSATION, id: CHARTED_ID, proposals: [],
  requests: [{
    ...CONVERSATION.requests[0], proposals: [],
    charts: [{ id: 't9', kind: 'bar', title: 'Each day of the run', unit: '%', tone: 'sign', references: [],
      series: [{ key: 'day', label: 'Day' }], categories: [{ label: '13 Oct', day: -0.5 }, { label: '14 Oct', day: 1.5 }],
      source: 'backtest_days of run 20261027-101010-abcdef' }],
    verdicts: [{ about: `run:${RUN}`, tone: 'positive', line: 'It holds on 21 days.' }],
  }],
}

// A conversation whose triage proposed a pause of new entries, fresh, and a restart proposed too long ago.
const ACTING_ID = '20261028-140000-3c4d5e'
const PAUSE: AgentProposal = {
  id: 'p9a8b7c6d', kind: 'action', request: 0, created_at: '2026-10-28T14:00:00Z', status: 'proposed',
  title: 'Pause new entries', rationale: 'Saxo refuses every sale since 13:52.', action: { kind: 'pause_entries' },
  fresh_until: '2099-01-01T00:00:00Z',
}
const STALE_RESTART: AgentProposal = {
  id: 'p1b2c3d4e', kind: 'action', request: 0, created_at: '2026-10-28T14:00:00Z', status: 'proposed',
  title: 'Restart the position monitor', rationale: 'It stopped beating.', fresh_until: '2026-10-28T14:30:00Z',
  action: { kind: 'restart', services: [{ id: 'position_monitor', name: 'Position monitor' }] },
}
const ACTING: AgentConversation = {
  ...CONVERSATION, id: ACTING_ID, proposals: [PAUSE, STALE_RESTART],
  requests: [{ ...CONVERSATION.requests[0], proposals: [PAUSE.id, STALE_RESTART.id] }],
}

// A review of a script's unsaved draft, whose fix goes into the editor; and a new rule proposed.
const DRAFT_ID = '20261028-150000-4d5e6f'
const NEW_RULE: AgentProposal = {
  id: 'p2c3d4e5f', kind: 'new_rule', request: 0, created_at: '2026-10-28T15:04:00Z', status: 'proposed',
  title: 'A slower cross, in shadow', rationale: 'Beat the cross on 22 days.',
  new_rule: {
    fields: { name: 'Cross, slow', asset: { provider: 'capital', key: 'US100' }, side: 'bid', bars: { type: 'time', timeframe: '5' },
      inputs: [{ id: 'in_0', var: null, title: null, value: 14 }], alerts: [{ title: 'Enter Long', action: 'long' }],
      alert_calls: 'ignore', indice: 'us100' },
    script: { id: '8c2df05570d5', name: 'EMA cross', version: 1 }, mode: 'shadow',
  },
}
const DRAFTED: AgentConversation = {
  ...CONVERSATION, id: DRAFT_ID, subject: { kind: 'draft', id: '0a1b2c3d', label: 'a draft of EMA cross' },
  proposals: [CONVERSATION.proposals[0], NEW_RULE],
  requests: [{ ...CONVERSATION.requests[0], proposals: [CONVERSATION.proposals[0].id, NEW_RULE.id] }],
}

// A research task waiting for the operator's answer to its question; and a routine.
const ASKING_ID = '20261028-160000-5e6f7a'
const ASKING: AgentConversation = {
  ...CONVERSATION, id: ASKING_ID, title: 'Find a line that holds', subject: null, proposals: [],
  task: {
    goal: 'Find a line that holds', budget: { usd: 5, backtests: 20, hours: 4 }, state: 'working',
    started_at: '2026-10-28T16:00:00Z', deadline: '2026-10-28T20:00:00Z', spent_usd: 0.4, backtests: 0, requests: 1,
    ended_at: null, ended_because: null, conclusion: null,
    question: { question: 'Fast lines or slow ones first?', why: 'Each takes half the budget.', choices: ['Fast', 'Slow'],
      asked_at: '2026-10-28T16:05:00Z', request: 0 },
  },
  requests: [{ ...CONVERSATION.requests[0], proposals: [] }],
}
const ROUTINE = {
  id: 'r0a1b2c3d', name: 'Weekly edge check', goal: 'Run the references again with the week.',
  schedule: { every: 'week' as const, weekday: 5, time: '10:00' }, budget: { usd: 3, backtests: 6, hours: 2 },
  model: null, effort: null, telegram: true, enabled: true, created_at: '2026-10-28T10:00:00Z', created_by: '10.8.0.2',
  runs: [], next: '2026-10-31T09:00:00Z',
}

// A routine ready to add (src/admin/agent_routines.py's TEMPLATES).
const SCOUT = {
  key: 'luxalgo_scout', name: 'LuxAlgo scout', goal: "Read LuxAlgo's newest indicators with luxalgo_known.",
  schedule: { every: 'week' as const, weekday: 5, time: '10:00' }, budget: { usd: 2, backtests: 2, hours: 1.5 }, telegram: true,
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

// What the attach picker lists of the scripts, and a file the server staged.
const SCRIPT_LISTED = { id: '0d4fd1117e2f', name: 'Kernel VWAP', kind: 'indicator', version: 10 }
const STAGED = { kind: 'file' as const, id: 'f0123456789', name: 'chart.png', type: 'image' as const, media_type: 'image/png',
  size: 2048, masked: 0 }
// A conversation whose request shared a run and a screenshot.
const ATTACHED_ID = '20261030-090000-f1f2f3'
const ATTACHED: AgentConversation = {
  ...CONVERSATION, id: ATTACHED_ID, proposals: [],
  requests: [{ ...CONVERSATION.requests[0], proposals: [], attached: [LISTED.subject!, STAGED] }],
}

let sent: { method: string; path: string; body: unknown }[] = []
let state: AgentState = STATE
// Whether the dashboard's pause is refused, as the server refuses an action whose situation changed.
let refusePause = false

function serve() {
  sent = []
  refusePause = false
  const json = (body: unknown, status = 200) =>
    new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    const url = new URL(path, 'http://admin.test')
    // A file staged is sent as a form: what is kept of it here is its name.
    const body = typeof init.body === 'string' ? JSON.parse(init.body)
      : init.body instanceof FormData ? { file: (init.body.get('file') as File | null)?.name } : undefined
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
    if (route === `/api/agent/conversations/${CHARTED_ID}`) return json(CHARTED)
    if (route === `/api/agent/conversations/${ACTING_ID}`) return json(ACTING)
    if (route === `/api/agent/conversations/${DRAFT_ID}`) return json(DRAFTED)
    if (route === `/api/agent/conversations/${ASKING_ID}`) return json(ASKING)
    if (route === `/api/agent/conversations/${ASKING_ID}/task/answer`) return json({ ...LISTED, id: ASKING_ID, status: 'queued' })
    if (route === '/api/agent/routines/templates') return json({ templates: [SCOUT] })
    if (route === '/api/agent/routines') return json({ revision: 'rv1', max: 10, routines: [ROUTINE] })
    if (route === `/api/agent/routines/${ROUTINE.id}/run`) return json({ conversation: ASKING_ID }, 201)
    if (route === `/api/agent/conversations/${DRAFT_ID}/proposals/${CONVERSATION.proposals[0].id}/apply`) {
      return json({ ...CONVERSATION.proposals[0], status: 'applied', result: { into_editor: true, script_id: '8c2df05570d5' } })
    }
    if (route === `/api/agent/conversations/${DRAFT_ID}/proposals/${NEW_RULE.id}/apply`) {
      return json({ ...NEW_RULE, status: 'applied', result: { rule_id: 'a1b2c3d4e5f6', name: 'Cross, slow', mode: 'shadow' } })
    }
    if (route === '/api/actions/pause-entries') {
      return refusePause ? json({ detail: 'New entries are paused already' }, 409) : json({ entries_paused_since: '2026-10-28T14:05:00Z' })
    }
    if (route === '/api/agent/verdicts') {
      return json({ items: { [`run:${RUN}`]: { about: `run:${RUN}`, tone: 'positive', line: 'It holds on 21 days.',
        at: '2026-10-28T08:05:00Z', conversation: CHARTED_ID, request: 0 } } })
    }
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
    if (route === '/api/ta/scripts') return json({ items: [SCRIPT_LISTED] })
    if (route === '/api/agent/files' && method === 'POST') return json(STAGED, 201)
    if (route === `/api/agent/files/${STAGED.id}` && method === 'DELETE') return json(null, 204)
    if (route === `/api/agent/conversations/${ATTACHED_ID}`) return json(ATTACHED)
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
      { path: '/agent/routines', element: page(<RoutinesPage />) },
      { path: '/agent/:conversationId', element: page(<ConversationPage />) },
      { path: '/backtests/:runId', element: page(<AgentSheet subject={{ kind: 'run', id: RUN, label: 'Cross R60' }} />) },
      // A list's page: its rows ask about themselves through the address.
      { path: '/positions', element: page(<AgentSheet />) },
      // A page that shows what the agent concluded on its run, the run having changed since or not.
      { path: '/verdict', element: page(<AgentVerdict about={`run:${RUN}`} />) },
      { path: '/verdict-before', element: page(<AgentVerdict about={`run:${RUN}`} changedAt="2026-10-29T08:00:00Z" />) },
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

    // Its note is its words between its steps, not a step.
    expect(await screen.findByRole('group', { name: 'Its notes' })).toHaveTextContent('Reading the run first.')
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

  it('says what a request under way is doing where its answer will be, without its tools or a copy yet', async () => {
    renderAt(`/agent/${RUNNING_ID}`)

    const working = await screen.findByText('Thinking')
    expect(working.closest('[role="status"]')).toHaveTextContent(/^Thinking$/)
    // Its latest thought, said again where the answer will be, beside its note.
    expect(screen.getAllByText('Reading the run first.')).toHaveLength(2)
    expect(screen.queryByRole('status', { name: 'Generating response' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Used 1 tool/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Copy response' })).toBeNull()
  })

  it('folds what an earlier request did to its line, and opens it at a click', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${TASK_ID}`)

    const folds = await screen.findAllByRole('button', { name: /What it did/ })
    expect(folds.map((fold) => fold.getAttribute('aria-expanded'))).toEqual(['false', 'true'])
    expect(folds[0]).toHaveTextContent('1 tool call · 1 note · 5 min')
    expect(screen.getAllByText(`Read run ${RUN}`)).toHaveLength(1)

    await user.click(folds[0])
    expect(folds[0]).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getAllByText(`Read run ${RUN}`)).toHaveLength(2)
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
      .toEqual({ prompt: 'Go on', model: 'claude-sonnet-5-5', effort: 'low', tell: false }))
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
      model: 'claude-opus-5-5', effort: 'high', tell: false,
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

  it('says a request on Telegram once answered when asked, and cannot without a Telegram bot', async () => {
    const user = userEvent.setup()
    state = { ...STATE, telegram: true }
    renderAt(`/agent/${ID}`)

    const toggle = await screen.findByRole('button', { name: 'Tell me on Telegram' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-pressed', 'true')
    await user.type(screen.getByRole('textbox', { name: 'Ask the agent again' }), 'Go on')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(sent.find((request) => request.method === 'POST'
      && request.path === `/api/agent/conversations/${ID}/requests`)?.body).toMatchObject({ prompt: 'Go on', tell: true }))
  })

  it('cannot be asked to tell on Telegram while no bot is set', async () => {
    state = { ...STATE, telegram: false }
    renderAt(`/agent/${ID}`)

    expect(await screen.findByRole('button', { name: 'Tell me on Telegram' })).toBeDisabled()
  })

  it('draws the charts its answer made, says where their figures come from, and its verdicts', async () => {
    renderAt(`/agent/${CHARTED_ID}`)

    expect((await screen.findAllByText('Each day of the run')).some((element) => element.tagName === 'FIGCAPTION')).toBe(true)
    expect(screen.getByText('From: backtest_days of run 20261027-101010-abcdef')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: `run ${RUN}` })).toHaveAttribute('href', `/backtests/${RUN}`)
    expect(screen.getByText(/It holds on 21 days/)).toBeInTheDocument()
  })

  it("shows its verdict on what a page is about, said to be from before the thing's last change", async () => {
    renderAt('/verdict')
    expect(await screen.findByText('It holds on 21 days.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /28\/10\/2026|28 Oct/ })).toHaveAttribute('href', `/agent/${CHARTED_ID}`)
    expect(sent.some((request) => request.path === `/api/agent/verdicts?about=${encodeURIComponent(`run:${RUN}`)}`)).toBe(true)
  })

  it('says a verdict older than what it is on changed', async () => {
    renderAt('/verdict-before')
    expect(await screen.findByRole('link', { name: /^before its last change/ })).toBeInTheDocument()
  })

  it('applies an action it proposed through the page\'s own confirmation, with the proposal it applies', async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${ACTING_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Pause new entries…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Pause new entries' })
    expect(within(dialog).getByText(/Nothing is closed and nothing is stopped/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Pause new entries' }))

    await waitFor(() => expect(sent.find((request) => request.path === '/api/actions/pause-entries')?.body)
      .toEqual({ proposal: `${ACTING_ID}/${PAUSE.id}` }))
  })

  it('keeps an action open when the page refuses it, saying what the server answered', async () => {
    refusePause = true
    const user = userEvent.setup()
    renderAt(`/agent/${ACTING_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Pause new entries…' }))
    const dialog = await screen.findByRole('dialog', { name: 'Pause new entries' })
    await user.click(within(dialog).getByRole('button', { name: 'Pause new entries' }))
    expect(await within(dialog).findByText(/New entries are paused already/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    const card = screen.getByRole('region', { name: 'Proposal: Pause new entries' })
    expect(within(card).getByText('Refused when you tried it: New entries are paused already')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Pause new entries…' })).toBeInTheDocument()
  })

  it('leads to the page where an action is done once its proposal is too old', async () => {
    renderAt(`/agent/${ACTING_ID}`)

    const restart = await screen.findByRole('region', { name: 'Proposal: Restart the position monitor' })
    expect(within(restart).getByText(/Proposed over 30 minutes ago/)).toBeInTheDocument()
    expect(within(restart).getByRole('link', { name: 'Go to System' })).toHaveAttribute('href', '/system')
    expect(within(restart).queryByRole('button', { name: /Restart the position monitor/ })).toBeNull()
  })

  it("puts a draft's fix in the editor, unsaved, and creates a new rule proposed in shadow", async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${DRAFT_ID}`)

    await user.click(await screen.findByRole('button', { name: 'Put in the editor, unsaved' }))
    await waitFor(() => expect(sent.find((request) => request.path.endsWith(`/proposals/${CONVERSATION.proposals[0].id}/apply`))?.body)
      .toEqual({ into_editor: true }))
    const rule = screen.getByRole('region', { name: 'Proposal: A slower cross, in shadow' })
    expect(rule.textContent).toMatch(/on US100, 5 min bars of its bid, for us100/)
    await user.click(within(rule).getByRole('button', { name: 'Create the rule (in shadow)' }))
    await waitFor(() => expect(sent.some((request) => request.path.endsWith(`/proposals/${NEW_RULE.id}/apply`))).toBe(true))
  })

  it("answers the question a research task waits on, with one of its choices", async () => {
    const user = userEvent.setup()
    renderAt(`/agent/${ASKING_ID}`)

    const question = await screen.findByRole('region', { name: 'Its question' })
    expect(within(question).getByText('Fast lines or slow ones first?')).toBeInTheDocument()
    await user.click(within(question).getByRole('button', { name: 'Slow' }))

    await waitFor(() => expect(sent.find((request) => request.path === `/api/agent/conversations/${ASKING_ID}/task/answer`)?.body)
      .toEqual({ answer: 'Slow' }))
  })

  it('adds a routine from a template, its form filled in to change before saving', async () => {
    const user = userEvent.setup()
    renderAt('/agent/routines')

    await user.click(await screen.findByRole('button', { name: 'From a template' }))
    await user.click(await screen.findByRole('menuitem', { name: /LuxAlgo scout/ }))
    const dialog = await screen.findByRole('dialog', { name: 'Add the LuxAlgo scout' })
    expect(within(dialog).getByRole('textbox', { name: 'Its name' })).toHaveValue('LuxAlgo scout')
    expect(within(dialog).getByRole('textbox', { name: 'Its goal' })).toHaveValue(SCOUT.goal)
    await user.click(within(dialog).getByRole('button', { name: 'Make the routine' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/agent/routines')?.body)
      .toMatchObject({ name: 'LuxAlgo scout', schedule: SCOUT.schedule, budget: SCOUT.budget, telegram: true, enabled: true, revision: 'rv1' }))
  })

  it('lists the routines with when each comes back, and runs one now', async () => {
    const user = userEvent.setup()
    renderAt('/agent/routines')

    expect(await screen.findByText('Weekly edge check')).toBeInTheDocument()
    expect(screen.getByText('Each Saturday at 10:00')).toBeInTheDocument()
    expect(screen.getByText(/would spend \$13\.05 a month at most/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run Weekly edge check now' }))

    await waitFor(() => expect(address()).toBe(`/agent/${ASKING_ID}`))
  })
})

describe('what is attached to a request', () => {
  beforeEach(() => {
    state = STATE
    serve()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('attaches a script from the paperclip and a file of the computer to a new conversation', async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    const [attach] = await screen.findAllByRole('button', { name: 'Attach' })
    await user.click(attach)
    const picker = await screen.findByRole('dialog', { name: 'Attach to the conversation' })
    await user.type(within(picker).getByRole('combobox', { name: 'Search' }), 'Kernel')
    await user.click(await within(picker).findByRole('option', { name: /Kernel VWAP/ }))
    const chooser = document.querySelector<HTMLInputElement>('input[type="file"]')!
    fireEvent.change(chooser, { target: { files: [new File(['png'], 'chart.png', { type: 'image/png' })] } })

    const attached = screen.getAllByRole('group', { name: 'Attached' })[0]
    expect(within(attached).getByText('Script Kernel VWAP v10')).toBeInTheDocument()
    expect(await within(attached).findByText('2 kB')).toBeInTheDocument()
    expect(sent.find((request) => request.path === '/api/agent/files')?.body).toEqual({ file: 'chart.png' })
    await user.type(screen.getByRole('textbox', { name: 'Ask the AI agent' }), 'What do they show?')
    await user.click(screen.getAllByRole('button', { name: 'Send' })[0])

    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/agent/conversations')?.body)
      .toMatchObject({ prompt: 'What do they show?', attach: [{ kind: 'script', id: '0d4fd1117e2f', version: 10 }, { kind: 'file', id: STAGED.id }] }))
  })

  it('takes a file dropped on the field, and removes it from the server when it is taken away', async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    const field = await screen.findByRole('textbox', { name: 'Ask the AI agent' })
    const dropped = [new File(['png'], 'chart.png', { type: 'image/png' })]
    fireEvent.drop(field.parentElement!, { dataTransfer: { types: ['Files'], files: dropped } })
    const attached = screen.getAllByRole('group', { name: 'Attached' })[0]
    expect(await within(attached).findByText('2 kB')).toBeInTheDocument()
    await user.click(within(attached).getByRole('button', { name: 'Remove chart.png' }))

    expect(screen.queryByRole('button', { name: 'Remove chart.png' })).toBeNull()
    expect(screen.queryByRole('group', { name: 'Attached' })).toBeNull()
    await waitFor(() => expect(sent.some((request) => request.method === 'DELETE' && request.path === `/api/agent/files/${STAGED.id}`)).toBe(true))
  })

  it('refuses a file it cannot take before uploading it, and holds the request until it is removed', async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    const field = await screen.findByRole('textbox', { name: 'Ask the AI agent' })
    fireEvent.drop(field.parentElement!, { dataTransfer: { types: ['Files'], files: [new File(['x'], 'tool.exe')] } })
    await user.type(field, 'And this?')

    expect(screen.getAllByText(/^Attach an image, a PDF/)[0]).toBeInTheDocument()
    expect(screen.getByText('tool.exe could not be taken: remove it to send')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Send' })[0]).toBeDisabled()
    expect(sent.some((request) => request.path === '/api/agent/files')).toBe(false)
  })

  it('keeps the page\'s subject first, which cannot be taken away', async () => {
    const user = userEvent.setup()
    renderAt(`/backtests/${RUN}?agent=new`)

    const attached = await screen.findByRole('group', { name: 'Attached' })
    expect(within(attached).getByText('Run Cross R60')).toBeInTheDocument()
    // Its controls once the agent's state is read: a request can be sent then.
    await screen.findByRole('button', { name: 'Attach' })
    expect(within(attached).queryByRole('button', { name: /^Remove/ })).toBeNull()
    await user.type(screen.getByRole('textbox', { name: 'Ask the AI agent' }), 'Can it be believed?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(sent.map((request) => `${request.method} ${request.path}`)).toContain('POST /api/agent/conversations'))
    expect(sent.find((request) => request.method === 'POST' && request.path === '/api/agent/conversations')?.body)
      .toEqual(expect.objectContaining({ subject: { kind: 'run', id: RUN } }))
    expect(sent.find((request) => request.path === '/api/agent/conversations' && request.method === 'POST')?.body)
      .not.toHaveProperty('attach')
  })

  it('shows what a request shared, a thing leading to its page and a file to itself', async () => {
    renderAt(`/agent/${ATTACHED_ID}`)

    const shared = await screen.findByRole('group', { name: 'Shared with the request' })
    expect(within(shared).getByRole('link', { name: 'Run Cross R60' })).toHaveAttribute('href', `/backtests/${RUN}`)
    const file = within(shared).getByRole('link', { name: 'chart.png' })
    expect(file).toHaveAttribute('href', `/api/agent/conversations/${ATTACHED_ID}/files/${STAGED.id}`)
    expect(file).toHaveAttribute('target', '_blank')
    expect(shared.querySelector('img')).toHaveAttribute('src', `/api/agent/conversations/${ATTACHED_ID}/files/${STAGED.id}`)
  })

  it('gives a research task what is attached to it', async () => {
    const user = userEvent.setup()
    renderAt('/agent')

    const attach = (await screen.findAllByRole('button', { name: 'Attach' }))[1]
    await user.click(attach)
    const picker = await screen.findByRole('dialog', { name: 'Attach to the conversation' })
    await user.click(await within(picker).findByRole('option', { name: /Kernel VWAP/ }))
    expect(within(screen.getByRole('group', { name: 'Given to the task' })).getByText('Script Kernel VWAP v10')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Its goal' }), 'Find a slower line that holds.')
    await user.click(screen.getByRole('button', { name: 'Start the task' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST' && request.path === '/api/agent/tasks')?.body)
      .toMatchObject({ goal: 'Find a slower line that holds.', attach: [{ kind: 'script', id: '0d4fd1117e2f', version: 10 }] }))
  })
})
