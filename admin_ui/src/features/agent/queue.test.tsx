import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { QueueItem, ResearchQueue, Session } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { ResearchQueuePage } from './ResearchQueuePage'

const SESSION: Session = {
  authenticated: true, version: '0.97.0', timezone: 'Europe/Paris', actions_enabled: true, config_edit_enabled: true,
  backtests_enabled: true, totp_enabled: false, restart_required: false,
}

function item(id: string, title: string, extra: Partial<QueueItem> = {}): QueueItem {
  return {
    id, title, hypothesis: `${title}: the data can say no`, family: 'opens', rationale: null, criteria: [], motivated_by: [],
    by: 'operator', conversation_from: null, state: 'waiting', order: 1, created_at: '2026-11-20T10:00:00Z', updated_at: null,
    conversation: null, experiment: null, conclusion: null, ...extra,
  }
}

const QUEUE: ResearchQueue = {
  revision: 'rev1',
  items: [
    item('q00000001', 'Open block', { criteria: [{ figure: 'edge_t', bound: 'at_least', value: 2.6 }], motivated_by: ['2026-11-11'] }),
    item('q00000002', 'Late entries', { order: 2 }),
    item('q00000003', 'Gap fill', { by: 'agent:20261120-180000-abcdef', family: 'gaps', order: 0 }),
    item('q00000004', 'Slower line', { state: 'done', experiment: 'a4b020', conversation: '20261119-220000-bbbbbb',
      conclusion: 'No edge on 22 days.', family: 'kernel-avg-vwap' }),
  ],
  tired: ['kernel-avg-vwap'],
  agent: true,
  autopilot: null,
  limits: { agent_waiting: 20, per_conversation: 3 },
}

let sent: { method: string; path: string; body: unknown }[] = []

function serve() {
  sent = []
  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    const url = new URL(path, 'http://admin.test')
    const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
    sent.push({ method, path: `${url.pathname}${url.search}`, body })
    if (url.pathname === '/api/agent') return json({ enabled: true })
    if (url.pathname === '/api/agent/queue' && method === 'GET') return json(QUEUE)
    if (url.pathname === '/api/agent/queue' && method === 'POST') return json({ revision: 'rev2', item: { ...QUEUE.items[0], ...(body as object) } }, 201)
    if (url.pathname.startsWith('/api/agent/queue/') && method === 'PATCH') return json({ revision: 'rev2', item: QUEUE.items[0] })
    return json({ detail: 'Not here' }, 404)
  }))
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [{ path: '/agent/queue', element: <WithTrail><ResearchQueuePage /></WithTrail> }],
    { initialEntries: ['/agent/queue'] },
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

const patched = (id: string) => sent.find((request) => request.method === 'PATCH' && request.path === `/api/agent/queue/${id}`)

describe('the research queue page', () => {
  beforeEach(() => serve())
  afterEach(() => vi.unstubAllGlobals())

  it('lists what waits in the order the autopilot takes it, and says when there is no autopilot yet', async () => {
    renderPage()

    const waiting = await screen.findByRole('list', { name: 'Waiting, in the order they are tried' })
    const rows = within(waiting).getAllByRole('listitem')
    expect(rows.map((row) => within(row).getByText(/^(Open block|Late entries|Gap fill)$/).textContent)).toEqual(['Open block', 'Late entries', 'Gap fill'])
    expect(within(rows[0]).getByText(/must show: edge t at least 2\.6/)).toBeInTheDocument()
    expect(within(rows[2]).getByRole('link', { name: 'a research task' })).toHaveAttribute('href', '/agent/20261120-180000-abcdef')
    expect(screen.getByText(/No research autopilot yet/)).toBeInTheDocument()
    expect(screen.getByText(/these families wait behind the others: kernel-avg-vwap/)).toBeInTheDocument()
    expect(screen.getByText('No edge on 22 days.')).toBeInTheDocument()
  })

  it('moves the operator’s hypotheses among theirs and takes a task’s as theirs, from the revision read', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Move Late entries up' }))
    await waitFor(() => expect(patched('q00000002')?.body).toEqual({ move: 0, revision: 'rev1' }))
    expect(screen.getByRole('button', { name: 'Move Open block up' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Take Gap fill as yours' }))
    await waitFor(() => expect(patched('q00000003')?.body).toEqual({ move: 2, revision: 'rev1' }))
  })

  it('files a hypothesis of the operator’s', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'New hypothesis' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'File it' })).toBeDisabled()
    await user.type(within(dialog).getByLabelText('Title'), 'No entry before 09:40')
    await user.type(within(dialog).getByLabelText('The hypothesis'), 'Entries of the first ten minutes lose')
    await user.type(within(dialog).getByLabelText('Family of ideas'), 'opens')
    await user.type(within(dialog).getByLabelText('The days it came from (optional)'), '2026-11-11, 2026-11-12')
    await user.click(within(dialog).getByRole('button', { name: 'File it' }))

    await waitFor(() => expect(sent.find((request) => request.method === 'POST')?.body).toEqual({
      title: 'No entry before 09:40', hypothesis: 'Entries of the first ten minutes lose', family: 'opens', rationale: null,
      motivated_by: ['2026-11-11', '2026-11-12'],
    }))
  })
})
