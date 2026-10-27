import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ThemeProvider } from 'ioiti-ui'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TaRule } from '@/lib/types'
import { charts, resetCharts, seriesOf } from '@/test/fakeCharts'
import { stubSystemTheme } from '@/test/systemTheme'

import { ComparisonPanel } from './ComparisonPanel'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const RULE: TaRule = {
  id: 'aaaaaaaaaaaa',
  name: 'Cross on US100',
  asset: { provider: 'capital', key: 'US100' },
  side: 'bid',
  bars: { type: 'time', timeframe: '1' },
  script: { id: 'cross', version: 1, sha256: 'x' },
  libraries: [],
  inputs: [],
  alerts: [{ title: 'Up', action: 'long' }],
  alert_calls: 'ignore',
  confidence_plot: null,
  indice: 'us100',
  mode: 'shadow',
  created_at: '2026-10-12T10:00:00+00:00',
  updated_at: '2026-10-12T10:00:00+00:00',
  mode_changed_at: '2026-10-12T10:00:00+00:00',
  script_name: 'Cross',
  latest: { script: 1, libraries: {} },
}

let sent: string[] = []

/** The comparison of the day asked for, and three minute bars of its morning. */
function serve() {
  sent = []
  const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      const url = new URL(path, 'http://admin.test')
      sent.push(`${url.pathname}${url.search}`)
      const day = url.searchParams.get('day') ?? url.searchParams.get('from') ?? ''
      if (url.pathname === `/api/ta/rules/${RULE.id}/comparison`) {
        return json({ day, indice: 'us100', tolerance_seconds: 120, rows: [], paired: 0, only_wata: 0, only_tradingview: 0 })
      }
      if (url.pathname.endsWith('/bars')) {
        const start = Date.parse(`${day}T07:30:00Z`) / 1000
        return json({ bars: [0, 1, 2].map((index) => [start + index * 60, 24_000, 24_004, 23_996, 24_002, null, null, null, null, 10]) })
      }
      return new Response(JSON.stringify({ detail: `Not served in this test: ${url.pathname}` }), { status: 404 })
    }),
  )
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter>
          <ComparisonPanel rules={[RULE]} timeZone="Europe/Paris" />
        </MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  )
}

const asked = (day: string) => sent.includes(`/api/ta/rules/${RULE.id}/comparison?day=${day}`)

beforeEach(() => {
  // Monday 19 October 2026, in the morning in Paris.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-19T08:00:00Z'))
  window.localStorage.clear()
  resetCharts()
  serve()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('the comparison with TradingView', () => {
  it('goes from weekday to weekday, the weekend stepped over, never past today', async () => {
    stubSystemTheme(true)
    const user = userEvent.setup()
    renderPanel()

    const day = screen.getByLabelText('Day')
    expect(day).toHaveValue('2026-10-19')
    expect(screen.getByRole('button', { name: 'The weekday after' })).toBeDisabled()
    await waitFor(() => expect(asked('2026-10-19')).toBe(true))

    await user.click(screen.getByRole('button', { name: 'The weekday before' }))
    expect(day).toHaveValue('2026-10-16')
    await waitFor(() => expect(asked('2026-10-16')).toBe(true))
    expect(sent.some((path) => path.includes('/bars?from=2026-10-16&to=2026-10-16'))).toBe(true)
    expect(screen.getByRole('button', { name: 'The weekday after' })).toBeEnabled()

    await user.click(screen.getByRole('button', { name: 'The weekday after' }))
    expect(day).toHaveValue('2026-10-19')
    expect(screen.getByRole('button', { name: 'The weekday after' })).toBeDisabled()
  })

  it('draws the day again in another theme, its chart drawn with the colours it read when it was made', async () => {
    const system = stubSystemTheme(true)
    renderPanel()
    await waitFor(() => expect(seriesOf(charts[0], 'Candlestick')?.data).toHaveLength(3))
    const first = charts[0]

    act(() => system.set(false))

    expect(first.removed).toBe(true)
    await waitFor(() => expect(seriesOf(charts[charts.length - 1], 'Candlestick')?.data).toHaveLength(3))
    expect(charts.filter((chart) => !chart.removed)).toHaveLength(1)
  })
})
