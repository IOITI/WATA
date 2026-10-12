import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'

import { useSession } from './auth-context'
import { AuthProvider } from './AuthProvider'
import { LoginPage } from './LoginPage'
import { RequireAuth } from './RequireAuth'

const SESSION = {
  authenticated: true,
  version: '0.11.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  restart_required: false,
}

type Handler = (init: RequestInit | undefined) => Response

function json(status: number, body?: unknown, headers: Record<string, string> = {}) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

const NO_SECOND_FACTOR: Record<string, Handler> = {
  'GET /api/healthz': () => json(200, { service: 'admin', totp: false }),
}

function stubApi(overrides: Record<string, Handler>) {
  const handlers = { ...NO_SECOND_FACTOR, ...overrides }
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    const handler = handlers[`${init?.method ?? 'GET'} ${path}`]
    if (!handler) throw new Error(`Unexpected request: ${init?.method ?? 'GET'} ${path}`)
    return handler(init)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function Private() {
  const session = useSession()
  return <p>Private page, WATA {session.version}</p>
}

function renderApp(initialPath: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/positions"
              element={
                <RequireAuth>
                  <Private />
                </RequireAuth>
              }
            />
            <Route path="/" element={<p>Dashboard</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('sign-in', () => {
  it('sends a signed-out visitor to the login page, then back to where they were going', async () => {
    let signedIn = false
    const fetchMock = stubApi({
      'GET /api/session': () => (signedIn ? json(200, SESSION) : json(401, { detail: 'Not authenticated' })),
      'POST /api/session': () => {
        signedIn = true
        return json(204)
      },
    })
    const user = userEvent.setup()
    renderApp('/positions')

    const password = await screen.findByLabelText('Password')
    expect(screen.queryByText(/Private page/)).not.toBeInTheDocument()

    await user.type(password, 'correct horse battery')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Private page, WATA 0.11.0')).toBeInTheDocument()
    const login = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(login?.[1]?.body as string)).toEqual({ password: 'correct horse battery' })
  })

  it('lets the theme be chosen before signing in', async () => {
    stubApi({
      'GET /api/session': () => json(401, { detail: 'Not authenticated' }),
      'GET /api/healthz': () => json(200, { service: 'admin', totp: false }),
    })
    renderApp('/login')

    expect(await screen.findByRole('button', { name: /^Theme:/ })).toBeInTheDocument()
  })

  it('shows why a sign-in was refused and clears the field', async () => {
    stubApi({
      'GET /api/session': () => json(401, { detail: 'Not authenticated' }),
      'POST /api/session': () => json(401, { detail: 'Invalid password' }),
    })
    const user = userEvent.setup()
    renderApp('/login')

    const password = await screen.findByLabelText('Password')
    await user.type(password, 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid password')
    expect(password).toHaveValue('')
  })

  it('says how long a locked-out sign-in has to wait', async () => {
    stubApi({
      'GET /api/session': () => json(401, { detail: 'Not authenticated' }),
      'POST /api/session': () => json(429, { detail: 'Too many failed attempts' }, { 'Retry-After': '840' }),
    })
    const user = userEvent.setup()
    renderApp('/login')

    await user.type(await screen.findByLabelText('Password'), 'wrong')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 14 min')
  })

  it('asks for a one-time code when the second factor is on, and sends it with the password', async () => {
    let signedIn = false
    const fetchMock = stubApi({
      'GET /api/session': () => (signedIn ? json(200, SESSION) : json(401, { detail: 'Not authenticated' })),
      'GET /api/healthz': () => json(200, { service: 'admin', totp: true }),
      'POST /api/session': () => {
        signedIn = true
        return json(204)
      },
    })
    const user = userEvent.setup()
    renderApp('/login')

    const code = await screen.findByLabelText('One-time code')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery')
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled()

    await user.type(code, '123 456')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
    const login = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(login?.[1]?.body as string)).toEqual({ password: 'correct horse battery', code: '123 456' })
  })

  it('does not submit an empty password', async () => {
    const fetchMock = stubApi({ 'GET /api/session': () => json(401, { detail: 'Not authenticated' }) })
    renderApp('/login')

    const button = await screen.findByRole('button', { name: 'Sign in' })

    expect(button).toBeDisabled()
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('takes a visitor who is already signed in away from the login page', async () => {
    stubApi({ 'GET /api/session': () => json(200, SESSION) })
    renderApp('/login')

    expect(await screen.findByText('Dashboard')).toBeInTheDocument()
  })

  it('says so when the server cannot be reached, instead of showing a login form', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    renderApp('/positions')

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('The admin server cannot be reached'))
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })
})
