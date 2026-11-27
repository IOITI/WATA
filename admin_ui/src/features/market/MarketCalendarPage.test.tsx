import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router'

import { AuthContext } from '@/features/auth/auth-context'
import type { MarketCalendar, Session } from '@/lib/types'
import { marketCalendar } from '@/test/marketCalendar'
import { WithTrail } from '@/test/WithTrail'

import { MarketCalendarPage } from './MarketCalendarPage'

const SESSION: Session = {
  authenticated: true,
  version: '0.106.0',
  timezone: 'Europe/Paris',
  actions_enabled: true,
  config_edit_enabled: true,
  backtests_enabled: true,
  totp_enabled: false,
  restart_required: false,
}

let answered: MarketCalendar = marketCalendar()

beforeEach(() => {
  answered = marketCalendar()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      if (path === '/api/market-calendar') {
        return new Response(JSON.stringify(answered), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      throw new Error(`Unexpected request: ${path}`)
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderPage(at = '/market-calendar') {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[at]}>
        <AuthContext.Provider value={{ session: SESSION, error: null, login: async () => {}, logout: async () => {} }}>
          <WithTrail>
            <MarketCalendarPage />
          </WithTrail>
        </AuthContext.Provider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** A day of the list, once the page shows it. */
async function dayItem(day: string) {
  await screen.findByRole('heading', { name: 'Day by day' })
  return screen.getByRole('listitem', { name: day })
}

describe('the Market calendar page', () => {
  it('lists yesterday’s holiday and today’s early close with what WATA does then', async () => {
    renderPage()

    const thanksgiving = await dayItem('Thu 26 Nov 2026')
    expect(thanksgiving).toHaveAttribute('id', 'd-2026-11-26')
    expect(within(thanksgiving).getByText('Yesterday')).toBeInTheDocument()
    expect(within(thanksgiving).getByText('US holiday')).toBeInTheDocument()
    expect(within(thanksgiving).getByText('Thanksgiving Day')).toBeInTheDocument()
    expect(within(thanksgiving).getByText('The US market is closed: no entry all day.')).toBeInTheDocument()

    const today = await dayItem('Fri 27 Nov 2026')
    expect(within(today).getByText('Today')).toBeInTheDocument()
    expect(within(today).getByText(/closes at 13:00 New York \(19:00 Paris\): no entry from 18:30/)).toBeInTheDocument()
    // 2027's New Year is on the 2027 tab only.
    expect(screen.queryByRole('listitem', { name: 'Fri 01 Jan 2027' })).not.toBeInTheDocument()
  })

  it('says the next day off and the next release window', async () => {
    renderPage()

    // In its tile, and as its line's day.
    expect(await screen.findAllByText('Thu 24 Dec')).toHaveLength(2)
    expect(screen.getByText('the US market closes early at 13:00 New York (Christmas Eve)')).toBeInTheDocument()
    expect(screen.getAllByText('Thu 10 Dec')).toHaveLength(2)
    expect(screen.getAllByText('No entry from 14:25 to 14:45 around CPI (08:30 New York, 14:30 Paris).')).toHaveLength(2)
  })

  it('shows the year asked for, each year a link', async () => {
    renderPage('/market-calendar?year=2027')

    const newYear = await dayItem('Fri 01 Jan 2027')
    expect(within(newYear).getByText('Closed date')).toBeInTheDocument()
    expect(within(newYear).getByRole('link', { name: 'Closed dates' })).toHaveAttribute('href', '/config/hours#b-closed')
    expect(screen.getByRole('link', { name: '2026' })).toHaveAttribute('href', '/market-calendar?year=2026')
  })

  it('leaves the release windows out when asked', async () => {
    renderPage()

    await dayItem('Thu 10 Dec 2026')
    await userEvent.click(screen.getByRole('switch', { name: 'Release windows' }))
    expect(screen.queryByRole('listitem', { name: 'Thu 10 Dec 2026' })).not.toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: 'Thu 24 Dec 2026' })).toBeInTheDocument()
  })

  it('leads each blocked day of the year at a glance to its line', async () => {
    renderPage()

    const cell = await screen.findByRole('link', { name: /^26 November: Closed all day/ })
    expect(cell).toHaveAttribute('href', '#d-2026-11-26')
  })

  it('says when no window is kept around releases, and leads to where it is set', async () => {
    answered = marketCalendar({ news: { ...marketCalendar().news, on: false, events: [], known_until: null } })
    renderPage()

    const notice = await screen.findByText(/No window is kept around US releases/)
    expect(within(notice).getByRole('link', { name: 'macro_news rule' })).toHaveAttribute('href', '/config/hours#b-news')
    expect(screen.queryByRole('switch', { name: 'Release windows' })).not.toBeInTheDocument()
  })

  it('says when a calendar runs out', async () => {
    answered = marketCalendar({ us: { readable: true, until: '2026-12-31', upkeep: 'The calendar of US market days ends on 2026-12-31, in 34 day(s)' } })
    renderPage()

    expect(await screen.findByText(/ends on 2026-12-31, in 34 day\(s\)\./)).toBeInTheDocument()
  })

  it('names itself in the trail, under Trading', async () => {
    renderPage()

    await dayItem('Thu 26 Nov 2026')
    expect(screen.getAllByRole('navigation', { name: /breadcrumb/i })[0]).toHaveTextContent(/Trading.*Market calendar/)
  })
})
