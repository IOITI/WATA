import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthContext } from '@/features/auth/auth-context'
import type { EdgeTrack, Experiment, Experiments, ForwardCheck, ForwardCheckWhole, Session, TrackRecordGroup } from '@/lib/types'
import { WithTrail } from '@/test/WithTrail'

import { criterionText, experimentsTab, familyTrials, forwardSentence, lockboxText, recordSentence } from './experiments'
import { ExperimentsPage } from './ExperimentsPage'
import { ForwardCheckPage } from './ForwardCheckPage'

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

function experiment(changes: Partial<Experiment> = {}): Experiment {
  return {
    id: '1a2b3c', title: 'EMA cross on 5 min', hypothesis: 'A cross of the fast average leads the next hour', family: 'ema-cross',
    plan: 'Run, sweep the lengths, stress the best', made_by: 'claude', opened_at: '2026-10-20T08:00:00Z',
    runs: [{ run: '20261020-080500-aaaaaa', kind: 'run', trials: 1, lockbox: false, note: '', at: '2026-10-20T08:05:00Z' }],
    elsewhere: [], trials: 1, verdict: null, notes: null, concluded_at: null, ...changes,
  }
}

describe('the experiment journal, read', () => {
  it('counts every configuration a family of ideas was tried with', () => {
    const trials = familyTrials([experiment({ trials: 13 }), experiment({ id: 'x', trials: 2 }), experiment({ id: 'y', family: 'rsi', trials: 4 })])

    expect([...trials]).toEqual([['ema-cross', 15], ['rsi', 4]])
  })

  it('says the lock box in words', () => {
    expect(lockboxText(null)).toMatch(/No lock box/)
    expect(lockboxText({ from: '2026-10-14', to: null, set_at: null, set_by: 'claude' })).toBe('Every day from 14 Oct 2026 is kept out.')
    expect(lockboxText({ from: '2026-10-14', to: '2026-10-16', set_at: null, set_by: null })).toBe('14 Oct 2026 to 16 Oct 2026 are kept out.')
  })
})

let journal: Experiments
let sent: { method: string; path: string; body: unknown }[] = []

const TRACK: EdgeTrack = {
  verdict: 'watch', reasons: ['the test is past half of its threshold'], n: 12, cumulative: -3.5, rank: 20, rank_bound: null,
  drawdown: -6.1, drawdown_rank: 12, drawdown_bound: null, streak: 3, streak_rank: 30, cusum: 2.1, cusum_top: 2.1, h: 4,
  alarm_at: null, tested: 12, trades_to_tell: 40, progress: 0.3, past_horizon: false, reference: null, run_id: '20261114-113341-aa2d8f',
  settings_differ: [], path: [],
} as unknown as EdgeTrack

const CHECK: ForwardCheck = {
  id: 'f1234abcd', experiment: '1a2b3c', title: 'EMA cross on 5 min', family: 'ema-cross', made_via: 'agent:abc',
  candidate: '20261114-113341-aa2d8f', first_candidate: '20261114-113341-aa2d8f', start: '2026-11-17', state: 'active',
  started_at: '2026-11-14T12:00:00Z', by: 'agent:abc', ended_at: null, why: null, last: null, judgement: TRACK, days_tested: 4,
}

function group(changes: Partial<TrackRecordGroup> = {}): TrackRecordGroup {
  return {
    experiments: 4, trials: 120, open: 0, kept: 2, rejected: 1, inconclusive: 1, kept_met_bar: 1, kept_against_bar: 1,
    forward: { active: 1, held: 3, watch: 1, broken: 1, too_early: 1, nothing_to_judge: 0 }, held_share: 60, ...changes,
  }
}

function serve() {
  sent = []
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? 'GET'
      const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined
      sent.push({ method, path, body })
      if (path === '/api/research/experiments') return json(journal)
      if (path === '/api/research/forward' && method === 'GET') return json({ checks: [CHECK], active: 1, most: 10 })
      if (path === '/api/research/forward' && method === 'POST') return json(CHECK)
      if (path === '/api/research/forward/f1234abcd') {
        const whole: ForwardCheckWhole = {
          ...CHECK, path: [{ n: 1, at: '2026-11-17T10:00:00Z', result: -1.2, cumulative: -1.2 }], days: [['2026-11-17', -1.2, 1]],
          history: [{ to: '2026-11-18', run: '20261118-230000-bbbbbb', n: 12, verdict: 'watch', cumulative: -3.5, rank: 20, cusum: 2.1, h: 4, days_tested: 4, at: '2026-11-18T22:30:00Z' }],
          model: null, cone: null,
        }
        return json(whole)
      }
      if (path === '/api/research/forward/f1234abcd/stop') return json({ ...CHECK, state: 'ended', why: 'stopped' })
      if (path === '/api/research/lockbox') {
        const { start, end } = body as { start: string | null; end: string | null }
        journal = { ...journal, lockbox: start ? { from: start, to: end, set_at: '2026-10-20T09:00:00Z', set_by: 'operator' } : null }
        return json({ lockbox: journal.lockbox })
      }
      return json({ detail: `Not served: ${method} ${path}` }, 404)
    }),
  )
}

function renderPage(at = '/backtests/experiments') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/backtests/experiments', element: <WithTrail><ExperimentsPage /></WithTrail> },
      { path: '/backtests/experiments/forward/:checkId', element: <WithTrail><ForwardCheckPage /></WithTrail> },
    ],
    { initialEntries: [at] },
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
}

beforeEach(() => {
  journal = {
    experiments: [
      experiment({
        id: '4d5e6f', title: 'RSI bounce', family: 'rsi', runs: [], trials: 35,
        elsewhere: [{ trials: 35, note: 'A Python port screened on the Mac, 12 Jun to 16 Oct', at: '2026-10-20T09:30:00Z' }],
      }),
      experiment({ verdict: 'rejected', notes: 'Did not beat random entries (61 %)', concluded_at: '2026-10-20T10:00:00Z' }),
    ],
    lockbox: { from: '2026-10-14', to: null, set_at: '2026-10-19T20:00:00Z', set_by: 'claude' },
  }
  serve()
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the Experiments page', () => {
  it('lists each experiment with its runs and conclusion, under Backtests', async () => {
    renderPage()

    expect(await screen.findByText('RSI bounce')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '20261020-080500-aaaaaa' })).toHaveAttribute('href', '/backtests/20261020-080500-aaaaaa')
    const concluded = screen.getByText('Did not beat random entries (61 %)').closest('tr') as HTMLElement
    expect(within(concluded).getByText('Rejected')).toBeInTheDocument()
    // Every run of it, in the list of runs narrowed to it.
    expect(within(concluded).getByRole('link', { name: '1 run' })).toHaveAttribute('href', '/backtests?experiment=1a2b3c')
    expect(screen.getByText('Every day from 14 Oct 2026 is kept out.')).toBeInTheDocument()
    // Trials made outside the runs: said with what they were, without a link, and counted in the family.
    const elsewhere = screen.getByText('+35 tried elsewhere: A Python port screened on the Mac, 12 Jun to 16 Oct')
    expect(elsewhere.closest('a')).toBeNull()
    expect(within(elsewhere.closest('tr') as HTMLElement).getByText('35 tried in all')).toBeInTheDocument()
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Analysis.*Backtests.*Experiments/)
  })

  it('lets the operator move the lock box, and clear it', async () => {
    const user = userEvent.setup()
    renderPage()

    const end = await screen.findByLabelText('To (empty: every day after)')
    await user.type(end, '2026-10-16')
    await user.click(screen.getByRole('button', { name: 'Save the lock box' }))
    expect(await screen.findByText('14 Oct 2026 to 16 Oct 2026 are kept out.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear it' }))

    await waitFor(() =>
      expect(sent.filter((request) => request.method === 'PUT').map((request) => request.body)).toEqual([
        { start: '2026-10-14', end: '2026-10-16' },
        { start: null, end: null },
      ]),
    )
    expect(await screen.findByText(/No lock box/)).toBeInTheDocument()
  })
})


describe('what an experiment rests on, and how it held on later days', () => {
  it('says a forward check, a criterion, a record and a tab in words', () => {
    expect(forwardSentence({ state: 'active', why: null, verdict: 'watch', trades: 12, cumulative: -3.5, days_tested: 4 })).toBe(
      'Worth watching after 12 trades, −3.50% on 4 days',
    )
    expect(forwardSentence({ state: 'ended', why: 'broken', verdict: 'broken', trades: 16, cumulative: -21, days_tested: 4 })).toMatch(
      /^Ended \(judged broken\): Broken after 16 trades/,
    )
    expect(forwardSentence({ state: 'active', why: null, verdict: null, trades: 0, cumulative: null, days_tested: 0 })).toBe('Nothing judged yet')
    expect(criterionText({ figure: 'edge_t', bound: 'at_least', value: 2.6 })).toBe('edge t at least 2.6')
    expect(recordSentence(group())).toBe('2 kept of 4 experiments; 1 against the bar; later days: 3 held, 1 on watch, 1 broken; 60 % held')
    expect(experimentsTab('record')).toBe('record')
    expect(experimentsTab('nothing')).toBe('journal')
  })

  it('shows the candidate a conclusion kept, how it stood, and its forward check', async () => {
    journal = {
      ...journal,
      experiments: [
        experiment({
          verdict: 'kept', notes: 'On 22 days', concluded_at: '2026-11-14T12:00:00Z', candidate: '20261114-113341-aa2d8f',
          checks: { bar: 'fail', failed: ['edge', 'family'], open: [] }, motivated_by: ['2026-11-11', '2026-11-12'],
          criteria: [{ figure: 'held_out_percent', bound: 'at_least', value: 0 }],
          forward_check: { id: 'f1234abcd', state: 'active', why: null, candidate: '20261114-113341-aa2d8f', start: '2026-11-17', verdict: 'watch', trades: 12, cumulative: -3.5, days_tested: 4 },
        }),
      ],
      forward: { active: 1, most: 10 },
    }
    renderPage()

    const row = (await screen.findByText('Kept against the bar')).closest('tr') as HTMLElement
    expect(within(row).getByText('Failed: edge, family')).toBeInTheDocument()
    expect(within(row).getByText(/Its idea came from 11 Nov 2026, 12 Nov 2026/)).toBeInTheDocument()
    expect(within(row).getByText('Set before any run: held out percent at least 0.')).toBeInTheDocument()
    expect(within(row).getByText('Worth watching after 12 trades, −3.50% on 4 days')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'Judged' })).toHaveAttribute('href', '/backtests/experiments/forward/f1234abcd')
  })

  it('lists the forward checks on their tab, and starts one for an older conclusion', async () => {
    const user = userEvent.setup()
    journal = { ...journal, experiments: [experiment({ id: '9z9z9z', title: 'Gap fill', verdict: 'kept', concluded_at: '2026-11-01T10:00:00Z' })] }
    renderPage('/backtests/experiments?tab=forward')

    expect(await screen.findByRole('link', { name: 'EMA cross on 5 min' })).toHaveAttribute('href', '/backtests/experiments/forward/f1234abcd')
    expect(screen.getByText(/1 running of 10 at most/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Experiment'), '9z9z9z')
    await user.selectOptions(screen.getByLabelText('Its run to judge'), '20261020-080500-aaaaaa')
    await user.click(screen.getByRole('button', { name: 'Start its forward check' }))

    await waitFor(() =>
      expect(sent.filter((request) => request.method === 'POST').map((request) => request.body)).toEqual([
        { experiment: '9z9z9z', candidate: '20261020-080500-aaaaaa' },
      ]),
    )
  })

  it('shows the track record on its tab', async () => {
    journal = {
      ...journal,
      track_record: { families: [{ ...group(), family: 'kernel-avg-vwap' }], makers: [{ ...group(), maker: 'the AI agent' }], total: group() },
    }
    renderPage('/backtests/experiments?tab=record')

    expect(await screen.findByText('kernel-avg-vwap')).toBeInTheDocument()
    expect(screen.getByText('the AI agent')).toBeInTheDocument()
    expect(screen.getAllByText(/3 held, 1 on watch, 1 broken/).length).toBeGreaterThan(0)
  })

  it('opens a forward check with its verdict, its evenings, and stops it', async () => {
    const user = userEvent.setup()
    renderPage('/backtests/experiments/forward/f1234abcd')

    expect(await screen.findByText('The test is past half of its threshold.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '20261118-230000-bbbbbb' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Stop it' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Stop it' }))

    await waitFor(() => expect(sent.some((request) => request.path === '/api/research/forward/f1234abcd/stop')).toBe(true))
    const trail = screen.getAllByRole('navigation', { name: 'Breadcrumb' })[0]
    expect(trail).toHaveTextContent(/Backtests.*Experiments/)
  })

  it('says a forward check that is not there', async () => {
    renderPage('/backtests/experiments/forward/f00000000')

    expect(await screen.findByRole('heading', { name: 'No such forward check' })).toBeInTheDocument()
  })
})
