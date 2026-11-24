import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { Session, SignalTrace } from '@/lib/types'
import { TRACE, UUID } from '@/test/signalTrace'
import { WithTrail } from '@/test/WithTrail'

import { SignalPage } from './SignalPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.101.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

let trace: SignalTrace = TRACE

beforeEach(() => {
  trace = TRACE
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const url = new URL(path, 'http://admin.test')
      if (url.pathname === `/api/signals/${UUID}`) return json(trace)
      if (url.pathname === '/api/agent') return json({ enabled: false, model: 'claude-opus-5-5' })
      return json({ detail: 'Signal not found' }, 404)
    }),
  )
})
afterEach(() => {
  vi.unstubAllGlobals()
})

function renderAt(address: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/signals/:ref', element: <WithTrail><SignalPage /></WithTrail> }], {
    initialEntries: [address],
  })
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

describe('the page of one signal', () => {
  it('says each moment of its way, from its bar to its position, under the Signals entry', async () => {
    renderAt(`/signals/${UUID}`)

    expect(await screen.findByRole('heading', { name: /^Long at / })).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(within(trail).getByRole('link', { name: 'Signals' })).toHaveAttribute('href', '/signals')
    for (const step of ['Bar closed', 'Raised by the engine', 'Signal received', 'Purchase refused by Saxo', 'Seen by WATA']) {
      expect(screen.getAllByText(step).length).toBeGreaterThan(0)
    }
    // How long each stretch took, from the bar's close.
    expect(screen.getByText('Bar close → received')).toBeInTheDocument()
    expect(screen.getByText('1.1 s')).toBeInTheDocument()
    expect(screen.getByText('Bar close → position seen')).toBeInTheDocument()
  })

  it('leads to its rule, its live day, the Engine monitor, its order, its position and the sale it made', async () => {
    renderAt(`/signals/${UUID}`)

    expect(await screen.findByRole('link', { name: 'Its rule' })).toHaveAttribute('href', '/ta/signals/0123456789ab')
    expect(screen.getByRole('link', { name: 'Its day: live, paper, backtest' })).toHaveAttribute(
      'href',
      '/ta/signals/0123456789ab/live/2026-11-23',
    )
    expect(screen.getByRole('link', { name: 'Engine monitor' })).toHaveAttribute('href', '/ta/monitor?rule=0123456789ab')
    expect(screen.getByRole('link', { name: '7001' })).toHaveAttribute('href', '/orders?find=7001')
    expect(screen.getByRole('link', { name: '5001' })).toHaveAttribute('href', '/positions?find=5001')
    expect(screen.getByRole('link', { name: '7002' })).toHaveAttribute('href', '/orders?find=7002')
    expect(screen.getByRole('link', { name: '4001' })).toHaveAttribute('href', '/positions?find=4001')
    expect(screen.getByRole('link', { name: 'v1' })).toHaveAttribute('href', '/ta/indicators/aaaaaaaaaaaa')
  })

  it('of TradingView names no rule, and leads to the list of signals', async () => {
    trace = { ...TRACE, source: { kind: 'received', label: 'TradingView or by hand', rule_id: null, rule_name: null, mode: null }, engine: null, run: null }
    renderAt(`/signals/${UUID}`)

    expect(await screen.findByText('TradingView, the scheduler or by hand')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Its rule' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'All signals' })).toHaveAttribute('href', '/signals')
  })

  it('that nobody recorded says so', async () => {
    renderAt('/signals/engine-404')

    expect(await screen.findByRole('heading', { name: 'No such signal' })).toBeInTheDocument()
  })
})
