import { render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ClientErrorBody, Session } from '@/lib/types'

const SESSION: Session = {
  authenticated: true,
  version: '0.102.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

class SilentEventSource {
  addEventListener() {}
  close() {}
}

/** What the fake server was sent at /api/client-errors. */
let reports: ClientErrorBody[] = []

beforeEach(() => {
  reports = []
  vi.resetModules()
  vi.stubGlobal('EventSource', SilentEventSource)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/client-errors') {
        reports.push(JSON.parse(String(init?.body)) as ClientErrorBody)
        return new Response(null, { status: 204 })
      }
      // The session, and nothing else the shell asks for: it says what it could not read.
      const body = path === '/api/session' ? SESSION : { detail: 'Not here' }
      return new Response(JSON.stringify(body), {
        status: path === '/api/session' ? 200 : 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
})

afterEach(() => {
  vi.doUnmock('@/features/timing/TimingPage')
  vi.doUnmock('@/features/exits/ExitsPage')
  vi.unstubAllGlobals()
  window.history.replaceState({}, '', '/')
})

/** The application itself, opened at an address: its router reads the address when it is made. */
async function openApp(path: string) {
  window.history.replaceState({}, '', path)
  const { default: App } = await import('./App')
  render(<App />)
}

describe('a page that fails', () => {
  it('shows what went wrong inside the shell, and is reported to the admin once', async () => {
    vi.doMock('@/features/timing/TimingPage', () => ({
      TimingPage: () => {
        throw new TypeError('The timings broke')
      },
    }))
    // React says in the console what a boundary caught: expected here.
    const said = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    await openApp('/timing')

    expect(await screen.findByRole('heading', { name: 'Something went wrong' })).toBeInTheDocument()
    // The shell stays around it: the menu still leads everywhere.
    const menu = screen.getAllByRole('navigation').find((nav) => within(nav).queryByRole('link', { name: /Positions/ }))
    expect(menu).toBeDefined()

    await waitFor(() => expect(reports).toHaveLength(1))
    expect(reports[0]).toMatchObject({ source: 'route', path: '/timing', name: 'TypeError', message: 'The timings broke' })
    said.mockRestore()
  })

  it('whose code does not load says so inside the shell, and offers to reload', async () => {
    // Its lazy() import rejects as a browser's does once a deploy replaced the page's file.
    vi.doMock('@/features/exits/ExitsPage', () => ({
      get ExitsPage() {
        throw new TypeError('Failed to fetch dynamically imported module: /assets/ExitsPage-1a2b3c.js')
      },
    }))
    const said = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    await openApp('/exits')

    expect(await screen.findByRole('heading', { name: 'This page could not be loaded' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument()
    await waitFor(() => expect(reports).toHaveLength(1))
    expect(reports[0]).toMatchObject({ source: 'route', path: '/exits', name: 'TypeError' })
    expect(reports[0].message).toContain('dynamically imported module')
    said.mockRestore()
  })
})
