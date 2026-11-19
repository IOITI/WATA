import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider, ToastProvider } from 'ioiti-ui'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { BacktestAutopsy, BacktestDecomposition, BacktestSweepPlateau } from '@/lib/types'

import { AutopsyPanel } from './AutopsyPanel'
import { CapsPanel } from './CapsPanel'
import { plateauOf } from './sweep'
import { capsSentences, exitSentences, plateauSentences, tailRows } from './why'

const AXES = [{ kind: 'input' as const, name: 'in_23', values: [15, 18, 20, 22] }]

const PLATEAU: BacktestSweepPlateau = {
  scores: [5, 5, 4, -8], best: 2, robust: 0, best_values: [20], robust_values: [15], best_total: 112.4, robust_total: 5,
  best_neighbours_median: -8, grid_median: 4.5, knife_edge: true, best_held_out: 9, robust_held_out: 1,
  robust_walk_forward: { total_percent: 3.2, days: 7 }, walk_forward_percent: -14.1,
}

const FIGURES = { days: 22, losing_share: 0, at_or_under: { '-5': 0, '-10': 0, '-25': 0 }, p1: 2.8, p5: 2.8, median: 3, worst: 2.8, at_target_share: 100 }
const DECOMPOSITION: BacktestDecomposition = {
  version: 1, twin: '20261119-120000-bbbbbb',
  with_caps: { entries: 112.44, random: 20, trades: 34 }, without_caps: { entries: -12.5, random: -30, trades: 90 },
  caps_add: 124.94, entries_add_with_caps: 92.44, entries_add_without_caps: 17.5,
  tail: {
    draws: 10000, days_drawn_from: 22, levels: [-5, -10, -25], observed: FIGURES,
    bounds: { best_first: { ...FIGURES, losing_share: 18.2, at_or_under: { '-5': 12, '-10': 6.1, '-25': 1.4 } }, results: { ...FIGURES, losing_share: 30.5 } },
  },
}

describe('why a run wins or loses, in words', () => {
  it('says a knife edge, the robust pick and the walk-forward on plateaus', () => {
    const said = plateauSentences(PLATEAU, AXES)

    expect(said[0]).toBe('Its best, input in_23 20 (+112.40%), stands on a knife edge: its neighbours made −8.00% at the median. One step away, most of it is gone.')
    expect(said[1]).toMatch(/^The robust pick, the best among its neighbours, is input in_23 15: \+5\.00%, \+1\.00% on the days held out/)
    expect(said[2]).toBe('Chosen on plateaus window after window: +3.20% over 7 days, against −14.10% chosen on peaks.')
  })

  it('speaks of no plateau when the best lost', () => {
    const lost = { ...PLATEAU, best_total: -41.78, best_neighbours_median: -51.02, knife_edge: false }

    expect(plateauSentences(lost, AXES)).toEqual(['Its best, input in_23 20, made −41.78%: no gain for its neighbours to keep.'])
  })

  it('works out the plateau as the server does, a knife edge from neighbours under half the best', () => {
    const grid = { axes: AXES, cells: [5, 4, 112.4, -20].map((total, index) => ({ values: [AXES[0].values[index]], tuning: { total_percent: total, days: 15 }, held_out: null, whole: null })) }

    expect(plateauOf(grid)).toMatchObject({ best: 2, knifeEdge: true, bestNeighboursMedian: -8 })
    const broad = { ...grid, cells: grid.cells.map((cell, index) => ({ ...cell, tuning: { total_percent: [3, 2.8, 3.1, 2.9][index], days: 15 } })) }
    expect(plateauOf(broad)).toMatchObject({ best: 2, knifeEdge: false })
  })

  it('says the exits, the caps and the tail', () => {
    expect(exitSentences({ losers: 10, losers_up_first: { '1': 60, '2': 30, '3': 10 }, winners: 24, winners_down_first: { '3': 12.5, '5': 4.2 } })).toEqual([
      'Of its 10 losing trades, 60.0 % had first been up 1 %, 30.0 % 2 %, 10.0 % 3 %.',
      'Of its 24 winning trades, 12.5 % had first been down 3 %, 4.2 % 5 %.',
    ])
    expect(capsSentences(DECOMPOSITION)[2]).toBe('The caps add +124.94%; its entries add +92.44% over random ones with the caps, +17.50% without them.')
    const rows = tailRows(DECOMPOSITION.tail)
    expect(rows.map((row) => row.label)).toEqual(['Days that lost', 'Days at −5 % or worse', 'Days at −10 % or worse', 'Days at −25 % or worse', 'The worst 1 % of days at', 'Days at their target'])
    expect(rows[1]).toMatchObject({ observed: '0.0 %', bestFirst: '12.0 %' })
  })
})

const AUTOPSY: BacktestAutopsy = {
  version: 1, trades: 34, days: 22, too_few: null, choose_days: ['2026-10-15', '2026-11-03'], test_days: ['2026-11-04', '2026-11-13'],
  fdr_q: 0.1, left_out: ['its outcome'], held: [{ name: 'minutes_after_open', said: 'Minutes after the US open above 30', q: 0.04, sentence: 'Minutes after the US open above 30: +2.10 points a trade on the later days (8 trades against 6, q 0.04). A hypothesis to test in an experiment, with the days it came from named.' }],
  features: [{ name: 'minutes_after_open', label: 'Minutes after the US open', kind: 'number', known: 34, cut: 30, said: 'Minutes after the US open above 30', chosen: { in: 12, difference: 2.5 }, tested: { in: 8, out: 6, mean_in: 1.2, mean_out: -0.9, difference: 2.1 }, p: 0.004, q: 0.04, same_sign: true, held: true }],
  exits: { losers: 10, losers_up_first: { '1': 60, '2': 30, '3': 10 }, winners: 24, winners_down_first: { '3': 12.5, '5': 4.2 } },
}

let sent: { method: string; path: string }[] = []
let decomposition: BacktestDecomposition | null = null

function serve() {
  sent = []
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal('fetch', vi.fn(async (path: string, init: RequestInit = {}) => {
    const method = init.method ?? 'GET'
    sent.push({ method, path })
    if (path.endsWith('/autopsy')) return json(AUTOPSY)
    if (path.endsWith('/decomposition')) return json({ decomposition, making: null })
    if (path.endsWith('/caps-off')) return json({ id: '20261119-120000-bbbbbb' })
    return json({})
  }))
}

function shown(element: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/', element }], { initialEntries: ['/'] })
  render(
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('the panels of a run', () => {
  it('says what held between winners and losers, and the exits', async () => {
    serve()
    shown(<AutopsyPanel runId="20261114-113341-aa2d8f" />)

    expect(await screen.findByText(/Minutes after the US open above 30: \+2\.10 points/)).toBeInTheDocument()
    expect(screen.getByText('Of its 10 losing trades, 60.0 % had first been up 1 %, 30.0 % 2 %, 10.0 % 3 %.')).toBeInTheDocument()
  })

  it('asks for the twin without the caps, then says what they add', async () => {
    const user = userEvent.setup()
    decomposition = null
    serve()
    shown(<CapsPanel runId="20261114-113341-aa2d8f" />)

    await user.click(await screen.findByRole('button', { name: "Make it again without the day's caps" }))
    await waitFor(() => expect(sent.some((request) => request.method === 'POST' && request.path.endsWith('/caps-off'))).toBe(true))

    decomposition = DECOMPOSITION
    serve()
    shown(<CapsPanel runId="20261114-113341-aa2d8f" />)
    expect(await screen.findByText(/The caps add \+124\.94%/)).toBeInTheDocument()
    expect(screen.getAllByText('Days at −25 % or worse').length).toBeGreaterThan(0)
  })
})
