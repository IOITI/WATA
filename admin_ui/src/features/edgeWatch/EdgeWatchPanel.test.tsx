import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import type { EdgeOwner } from '@/lib/types'

import { EdgeWatchPanel } from './EdgeWatchPanel'

const LEVELS = [1, 2.5, 5, 10, 25, 50, 75, 90, 95, 97.5, 99]
const GRID = Array.from({ length: 60 }, (_, index) => index + 1)

// The live trades against their reference: 40 trades that lost as random entries do.
const LIVE: EdgeOwner = {
  owner: 'live',
  name: 'the live trades',
  mode: null,
  reference: { run_id: '20261101-120000-aaaaaa', name: 'Replay of October', done: true, days: 22, first_day: '2026-10-01', last_day: '2026-10-31', stale: false, made_with: '0.85.0' },
  since: '2026-11-01',
  model: {
    usable: true, why_not: null,
    reference: { days: 22, trades: 48, per_day: 2.2, mean: 0.9, sd: 4.1, t: 1.5, sample: 'few', h1: 'random', h1_mean: -1.2 },
    test: { alpha: 0.05, h: 4.2, trades_to_tell: 74, detect: { median: 41, p80: 74, within_horizon: 0.92, followed: 1056 } },
    horizon: 264,
  },
  verdict: 'broken',
  main: 'real',
  cone: {
    levels: LEVELS,
    n: GRID,
    cumulative: GRID.map((n) => LEVELS.map((level) => (level - 50) * 0.08 * Math.sqrt(n) + 0.9 * n)),
    drawdown: GRID.map(() => LEVELS.map(() => 0)),
    streak_at_least: GRID.map(() => [80, 40, 10]),
  },
  tracks: {
    real: {
      verdict: 'broken', reasons: ["its trades look like random entries' rather than its reference's: the test passed its threshold at trade 33"],
      n: 40, tested: 40, cumulative: -24.6, rank: 1, rank_bound: 'at_most', drawdown: -27.3, drawdown_rank: 2, streak: 6, streak_rank: 3,
      cusum: 5.1, h: 4.2, alarm_at: 33, trades_to_tell: 74, progress: 0.54, settings_differ: [], sample: 'few',
      path: GRID.slice(0, 40).map((n) => ({ n, at: '2026-11-03T10:00:00Z', result: -0.7, cumulative: -0.62 * n })),
    },
    paper: {
      verdict: 'watch', reasons: [], n: 12, cumulative: -6.1, rank: 4, drawdown: -8, drawdown_rank: 6, streak: 3, streak_rank: 20,
      cusum: 2.4, h: 4.2, alarm_at: null, trades_to_tell: 74, progress: 0.16, settings_differ: [],
    },
  },
  guard: { on: true, least_trades: 20 },
  judged_at: '2026-11-16T12:00:00Z',
}

function renderPanel(owner: string, answer: { status: number; body: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(answer.body), { status: answer.status, headers: { 'Content-Type': 'application/json' } })),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EdgeWatchPanel owner={owner} enabled />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** A rule's panel: its own answer, and the list's (whether the live reference judges its real trades meanwhile). */
function renderRulePanel(rule: string, answer: { status: number; body: unknown }, list: unknown, liveRule: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      const [status, body] = url.includes(`/api/edge-watch/${rule}`) ? [answer.status, answer.body] : [200, list]
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EdgeWatchPanel owner={rule} enabled liveRule={liveRule} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const RULE = '0123456789ab'

afterEach(() => vi.unstubAllGlobals())

describe('Edge Watch’s panel', () => {
  it('says the live trades are broken, why, how unusual, and draws them over their reference’s cone', async () => {
    renderPanel('live', { status: 200, body: LIVE })

    expect(await screen.findByText('Broken after 40 trades, −24.60%')).toBeInTheDocument()
    const panel = screen.getByRole('heading', { name: 'Edge Watch: broken, or just unlucky?' }).closest('section') as HTMLElement
    // Its verdict beside its heading, and the trade the test broke at marked on the chart.
    expect(within(panel).getAllByText('Broken')).toHaveLength(2)
    expect(within(panel).getByText(/the test passed its threshold at trade 33/)).toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Replay of October' })).toHaveAttribute('href', '/backtests/20261101-120000-aaaaaa')
    // The figures that say how unusual the trades are.
    expect(within(panel).getByText('Trades judged')).toBeInTheDocument()
    expect(within(panel).getByText('40 of the 74 trades it takes to tell a dead edge, 80 % of the time.')).toBeInTheDocument()
    expect(within(panel).getByText('1 % or fewer of the reference’s paths did as badly or worse after 40 trades.')).toBeInTheDocument()
    // The cone and the path over it, for screen readers too.
    const table = within(panel).getByRole('table', { name: 'Real trades against where its reference’s paths went' })
    expect(within(table).getByRole('columnheader', { name: 'Trades since its reference' })).toBeInTheDocument()
    expect(within(panel).getByText(/signals received, on paper: worth watching after 12 trades/)).toBeInTheDocument()
    expect(within(panel).getByText(/The edge guard is on/)).toBeInTheDocument()
  })

  it('says how to choose a live reference while there is none', async () => {
    renderPanel('live', { status: 404, body: { detail: 'No live reference' } })

    expect(await screen.findByText(/No live reference: on the page of a replay/)).toBeInTheDocument()
  })

  it('says what makes a verdict mean less: a thin reference, settings that differ', async () => {
    const thin: EdgeOwner = {
      ...LIVE,
      model: { ...LIVE.model!, reference: { ...LIVE.model!.reference, sample: 'too_few' } },
      tracks: { real: { ...LIVE.tracks.real!, verdict: 'as_expected', alarm_at: null, settings_differ: ['stoploss_percent'] } },
    }
    renderPanel('live', { status: 200, body: thin })

    expect(await screen.findByText(/fewer than 30 trades or 20 days: the edge guard pauses nothing on it/)).toBeInTheDocument()
    expect(screen.getByText(/The live settings are not its reference’s \(stoploss_percent\)/)).toBeInTheDocument()
  })

  it('judges a live rule’s real trades against its own reference, its paper trades beside them', async () => {
    const rule: EdgeOwner = {
      ...LIVE,
      owner: RULE,
      name: 'Kernel VWAP',
      mode: 'live',
      since: '2026-10-28',
      tracks: { real: { ...LIVE.tracks.real!, since: '2026-11-03', started_at: '2026-11-03T14:00:00+00:00' }, paper: LIVE.tracks.paper },
    }
    renderRulePanel(RULE, { status: 200, body: rule }, { live: null, rules: {}, judged_at: '', guard: { on: true, least_trades: 20 } }, true)

    expect(await screen.findByText('Broken after 40 trades, −24.60%')).toBeInTheDocument()
    const panel = screen.getByRole('heading', { name: 'Edge Watch: broken, or just unlucky?' }).closest('section') as HTMLElement
    expect(within(panel).getByText(/Its real trades since it went live and since its reference/)).toBeInTheDocument()
    expect(within(panel).getByText(/Judged against/)).toHaveTextContent('; real trades since 03 Nov 2026.')
    expect(within(panel).getByRole('table', { name: 'Real trades against where its reference’s paths went' })).toBeInTheDocument()
    expect(within(panel).getByText(/On paper since .*: worth watching after 12 trades/)).toBeInTheDocument()
    expect(within(panel).getByText(/The edge guard is on: a broken verdict on 20 real trades or more pauses/)).toBeInTheDocument()
  })

  it('tells a live rule without a reference how to get one, and who judges its real trades meanwhile', async () => {
    const list = { live: { ...LIVE, cone: undefined }, rules: {}, unwatched: [{ id: RULE, name: 'Kernel VWAP' }], judged_at: '', guard: { on: false, least_trades: 20 } }
    renderRulePanel(RULE, { status: 404, body: { detail: 'This rule has no reference run' } }, list, true)

    const link = await screen.findByRole('link', { name: 'Make a run from the rule' })
    expect(link).toHaveAttribute('href', `/backtests/new?rule=${RULE}`)
    expect(screen.getByText(/the edge guard cannot act on them/)).toBeInTheDocument()
    expect(await screen.findByText(/Meanwhile its real trades are judged with WATA’s other real trades/)).toBeInTheDocument()
  })

  it('tells a rule in shadow without a reference what one would judge, its paper trades', async () => {
    renderRulePanel(RULE, { status: 404, body: { detail: 'This rule has no reference run' } }, {}, false)

    expect(await screen.findByText(/Edge Watch judges its paper trades against it/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Make a run from the rule' })).not.toBeInTheDocument()
  })
})
