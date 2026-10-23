import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { DailyPerformance, WeekdayHourStats } from '@/lib/types'

import { DayCalendar, WeekdayHourHeatmap } from './Heatmaps'

function stats(weekday: number, hour: number, trades: number, profit: number, won: number): WeekdayHourStats {
  return {
    weekday,
    hour,
    trade_count: trades,
    win_count: 0,
    loss_count: 0,
    win_rate: won,
    profit_loss: profit,
    average_percent: null,
    average_best_percent: null,
  }
}

function day(date: string, profit: number, trades: number, won: number): DailyPerformance {
  return {
    day_date: date,
    trade_count: trades,
    win_count: won,
    loss_count: trades - won,
    profit_loss: profit,
    cumulative_profit_loss: 0,
    performance_percent: 0,
  }
}

/** What the heatmap says of the cell the keyboard is on. */
const said = () => screen.getByRole('status').textContent

describe('profit and loss by weekday and hour', () => {
  it('reads each cell as the arrow keys reach it, and says the totals, the best and the worst', async () => {
    const user = userEvent.setup()
    render(<WeekdayHourHeatmap rows={[stats(2, 9, 2, 5, 50), stats(3, 10, 1, -2, 0)]} label="By weekday and hour" />)

    const map = screen.getByRole('img', { name: /^By weekday and hour\. The arrow keys go from one cell to the next\./ })
    expect(screen.getByText('+€3.00 over 3 trades')).toBeInTheDocument()
    expect(screen.getByText(/^Best Tue · 09:00/)).toHaveTextContent(/^Best Tue · 09:00 · \+€5.00 · worst Wed · 10:00 · −€2.00$/)

    await user.tab()
    expect(map).toHaveFocus()
    // From the last hour of the first row, Monday.
    await user.keyboard('{ArrowRight}')
    expect(said()).toBe('Mon · 10:00: No trade opened')
    await user.keyboard('{ArrowDown}{ArrowLeft}')
    expect(said()).toBe('Tue · 09:00: +€5.00, 2 trades, 50% won')
    await user.keyboard('{ArrowDown}{ArrowRight}')
    expect(said()).toBe('Wed · 10:00: −€2.00, 1 trade, 0% won')
    await user.keyboard('{Escape}')
    expect(said()).toBe('')
  })

  it('colours gains and losses on scales of their own, by size, and leaves a cell without trades grey', () => {
    const { container } = render(
      <WeekdayHourHeatmap rows={[stats(1, 9, 1, 40, 100), stats(1, 10, 1, -2, 0), stats(2, 9, 1, -40, 0)]} label="By weekday and hour" />,
    )

    const cells = Array.from(container.querySelectorAll('rect.cj-cell'))
    // Monday to Friday, 9:00 and 10:00.
    expect(cells).toHaveLength(10)
    const fill = (index: number) => cells[index].getAttribute('style') ?? ''
    // A gain and a loss of one size take the same step, each of its own colour; a smaller one a paler step.
    expect(fill(0)).toContain('fill: var(--chart-gain-2)')
    expect(fill(2)).toContain('fill: var(--chart-loss-2)')
    expect(fill(1)).toContain('fill: var(--chart-loss-1)')
    expect(fill(3)).not.toContain('fill:')
  })

  it('has its figures as a table under it', async () => {
    const user = userEvent.setup()
    render(<WeekdayHourHeatmap rows={[stats(2, 9, 2, 5, 50), stats(3, 10, 1, -2, 0)]} label="By weekday and hour" />)

    await user.click(screen.getByText('The figures as a table'))

    const row = screen.getByRole('rowheader', { name: 'Tuesday, 09:00' }).closest('tr') as HTMLElement
    expect(within(row).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['2', '50%', '+€5.00'])
    expect(screen.getByRole('rowheader', { name: 'Wednesday, 10:00' })).toBeInTheDocument()
  })

  it('says so when there is no trade to place', () => {
    render(<WeekdayHourHeatmap rows={[]} label="By weekday and hour" />)

    expect(screen.getByText('No trade to place.')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})

describe('the days as a calendar', () => {
  it('draws the days of the period only, a column a week from Monday, and reads each day', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <DayCalendar
        daily={[day('2026-10-21', 12.4, 3, 2), day('2026-10-23', -5, 1, 0)]}
        from="2026-10-21"
        to="2026-10-23"
        label="Each day"
      />,
    )

    // Wednesday to Friday of one week: nothing before the period, nothing after its end.
    expect(container.querySelectorAll('rect.cj-cell')).toHaveLength(3)
    expect(screen.getByText('1 day won, 1 lost')).toBeInTheDocument()

    await user.tab()
    // The first day there is: Monday and Tuesday are not drawn.
    await user.keyboard('{ArrowRight}')
    expect(said()).toBe('Wed, 21 Oct 2026: +€12.40, 3 trades, 2 won')
    await user.keyboard('{ArrowDown}')
    expect(said()).toBe('Thu, 22 Oct 2026: No trade closed')
    await user.keyboard('{ArrowUp}{ArrowUp}')
    expect(said()).toBe('Wed, 21 Oct 2026: +€12.40, 3 trades, 2 won')
  })

  it('names the months over their first weeks, the one the period barely starts in left out', () => {
    // Ninety days from Sunday 26 July: the first week has one day of July.
    const { container } = render(
      <DayCalendar daily={[day('2026-08-04', 3, 1, 1)]} from="2026-07-26" to="2026-10-23" label="Each day" />,
    )

    const labels = Array.from(container.querySelectorAll('svg text')).map((text) => text.textContent)
    expect(labels).toEqual(['Aug', 'Sept', 'Oct', 'Mon', 'Wed', 'Fri'])
  })
})
