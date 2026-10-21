import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeProvider } from '@/components/ThemeProvider'
import { ToastProvider } from '@/components/ToastProvider'
import { AuthContext } from '@/features/auth/auth-context'
import type { McpState, Session } from '@/lib/types'

import { callArguments, endpointState, shareLines } from './claude'
import { ClaudePanel } from './ClaudePanel'

const SESSION: Session = {
  authenticated: true,
  version: '0.23.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

function mcpState(changes: Partial<McpState> = {}): McpState {
  return {
    enabled: true,
    paused: { paused: false, changed_at: null },
    tokens_readable: true,
    tokens: [
      { id: '0a1b2c3d', name: 'claude-mac', created_at: '2026-10-20T08:00:00Z', expires_at: '2027-01-18T08:00:00Z', revoked_at: null, state: 'active', last_used_at: '2026-10-20T09:00:00Z' },
      { id: '99887766', name: 'old-mac', created_at: '2026-07-01T08:00:00Z', expires_at: '2026-09-29T08:00:00Z', revoked_at: null, state: 'expired', last_used_at: null },
    ],
    limits: {
      max_waiting_runs: 3, runs_per_day: 60, max_day_simulations: 10_000, study_minutes_per_hour: 20, study_memory_mb: 384,
      study_timeout_seconds: 90, max_scripts: 100, max_shadow_rules: 3, shadow_day_ms: 2000, min_free_disk_gb: 5,
      shadow_rules: false,
    },
    share: { waiting_runs: 3, runs_today: 12, scripts: 4, rules: 0, live_rules: 0, study_seconds_last_hour: 330 },
    calls: [
      { at: '2026-10-20T09:00:00Z', caller: 'claude-mac', tool: 'pine_save', arguments: { source: { sha256: 'ab', chars: 1830 }, name: 'EMA cross' }, outcome: 'ok', milliseconds: 2400 },
      { at: '2026-10-20T08:59:00Z', caller: 'claude-mac', tool: 'backtest_start', arguments: {}, outcome: 'refused', milliseconds: 3, detail: 'Experiment 1a2b3c is concluded (kept): open a new one with exp_new' },
    ],
    ...changes,
  }
}

describe("what the System page says of Claude's endpoint", () => {
  it('says whether the endpoint can be reached, and why not', () => {
    expect(endpointState(mcpState()).label).toBe('Open')
    expect(endpointState(mcpState({ enabled: false })).detail).toMatch(/admin\.mcp_enabled/)
    expect(endpointState(mcpState({ paused: { paused: true, changed_at: null } })).label).toBe('Paused')
    expect(endpointState(mcpState({ tokens_readable: false })).detail).toMatch(/cannot be read/)
    expect(endpointState(mcpState({ tokens: [] })).detail).toMatch(/watamcptoken create/)
  })

  it("says what Claude's work holds of the server beside its limits", () => {
    const lines = shareLines(mcpState())

    expect(lines.find((line) => line.label === 'Runs waiting')).toEqual({ label: 'Runs waiting', value: '3', of: 'of 3', full: true })
    expect(lines.find((line) => line.label === 'Pine studies, last hour')).toMatchObject({ value: '6 min', of: 'of 20 min', full: false })
    expect(lines.find((line) => line.label === 'Shadow rules')?.of).toBe('switched off')
  })

  it("gives a call's long arguments as their length, as the log keeps them, and leaves the empty ones out", () => {
    expect(callArguments(mcpState().calls[0])).toBe('source: 1830 characters, name: EMA cross')
    const call = { ...mcpState().calls[0], arguments: { rule_id: '', name: 'x', inputs: {}, plots: [], version: 0, alerts: { Up: 'long' } } }
    expect(callArguments(call)).toBe('name: x, version: 0, alerts: {"Up":"long"}')
  })
})

let state: McpState
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
      if (path === '/api/mcp') return json(state)
      if (path === '/api/mcp/paused') {
        state = { ...state, paused: { paused: (body as { paused: boolean }).paused, changed_at: '2026-10-20T09:05:00Z' } }
        return json(state.paused)
      }
      const revoke = /^\/api\/mcp\/tokens\/([^/]+)\/revoke$/.exec(path)
      if (revoke) {
        state = { ...state, tokens: state.tokens.map((token) => (token.id === revoke[1] ? { ...token, state: 'revoked' as const } : token)) }
        return json({ revoked: state.tokens.filter((token) => token.id === revoke[1]) })
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/system', element: <ClaudePanel /> }], { initialEntries: ['/system'] })
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

beforeEach(() => {
  state = mcpState()
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Claude panel', () => {
  it("shows the tokens without their secret, Claude's share and its last calls", async () => {
    renderPanel()

    expect(await screen.findByText('claude-mac')).toBeInTheDocument()
    expect(screen.getByText('Valid')).toBeInTheDocument()
    expect(screen.getByText('Expired')).toBeInTheDocument()
    expect(screen.getByText('pine_save')).toBeInTheDocument()
    expect(screen.getByText('source: 1830 characters, name: EMA cross')).toBeInTheDocument()
    expect(screen.getByText(/open a new one with exp_new/)).toBeInTheDocument()
    expect(screen.getByText(/shadow rules are switched off/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Experiments' })).toHaveAttribute('href', '/backtests/experiments')
  })

  it('pauses the endpoint and resumes it', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: 'Pause Claude' }))
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Resume' }))

    await waitFor(() => expect(sent.filter((request) => request.method === 'PUT').map((request) => request.body)).toEqual([{ paused: true }, { paused: false }]))
  })

  it('revokes a token once asked to', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(await screen.findByRole('button', { name: 'Revoke' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Revoke' }))

    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path === '/api/mcp/tokens/0a1b2c3d/revoke')).toBe(true))
    expect(await screen.findAllByText('Revoked')).toHaveLength(1)
  })

  it('says the endpoint is off when the configuration has it off, with no switch to pause it', async () => {
    state = mcpState({ enabled: false })
    renderPanel()

    expect(await screen.findByText(/Switched off in the configuration/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Pause Claude' })).toBeNull()
  })
})
