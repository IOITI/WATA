import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RestartPlan, ServicesState, WataService } from '@/lib/types'

import { ContainersPanel } from './ContainersPanel'

function service(id: string, name: string, overrides: Partial<WataService> = {}): WataService {
  return {
    id,
    name,
    trading_path: false,
    container: `${id}1`,
    state: 'running',
    running: true,
    started_at: '2026-10-24T07:00:00.123456789Z',
    finished_at: null,
    exit_code: 0,
    oom_killed: false,
    restart_count: 2,
    image: 'wata-base:latest',
    restarting: false,
    last_restart: null,
    pending: { paths: [], outside: false },
    refusal: null,
    cost: `What a restart of the ${name.toLowerCase()} costs.`,
    ...overrides,
  }
}

function servicesState(overrides: Partial<ServicesState> = {}): ServicesState {
  return {
    available: true,
    error: null,
    starts_known: true,
    actions_enabled: true,
    services: [
      service('telegram', 'Telegram', { pending: { paths: ['logging.level'], outside: false } }),
      service('ta_engine', 'TA engine', { state: 'absent', running: null, started_at: null, restart_count: null }),
      service('trader', 'Trader', {
        trading_path: true,
        pending: { paths: ['reporting.signal_silence_minutes', 'logging.level'], outside: false },
        refusal: '1 position open',
      }),
    ],
    plan: null,
    ...overrides,
  }
}

let state: ServicesState
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
      if (path === '/api/system/services') return json(state)
      if (path === '/api/system/services/restart' && method === 'POST') {
        const asked = body as { services: string[]; force: boolean }
        const plan: RestartPlan = {
          services: asked.services,
          forced: asked.force,
          by: '10.0.0.1',
          started_at: '2026-10-24T09:00:00+00:00',
          steps: asked.services.map((id) => ({ service: id, state: 'waiting', detail: null })),
          done: false,
        }
        state = { ...state, plan }
        return json({ plan }, 202)
      }
      if (path === '/api/session') return json({})
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/system', element: <ContainersPanel timeZone="Europe/Paris" /> }], {
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
  state = servicesState()
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the containers panel', () => {
  it('says what each container does and what it does not run with yet', async () => {
    renderPanel()

    const telegram = (await screen.findByText('Telegram')).closest('tr')!
    expect(telegram).toHaveTextContent(/Running since/)
    expect(telegram).toHaveTextContent('1 saved change not applied')
    const engine = screen.getByText('TA engine').closest('tr')!
    expect(engine).toHaveTextContent('Not created: its compose profile is off')
    expect(within(engine).getByRole('button', { name: 'Restart' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Restart the 2 with changes' })).toBeEnabled()
  })

  it('restarts a service on its own, at once', async () => {
    const user = userEvent.setup()
    renderPanel()

    const telegram = (await screen.findByText('Telegram')).closest('tr')!
    await user.click(within(telegram).getByRole('button', { name: 'Restart' }))
    const dialog = await screen.findByRole('dialog', { name: 'Restart the telegram' })
    expect(dialog).toHaveTextContent('What a restart of the telegram costs.')
    await user.click(within(dialog).getByRole('button', { name: 'Restart' }))

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/services/restart')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/services/restart')!.body).toEqual({ services: ['telegram'], force: false })
    expect(await screen.findByText('Restarting')).toBeInTheDocument()
  })

  it('asks to restart anyway what is held back, and says it to the server', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: 'Restart the 2 with changes' }))
    const dialog = await screen.findByRole('dialog', { name: 'Restart 2 services' })
    expect(within(dialog).getByText('Held back now')).toBeInTheDocument()
    expect(dialog).toHaveTextContent('Trader: 1 position open')
    const slider = within(dialog).getByRole('slider', { name: 'Slide to restart' })
    slider.focus()
    await user.keyboard('{Enter}')
    expect(sent.some((call) => call.path === '/api/system/services/restart')).toBe(false)

    await user.click(within(dialog).getByRole('checkbox', { name: 'Restart anyway' }))
    slider.focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/services/restart')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/services/restart')!.body).toEqual({
      services: ['telegram', 'trader'],
      force: true,
    })
  })

  it('restarts the others now when those held back are left out', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: 'Restart the 2 with changes' }))
    const dialog = await screen.findByRole('dialog', { name: 'Restart 2 services' })
    await user.click(within(dialog).getByRole('button', { name: 'Leave it out: restart the other' }))

    expect(await screen.findByRole('dialog', { name: 'Restart the telegram' })).toHaveTextContent(
      'Left out, held back now: Trader.',
    )
    await user.click(within(dialog).getByRole('button', { name: 'Restart' }))

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/services/restart')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/services/restart')!.body).toEqual({ services: ['telegram'], force: false })
  })

  it('says why nothing can be restarted from here without the control service', async () => {
    state = servicesState({
      available: false,
      starts_known: false,
      error: 'No control service runs beside this admin: restart the services on the server (watastart)',
    })
    renderPanel()

    const said = await screen.findByText(/counted from the admin’s own start/)
    expect(said).toHaveTextContent('No control service runs beside this admin')
    for (const button of screen.getAllByRole('button', { name: 'Restart' })) expect(button).toBeDisabled()
  })

  it('shows a plan as it goes, then what it came to', async () => {
    state = servicesState({
      plan: {
        services: ['telegram', 'trader'],
        forced: false,
        by: null,
        started_at: '2026-10-24T09:00:00+00:00',
        steps: [
          { service: 'telegram', state: 'ready', detail: 'Consuming telegram_channel' },
          { service: 'trader', state: 'held', detail: 'Held back: 1 position open' },
        ],
        done: true,
      },
    })
    renderPanel()

    expect(await screen.findByText('Restart done')).toBeInTheDocument()
    expect(screen.getByText('Telegram is at work again; Trader: Held back: 1 position open.')).toBeInTheDocument()
  })
})
