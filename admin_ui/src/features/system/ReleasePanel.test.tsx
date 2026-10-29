import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReleaseContainer, ReleaseState } from '@/lib/types'

import { ReleasePanel } from './ReleasePanel'

function entry(container: string, role: string, overrides: Partial<ReleaseContainer> = {}): ReleaseContainer {
  return {
    container,
    role,
    kind: 'unchanged',
    when: 'skip',
    version: '0.61.0',
    target: null,
    reasons: {},
    held: null,
    group: null,
    held_with: [],
    image: 'wata-base:0.61.0',
    said: '',
    ...overrides,
  }
}

function releaseState(overrides: Partial<ReleaseState> = {}): ReleaseState {
  return {
    available: true,
    error: null,
    actions_enabled: true,
    held: { trader1: "the market is open by WATA's hours" },
    applying: null,
    status: {
      newest: '0.61.0',
      planned_at: '2026-10-29T12:00:00+00:00',
      containers: [
        entry('admin1', 'admin'),
        entry('trader1', 'trader', { kind: 'change', when: 'pending', version: '0.59.1', group: 1,
          reasons: { code: ['src/trade/rules.py'] } }),
        entry('scheduler1', 'scheduler', { kind: 'change', when: 'pending', version: '0.59.1', group: 1,
          reasons: { code: ['src/trade/rules.py'] }, held_with: ['trader1'] }),
        entry('ta_engine1', 'ta_engine', { kind: 'change', when: 'now', version: '0.59.1',
          reasons: { code: ['src/ta_engine/engine.py'] } }),
      ],
      requests: [],
    },
    ...overrides,
  }
}

let state: ReleaseState
let sent: { method: string; path: string; body: unknown }[] = []

function serve() {
  sent = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/system/release') return json(state)
      if (path === '/api/system/release/apply' && method === 'POST') {
        const asked = body as { containers: string[]; force: boolean }
        return json({ request: { id: 'a'.repeat(32), version: '0.61.0', containers: asked.containers, force: asked.force,
          asked_by: 'the System page', asked_at: '2026-10-29T12:00:00+00:00' } }, 202)
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/system', element: <ReleasePanel timeZone="Europe/Paris" /> }], {
    initialEntries: ['/system'],
  })
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  state = releaseState()
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the release panel', () => {
  it('says what waits for the newest release, why, and what holds it back', async () => {
    renderPanel()

    expect(await screen.findByText(/0.61.0 is the newest release installed; 1 container runs it; 3 containers wait for it./)).toBeInTheDocument()
    const trader = screen.getByText('trader1').closest('tr')!
    expect(trader).toHaveTextContent('its code (1 file: src/trade/rules.py)')
    expect(trader).toHaveTextContent("the market is open by WATA's hours")
    expect(within(trader).getByRole('button', { name: 'Apply' })).toBeEnabled()
    expect(within(trader).getByText('Trading')).toBeInTheDocument()
    const scheduler = screen.getByText('scheduler1').closest('tr')!
    expect(scheduler).toHaveTextContent('waits with trader1, which change because of the same files')
    expect(screen.getByText('ta_engine1').closest('tr')!).toHaveTextContent('May go now')
  })

  it('applies a container that may go now, at once', async () => {
    const user = userEvent.setup()
    renderPanel()

    const engine = (await screen.findByText('ta_engine1')).closest('tr')!
    await user.click(within(engine).getByRole('button', { name: 'Apply' }))
    const dialog = await screen.findByRole('dialog', { name: 'Apply 0.61.0 to ta_engine1' })
    expect(dialog).toHaveTextContent('Changes its code (1 file: src/ta_engine/engine.py).')
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/release/apply')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/release/apply')!.body).toEqual({ containers: ['ta_engine1'], force: false })
    expect(await screen.findByText('Asked: the server takes the request within seconds.')).toBeInTheDocument()
  })

  it('applies a group held back only anyway, with a slide, and says it to the server', async () => {
    const user = userEvent.setup()
    renderPanel()

    const trader = (await screen.findByText('trader1')).closest('tr')!
    await user.click(within(trader).getByRole('button', { name: 'Apply' }))
    const dialog = await screen.findByRole('dialog', { name: 'Apply 0.61.0 to 2 containers' })
    expect(within(dialog).getByText('Held back now')).toBeInTheDocument()
    const slider = within(dialog).getByRole('slider', { name: 'Slide to apply' })
    slider.focus()
    await user.keyboard('{Enter}')
    expect(sent.some((call) => call.path === '/api/system/release/apply')).toBe(false)

    await user.click(within(dialog).getByRole('checkbox', { name: 'Apply anyway' }))
    slider.focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/release/apply')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/release/apply')!.body).toEqual({
      containers: ['trader1', 'scheduler1'],
      force: true,
    })
  })

  it('shows an apply as it goes', async () => {
    state = releaseState({
      applying: {
        id: 'x', kind: 'apply', by: 'admin (the System page)', force: false, version: '0.61.0', request: null,
        started_at: '2026-10-29T12:00:00+00:00', done: false,
        steps: [
          { container: 'ta_engine1', role: 'ta_engine', before: 'wata-base:0.59.1', after: 'wata-base:0.61.0', state: 'ready', detail: 'its state written since its start', started_at: null },
          { container: 'admin1', role: 'admin', before: 'wata-base:0.59.1', after: 'wata-base:0.61.0', state: 'recreating', detail: null, started_at: null },
        ],
      },
    })
    renderPanel()

    expect(await screen.findByText(/Applying 0.61.0/)).toBeInTheDocument()
    expect(screen.getByText('Recreating')).toBeInTheDocument()
    for (const button of screen.getAllByRole('button', { name: /Apply/ })) expect(button).toBeDisabled()
  })

  it('says when the server has said nothing yet', async () => {
    state = releaseState({ available: false, error: 'watadeploy has said nothing yet on this server: run watadeploy status there', status: {} })
    renderPanel()

    expect(await screen.findByText(/watadeploy has said nothing yet on this server/)).toBeInTheDocument()
  })
})
