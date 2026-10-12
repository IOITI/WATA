import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, Route, Routes } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { Session } from '@/lib/types'

import { AppShell } from './AppShell'
import { ToastProvider } from './ToastProvider'

const SESSION: Session = {
  authenticated: true,
  version: '0.11.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

interface ServerState {
  saxo?: 'ok' | 'down' | 'unknown'
  pausedSince?: string | null
  restartRequired?: boolean
}

function renderShell({ saxo = 'ok', pausedSince = null, restartRequired = false }: ServerState = {}) {
  const bodies: Record<string, unknown> = {
    '/api/system/health': {
      status: saxo === 'down' ? 'down' : 'ok',
      services: [
        { id: 'saxo', name: 'Saxo connection', status: saxo, detail: '' },
        { id: 'web_server', name: 'Web server', status: 'ok', detail: '' },
      ],
    },
    '/api/overview': { entries_paused_since: pausedSince },
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (!(path in bodies)) throw new Error(`Unexpected request: ${path}`)
      return new Response(JSON.stringify(bodies[path]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider
        value={{
          session: { ...SESSION, restart_required: restartRequired },
          error: null,
          login: async () => {},
          logout: async () => {},
        }}
      >
        <ToastProvider>
          <MemoryRouter initialEntries={['/positions']}>
            <Routes>
              <Route element={<AppShell />}>
                <Route path="positions" element={<h1>Positions</h1>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('trading state in the top bar', () => {
  it('shows nothing while everything is as usual', async () => {
    renderShell()

    // The health summary has arrived: the chips had what they need to appear.
    expect(await screen.findByText('Up')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Saxo: authorise/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Entries paused/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Restart needed/ })).not.toBeInTheDocument()
  })

  it('says on every page that Saxo needs a new authorisation, and where to give it', async () => {
    renderShell({ saxo: 'down' })

    const chip = await screen.findByRole('link', { name: /Saxo: authorise/ })
    expect(chip).toHaveAttribute('href', '/system')
    expect(chip).toHaveAccessibleName(/nothing can be traded/)
    // The summary beside it agrees.
    expect(screen.getByText('Down')).toBeInTheDocument()
  })

  it('does not raise the alarm when the token state could not be read', async () => {
    renderShell({ saxo: 'unknown' })

    expect(await screen.findByText('Up')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Saxo: authorise/ })).not.toBeInTheDocument()
  })

  it('says on every page that new entries are paused, and where to resume them', async () => {
    renderShell({ pausedSince: '2026-10-12T05:09:22Z' })

    expect(await screen.findByRole('link', { name: /Entries paused/ })).toHaveAttribute('href', '/')
  })

  it('says that the services run an older configuration, and links to it', async () => {
    renderShell({ restartRequired: true })

    expect(await screen.findByRole('link', { name: /Restart needed/ })).toHaveAttribute('href', '/config')
    // The notice itself is on the configuration page, not across the top of every page.
    expect(screen.queryByText('Configuration changed since the services started')).not.toBeInTheDocument()
  })

  it('no longer repeats the page name in a breadcrumb', async () => {
    renderShell()

    await screen.findByText('Up')
    expect(screen.queryByRole('navigation', { name: /breadcrumb/i })).not.toBeInTheDocument()
    expect(screen.getAllByText('Positions').length).toBeGreaterThan(0)
  })
})
