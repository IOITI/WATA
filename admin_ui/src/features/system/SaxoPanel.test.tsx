import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { SaxoStatus, Session } from '@/lib/types'

import { SaxoCallbackOutcome, SaxoPanel } from './SaxoPanel'

const SESSION: Session = {
  authenticated: true,
  version: '0.44.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

function saxoStatus(changes: Partial<SaxoStatus> = {}): SaxoStatus {
  return {
    has_token: true,
    saved_at: '2026-10-24T08:00:00+00:00',
    access_token_expires_at: '2026-10-24T08:20:00+00:00',
    refresh_token_expires_at: '2026-10-24T09:00:00+00:00',
    access_token_expired: false,
    refresh_token_expired: false,
    needs_authorization_since: null,
    last_refusal: null,
    environment: 'live',
    where: 'database',
    saved_by: 'position_monitor',
    redirect_url: 'https://admin.example.com/api/saxo/callback',
    callback: true,
    apps: {
      live: { set: true, app_key: 'a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6', fingerprint: 'c5d6' },
      simulation: { set: false, app_key: null, fingerprint: null },
    },
    authorization_url: null,
    ...changes,
  }
}

const LAPSED: Partial<SaxoStatus> = {
  refresh_token_expired: true,
  access_token_expired: true,
  needs_authorization_since: '2026-10-24T09:00:00+00:00',
  last_refusal: 'invalid_grant (400)',
}

let status: SaxoStatus
let sent: { method: string; path: string; body: unknown }[] = []

function serve() {
  sent = []
  const json = (body: unknown, code = 200) =>
    new Response(JSON.stringify(body), { status: code, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/system/saxo') return json(status)
      if (path === '/api/system/saxo/authorize' && method === 'POST') {
        return json({ url: 'https://live.logonvalidation.net/authorize?response_type=code&state=made-here' })
      }
      if (path === '/api/system/saxo/auth-code' && method === 'POST') return new Response(null, { status: 204 })
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPanel(address = '/system') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      {
        path: '/system',
        element: (
          <>
            <SaxoCallbackOutcome />
            <SaxoPanel />
          </>
        ),
      },
      { path: '/config/credentials', element: <p>Credentials</p> },
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
  return router
}

beforeEach(() => {
  status = saxoStatus()
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Saxo panel', () => {
  it('shows the login the services renew, and who saved it', async () => {
    renderPanel()

    expect(await screen.findByText('Authorised')).toBeInTheDocument()
    expect(screen.getByText('Refresh token expires')).toBeInTheDocument()
    expect(screen.getByText(/by the position monitor/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Open Saxo/ })).not.toBeInTheDocument()
  })

  it('opens a login link made for this login, which comes back to the admin by itself', async () => {
    status = saxoStatus(LAPSED)
    const opened = vi.fn()
    vi.stubGlobal('open', opened)
    const user = userEvent.setup()
    renderPanel()

    expect(await screen.findByText('Authorisation required')).toBeInTheDocument()
    expect(screen.getByText(/invalid_grant \(400\)/)).toBeInTheDocument()
    expect(screen.getByText('https://admin.example.com/api/saxo/callback')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Open Saxo/ }))

    await waitFor(() => expect(opened).toHaveBeenCalledTimes(1))
    expect(sent.find((call) => call.path === '/api/system/saxo/authorize')!.body).toEqual({ environment: 'live' })
    expect(opened.mock.calls[0][0]).toBe('https://live.logonvalidation.net/authorize?response_type=code&state=made-here')
    expect(await screen.findByText(/hands the code over by itself/)).toBeInTheDocument()
  })

  it('hands a pasted address over with its state, and a code alone without one', async () => {
    status = saxoStatus({ ...LAPSED, callback: false, redirect_url: 'https://example.com/saxo' })
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: 'I have the address' }))
    await user.type(await screen.findByLabelText('Redirect address or code'), 'https://example.com/saxo?code=abc-123&state=made-here')
    await user.click(screen.getByRole('button', { name: 'Hand the code over' }))

    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/saxo/auth-code')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/saxo/auth-code')!.body).toEqual({
      code: 'abc-123',
      state: 'made-here',
    })
    expect(await screen.findByText('Code handed over')).toBeInTheDocument()

    sent = []
    await user.click(screen.getByRole('button', { name: /Paste the address/ }))
    await user.type(await screen.findByLabelText('Redirect address or code'), 'abc-456')
    await user.click(screen.getByRole('button', { name: 'Hand the code over' }))
    await waitFor(() => expect(sent.some((call) => call.path === '/api/system/saxo/auth-code')).toBe(true))
    expect(sent.find((call) => call.path === '/api/system/saxo/auth-code')!.body).toEqual({ code: 'abc-456', state: null })
  })

  it('says what the callback came to, until it is put away', async () => {
    const user = userEvent.setup()
    const router = renderPanel('/system?saxo=refused&why=This%20login%27s%20link%20is%20unknown')

    const said = await screen.findByRole('alert')
    expect(said).toHaveTextContent('did not come back as expected')
    expect(said).toHaveTextContent("This login's link is unknown. A new link is made under Saxo connection")
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))

    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says when the login has no row yet, and offers no link the services would not read', async () => {
    status = saxoStatus({ where: 'none', has_token: false, saved_by: null, refresh_token_expired: true })
    renderPanel()

    expect(await screen.findByText('Not in PostgreSQL yet')).toBeInTheDocument()
    expect(screen.getByText(/token file/)).toHaveTextContent('watasaxoauth')
    expect(screen.queryByRole('button', { name: /Open Saxo/ })).not.toBeInTheDocument()
  })

  it('sends to the Credentials when the app is not sealed yet, and names the redirect to set', async () => {
    status = saxoStatus({
      ...LAPSED,
      environment: 'simulation',
      redirect_url: null,
      callback: false,
    })
    renderPanel()

    expect(await screen.findByRole('link', { name: 'Configuration, Credentials' })).toHaveAttribute('href', '/config/credentials')
    expect(screen.getByText(/simulation account’s app|simulation account's app/)).toBeInTheDocument()
    expect(screen.getByText(/No redirect address/)).toHaveTextContent('SAXO_REDIRECT_URL')
  })
})
