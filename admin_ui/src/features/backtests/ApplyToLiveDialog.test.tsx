import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/features/auth/auth-context'
import type { ConfigSaved, RunToLive, Session } from '@/lib/types'
import { RUN_ID } from '@/test/backtests'
import { CATALOG, STOPLOSS } from '@/test/configCatalog'

import { ApplyToLiveDialog } from './ApplyToLiveDialog'

const SESSION: Session = {
  authenticated: true, version: '0.88.0', timezone: 'Europe/Paris', actions_enabled: true, config_edit_enabled: true,
  backtests_enabled: true, totp_enabled: false, restart_required: false,
}

const FOUND: RunToLive = {
  run: { id: RUN_ID, name: 'Tighter stop' },
  revision: 'r1',
  differences: [
    { setting: 'stoploss_percent', label: 'Stop-loss', unit: '%', group: 'exits', before: -20, after: -10, own: true,
      paths: [STOPLOSS], apply: true, why_not: null, note: null },
    { setting: 'cooldown_after_loss_minutes', label: 'No entry after a losing trade for', unit: 'min', group: 'entries',
      before: 10, after: 5, own: false, paths: ['trade.rules[risk_management].rule_config.cooldown_after_loss_minutes'],
      apply: true, why_not: null, note: null },
    { setting: 'trading_end_hour', label: 'No entry from', unit: 'h', group: 'entries', before: 22, after: 24, own: true,
      paths: ['trade.rules[market_hours].rule_config.trading_end_hour'], apply: false,
      why_not: 'No entry from is from 0 up to 23 in config.json', note: null },
  ],
  backtest_only: [{ setting: 'capital', label: 'Account at the start', unit: '€', value: 5000 }],
}

const PREVIEW = {
  valid: true, revision: 'r1', warnings: [],
  changes: [{ path: STOPLOSS, before: -20, after: -10, added: false, removed: false }],
  impact: { services: ['position_monitor'], consumers: [], risk: 'trading', unknown: [] },
}

const SAVED: ConfigSaved = { revision: 'r2', backup: 'config.json.bak-20261116-220000', restart_required: true,
  changes: PREVIEW.changes, impact: PREVIEW.impact as ConfigSaved['impact'] }

let sent: { method: string; path: string; body: unknown }[] = []
let answerApply: () => Response

function renderDialog(session: Session = SESSION) {
  const onSaved = vi.fn()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthContext.Provider value={{ session, error: null, login: async () => {}, logout: async () => {} }}>
        {/* The review links each key to its place on the Configuration page. */}
        <MemoryRouter>
          <ApplyToLiveDialog runId={RUN_ID} open onClose={() => {}} onSaved={onSaved} />
        </MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>,
  )
  return onSaved
}

beforeEach(() => {
  sent = []
  answerApply = () => new Response(JSON.stringify(SAVED), { status: 200 })
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    sent.push({ method, path, body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined })
    if (path === `/api/backtests/${RUN_ID}/to-live`) return new Response(JSON.stringify(FOUND), { status: 200 })
    if (path === `/api/backtests/${RUN_ID}/to-live/preview`) return new Response(JSON.stringify(PREVIEW), { status: 200 })
    if (path === `/api/backtests/${RUN_ID}/to-live/apply`) return answerApply()
    if (path === '/api/backtests/options') return new Response(JSON.stringify({ fields: [] }), { status: 200 })
    if (path === '/api/config/catalog') return new Response(JSON.stringify(CATALOG), { status: 200 })
    throw new Error(`Unexpected request: ${method} ${path}`)
  }))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("applying a run's settings to WATA", () => {
  it('ticks every setting that differs, reviews the change of config.json, and saves it with the revision reviewed', async () => {
    const user = userEvent.setup()
    const onSaved = renderDialog()
    const dialog = within(await screen.findByRole('dialog', { name: 'Apply this run’s settings to WATA' }))

    const toApply = within(await dialog.findByRole('list', { name: 'Settings to apply' }))
    expect(toApply.getByRole('checkbox', { name: 'Stop-loss' })).toBeChecked()
    expect(toApply.getByRole('checkbox', { name: 'No entry after a losing trade for' })).toBeChecked()
    expect(toApply.getByText('WATA’s then, changed since')).toBeInTheDocument()
    expect(within(dialog.getByRole('list', { name: 'Settings not applied' })).getByText('No entry from is from 0 up to 23 in config.json'))
      .toBeInTheDocument()
    expect(dialog.getByText(/Only a backtest has these, nothing to apply: Account at the start 5000 €/)).toBeInTheDocument()

    // The cooldown WATA changed since is kept as it is: only the stop is applied.
    await user.click(toApply.getByRole('checkbox', { name: 'No entry after a losing trade for' }))
    await user.click(dialog.getByRole('button', { name: 'Review the changes' }))
    expect(await dialog.findByText(/This acts on the live trading account/)).toBeInTheDocument()
    await waitFor(() => expect(dialog.getByRole('slider')).not.toHaveAttribute('aria-disabled', 'true'))
    dialog.getByRole('slider').focus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(SAVED))
    expect(sent.filter((request) => request.method === 'POST').map((request) => [request.path, request.body])).toEqual([
      [`/api/backtests/${RUN_ID}/to-live/preview`, { settings: ['stoploss_percent'] }],
      [`/api/backtests/${RUN_ID}/to-live/apply`, { settings: ['stoploss_percent'], revision: 'r1' }],
    ])
  })

  it('says config.json moved on since the review, and offers to review it again', async () => {
    answerApply = () => new Response(JSON.stringify({ detail: 'The file changed' }), { status: 409 })
    const user = userEvent.setup()
    const onSaved = renderDialog()
    const dialog = within(await screen.findByRole('dialog', { name: 'Apply this run’s settings to WATA' }))

    await user.click(await dialog.findByRole('button', { name: 'Review the changes' }))
    await waitFor(() => expect(dialog.getByRole('slider')).not.toHaveAttribute('aria-disabled', 'true'))
    dialog.getByRole('slider').focus()
    await user.keyboard('{Enter}')

    expect(await dialog.findByText(/config\.json was saved by someone else since this review/)).toBeInTheDocument()
    await user.click(dialog.getByRole('button', { name: 'Review again' }))
    expect(await dialog.findByRole('list', { name: 'Settings to apply' })).toBeInTheDocument()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('shows the difference with editing off, and holds the review back', async () => {
    renderDialog({ ...SESSION, config_edit_enabled: false })
    const dialog = within(await screen.findByRole('dialog', { name: 'Apply this run’s settings to WATA' }))

    expect(await dialog.findByRole('list', { name: 'Settings to apply' })).toBeInTheDocument()
    expect(dialog.getByRole('button', { name: 'Review the changes' })).toBeDisabled()
  })
})
