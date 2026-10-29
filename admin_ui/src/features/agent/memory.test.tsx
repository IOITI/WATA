import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { AgentMemoryChange, AgentMemoryEntry, AgentMemoryState, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { MemoryPage } from './MemoryPage'

const SESSION: Session = {
  authenticated: true, version: '0.60.0', timezone: 'Europe/Paris', actions_enabled: true, config_edit_enabled: true,
  backtests_enabled: true, totp_enabled: false, restart_required: false,
}
const CONVERSATION = '20261029-080000-a1b2c3'
const LESSON: AgentMemoryEntry = {
  name: 'range-ticks', kind: 'lesson', description: 'R60 is 6 points on capital/US100, 0.06 on dukascopy/us100',
  body: "Dukascopy's files hold **thousandths**: 6 points there are R6000.", locked: false, revision: 2,
  created_at: '2026-10-28T08:00:00Z', created_by: 'agent', created_in: CONVERSATION, updated_at: '2026-10-29T07:00:00Z',
  updated_by: 'agent', updated_in: CONVERSATION, read_at: '2026-10-29T08:00:00Z', read_by: 'claude-mac',
}
const PREFERENCE: AgentMemoryEntry = {
  ...LESSON, name: 'answer-short', kind: 'preference', description: 'The verdict first, five lines at most',
  body: 'I read the answers on a phone.', locked: true, revision: 1, created_by: 'operator', created_in: null,
  updated_by: 'operator', updated_in: null, read_at: null, read_by: null,
}
const STATE: AgentMemoryState = {
  entries: [PREFERENCE, LESSON], pause: null, agent: true, used: true,
  kinds: ['preference', 'lesson', 'finding', 'reference'], limits: { entries: 200, description: 200, body: 4000 },
}
const HISTORY: AgentMemoryChange[] = [
  { seq: 3, at: '2026-10-29T07:00:00Z', action: 'changed', name: 'range-ticks', by: 'agent', address: null,
    conversation: CONVERSATION, title_of: 'Ticks', request: 1,
    before: { kind: 'lesson', description: LESSON.description, body: 'R6000.', locked: false },
    after: { kind: 'lesson', description: LESSON.description, body: LESSON.body, locked: false } },
  { seq: 2, at: '2026-10-28T09:00:00Z', action: 'forgotten', name: 'old-note', by: 'agent', address: null,
    conversation: CONVERSATION, title_of: 'Ticks', request: 0, why: 'Proved wrong',
    before: { kind: 'finding', description: 'A strategy without edge', body: 'No edge in 90 days.', locked: false }, after: null },
]

let sent: { method: string; path: string; body: unknown }[] = []
let state: AgentMemoryState = STATE
let changeStatus = 200

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
    if (route === '/api/agent/memory' && method === 'GET') return json(state)
    if (route === '/api/agent/memory' && method === 'POST') return json({ ...PREFERENCE, ...(body as object), revision: 1 }, 201)
    if (route === '/api/agent/memory/history') return json({ items: HISTORY })
    if (route === '/api/agent/memory/restore') return json({ ...LESSON, name: 'old-note' })
    if (route === '/api/agent/memory/pause') return json({ pause: { since: '2026-10-29T09:00:00Z', by: '10.8.0.2' } })
    if (route.endsWith('/lock')) return json({ ...LESSON, locked: true, revision: 3 })
    if (route === '/api/agent/memory/range-ticks' && method === 'PUT') {
      return changeStatus === 409
        ? json({ detail: "'range-ticks' changed since it was read (it is at revision 3): read it again" }, 409)
        : json({ ...LESSON, ...(body as object), revision: 3 })
    }
    if (route === '/api/agent/memory/range-ticks' && method === 'DELETE') return json(null, 204)
    return json({ detail: 'Not here' }, 404)
  }))
}

function Address() {
  const location = useLocation()
  return <output aria-label="address">{location.pathname + location.search}</output>
}

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/agent/memory', element: <WithTrail><MemoryPage /><Address /></WithTrail> }],
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

const posted = (path: string) => sent.find((request) => request.method !== 'GET' && request.path === path)

describe("the AI agent's memory page", () => {
  beforeEach(() => {
    state = STATE
    changeStatus = 200
    serve()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists the entries, opens the one its address names on its text, and locks it', async () => {
    const user = userEvent.setup()
    renderAt('/agent/memory?find=range-ticks')

    expect(await screen.findByText('range-ticks')).toBeInTheDocument()
    expect(screen.queryByText('answer-short')).toBeNull()
    await waitFor(() => expect(screen.getByText('thousandths')).toBeVisible())
    expect(screen.getByText(/Read last by Claude Code \(claude-mac\)/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Its history' })).toHaveAttribute('href', '/agent/memory?tab=history&find=range-ticks')
    await user.click(screen.getByRole('button', { name: 'Lock: the agent only reads it' }))

    await waitFor(() => expect(posted('/api/agent/memory/range-ticks/lock')?.body).toEqual({ locked: true }))
  })

  it('writes a new entry named from its line, locked unless untold', async () => {
    const user = userEvent.setup()
    renderAt('/agent/memory')

    await user.click(await screen.findByRole('button', { name: 'New entry' }))
    const dialog = await screen.findByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Save the entry' })
    expect(save).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox', { name: 'When it matters' }), 'Never a stop under 5 %')
    expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('never-a-stop-under-5')
    await user.type(within(dialog).getByRole('textbox', { name: 'What it should remember' }), 'My rule since October.')
    await user.click(save)

    await waitFor(() => expect(posted('/api/agent/memory')?.body).toEqual({
      name: 'never-a-stop-under-5', kind: 'preference', description: 'Never a stop under 5 %',
      body: 'My rule since October.', locked: true,
    }))
  })

  it('changes an entry from the revision it showed, and says so when the agent changed it meanwhile', async () => {
    const user = userEvent.setup()
    changeStatus = 409
    renderAt('/agent/memory?find=range-ticks')

    await user.click(await screen.findByRole('button', { name: 'Change' }))
    const dialog = await screen.findByRole('dialog')
    const text = within(dialog).getByRole('textbox', { name: 'What it should remember' })
    await user.clear(text)
    await user.type(text, 'Thousandths.')
    await user.click(within(dialog).getByRole('button', { name: 'Save the change' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/changed since it was read.*open the entry again/)
    expect(posted('/api/agent/memory/range-ticks')?.body).toMatchObject({ body: 'Thousandths.', revision: 2, name: 'range-ticks' })
  })

  it('hides an entry forgotten until its Undo, and forgets it from its revision once the toast is closed', async () => {
    const user = userEvent.setup()
    renderAt('/agent/memory?find=range-ticks')

    await user.click(await screen.findByRole('button', { name: 'Forget it' }))
    expect(screen.queryByText(LESSON.description)).toBeNull()
    await user.click(await screen.findByRole('button', { name: /Undo/ }))
    expect(await screen.findByText(LESSON.description)).toBeInTheDocument()
    expect(sent.some((request) => request.method === 'DELETE')).toBe(false)

    await user.click(await screen.findByRole('button', { name: 'Forget it' }))
    await user.click(await screen.findByRole('button', { name: 'Close notification' }))
    await waitFor(() => expect(posted('/api/agent/memory/range-ticks?revision=2')).toBeDefined())
  })

  it('pauses the agent\'s changes, and says so with the memory off', async () => {
    const user = userEvent.setup()
    state = { ...STATE, used: false }
    renderAt('/agent/memory')

    expect(await screen.findByText(/does not use its memory/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Pause its changes' }))
    await waitFor(() => expect(posted('/api/agent/memory/pause')).toBeDefined())
  })

  it('shows its history and brings back a forgotten entry', async () => {
    const user = userEvent.setup()
    renderAt('/agent/memory?tab=history')

    expect(await screen.findByText('The AI agent forgot old-note: Proved wrong')).toBeInTheDocument()
    expect(screen.getByText('The AI agent changed range-ticks')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Ticks' })[0]).toHaveAttribute('href', `/agent/${CONVERSATION}`)
    await user.click(screen.getByText('The AI agent forgot old-note: Proved wrong'))
    await user.click(await screen.findByRole('button', { name: 'Bring it back' }))

    await waitFor(() => expect(posted('/api/agent/memory/restore')?.body).toEqual({ seq: 2 }))
  })
})
