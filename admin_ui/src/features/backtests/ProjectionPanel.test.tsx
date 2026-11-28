import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BacktestProjection, ProjectionDay } from '@/lib/types'
import { OPTIONS, run, RUN_ID } from '@/test/backtests'

import { ProjectionPanel } from './ProjectionPanel'

function weekdays(from: string, to: string): ProjectionDay[] {
  const days: ProjectionDay[] = []
  for (let day = new Date(`${from}T12:00:00Z`); day.toISOString().slice(0, 10) <= to; day.setUTCDate(day.getUTCDate() + 1)) {
    if (day.getUTCDay() === 0 || day.getUTCDay() === 6) continue
    const iso = day.toISOString().slice(0, 10)
    if (iso === '2026-12-25' || iso === '2027-01-01') continue
    days.push(iso === '2026-12-24' ? { day: iso, kind: 'early_close', estimated: false, name: 'Christmas Eve', close_new_york: '13:00' } : { day: iso, kind: 'full', estimated: false })
  }
  return days
}

const PROJECTION: BacktestProjection = {
  usable: true,
  why_not: null,
  run_id: RUN_ID,
  from: '2026-11-30',
  to: '2028-11-29',
  capital: 1000,
  seed: 42,
  market_timezone: 'Europe/Paris',
  calendar: { until: '2028-12-31', estimated_from: null, error: null, config_unreadable: [] },
  days: weekdays('2026-11-30', '2028-11-29'),
  skipped: [
    { day: '2026-12-25', why: 'us_closed', name: 'Christmas Day', estimated: false },
    { day: '2027-01-01', why: 'us_closed', name: 'New Year’s Day', estimated: false },
  ],
  pace: {
    days_used: ['2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19', '2026-11-20', '2026-11-23'],
    growths: [1.01, 1.01, 1.01, 1.01, 1.01, 1.01],
    early_days_used: ['2026-11-16'],
    early_growths: [1.005],
    left_out: [{ day: '2026-11-26', why: 'us_closed', name: 'Thanksgiving Day', estimated: false }],
    trades_used: 12,
    partial_days: 0,
    held_past_early_sale: 0,
    geometric_mean_percent: 1,
    early_geometric_mean_percent: 0.5,
    account_return_percent: 6.15,
    sample: 'too_few',
    guard_day: null,
    emptied: false,
  },
  notes: [],
}

let answer: BacktestProjection = PROJECTION

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <ProjectionPanel run={run()} />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  answer = PROJECTION
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path === `/api/backtests/${RUN_ID}/projection`) return new Response(JSON.stringify(answer), { status: 200 })
      if (path === '/api/backtests/options') return new Response(JSON.stringify({ ...OPTIONS, capital: { value: 1000, balance: 10_250 } }), { status: 200 })
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => vi.unstubAllGlobals())

describe('ProjectionPanel', () => {
  it('draws the run’s pace a year on from its capital, the days counted and its notes', async () => {
    renderPanel()

    // Every day the same: every path is the run's average day, 1 % a day, and the early close half of it.
    const tile = (await screen.findByText('At its average day', { selector: 'dt' })).parentElement as HTMLElement
    const yearDays = PROJECTION.days!.filter((day) => day.day <= '2027-11-29')
    const expected = 1000 * 1.01 ** (yearDays.length - 1) * 1.005
    expect(within(tile).getByText(`€${expected.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)).toBeInTheDocument()
    expect(screen.getByText(/1 closes early, drawn as mornings, 2 weekdays off/)).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Weekdays off and early closes' })).toHaveTextContent('Christmas Day')
    expect(screen.getByText(/Left out of its pace: 1 day a US holiday, traded at nothing \(Thanksgiving Day/)).toBeInTheDocument()
    expect(screen.getByText(/too few to conclude/)).toBeInTheDocument()
  })

  it('switches to percent of the start, and to another horizon', async () => {
    const user = userEvent.setup()
    renderPanel()
    await screen.findByText('At its average day', { selector: 'dt' })

    await user.click(screen.getByRole('button', { name: 'Percent' }))
    await user.click(screen.getByRole('tab', { name: '3 months' }))

    const tile = screen.getByText('At its average day', { selector: 'dt' }).parentElement as HTMLElement
    expect(tile).toHaveTextContent(/^At its average day\+\d+\.\d\d%By \d+ (Feb|Mar) 2027, \d+ trading days on\./)
  })

  it('starts from the account’s balance when asked, and says it is far from the run’s capital', async () => {
    const user = userEvent.setup()
    renderPanel()
    await screen.findByText('At its average day', { selector: 'dt' })

    await user.click(await screen.findByRole('button', { name: /Use the account’s balance/ }))

    expect(screen.getByText(/The run started with €1,000.00: at €10,250.00/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back to the run’s capital' }))
    expect(screen.queryByText(/The run started with/)).not.toBeInTheDocument()
  })

  it('bends the money to a line once the stake is capped', async () => {
    const user = userEvent.setup()
    renderPanel()
    await screen.findByText('At its average day', { selector: 'dt' })
    const tile = () => screen.getByText('At its average day', { selector: 'dt' }).parentElement as HTMLElement
    const uncapped = tile().textContent

    const cap = screen.getByRole('spinbutton', { name: 'Stake at most' })
    await user.type(cap, '1000')
    await user.tab()

    expect(screen.getByText('Past it, the rest is set aside')).toBeInTheDocument()
    expect(tile().textContent).not.toBe(uncapped)
  })

  it('says why a run has no projection', async () => {
    answer = { usable: false, why_not: 'The run made no trade: there is no pace to draw', run_id: RUN_ID }
    renderPanel()

    expect(await screen.findByText('The run made no trade: there is no pace to draw')).toBeInTheDocument()
  })
})
