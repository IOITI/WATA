import { describe, expect, it } from 'vitest'

import type { DailyPerformance, WeekdayHourStats } from '@/lib/types'

import { calendarDays, tradesLine, weekdayHourGrid, weekdayName, weeksOf } from './heatmapData'

function stats(weekday: number, hour: number, trades: number, profit: number, won: number | null = 50): WeekdayHourStats {
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

describe('a weekday × hour heatmap', () => {
  it('has a row for each weekday and a column for each hour from the first traded to the last', () => {
    const grid = weekdayHourGrid([stats(2, 15, 2, 10), stats(4, 9, 1, -5.5, 0), stats(2, 11, 3, 0, 100)])

    expect(grid.weekdays).toEqual([1, 2, 3, 4, 5])
    expect(grid.hours).toEqual([9, 10, 11, 12, 13, 14, 15])
    // No trade is not a result of nothing: those cells have none.
    expect(grid.values[0]).toEqual([null, null, null, null, null, null, null])
    expect(grid.values[1]).toEqual([null, null, 0, null, null, null, 10])
    expect(grid.values[3][0]).toBe(-5.5)
    expect(grid.details[1][6]).toBe('2 trades, 50% won')
    expect(grid.details[3][0]).toBe('1 trade, 0% won')
    expect(grid.details[0][0]).toBeNull()
    expect([grid.trades, grid.total]).toEqual([6, 4.5])
  })

  it('shows a weekend day only when a trade was opened on it, and nothing without trades', () => {
    expect(weekdayHourGrid([stats(7, 10, 1, 3)]).weekdays).toEqual([1, 2, 3, 4, 5, 7])
    expect(weekdayHourGrid([stats(2, 10, 0, 0)])).toEqual({ weekdays: [], hours: [], values: [], details: [], trades: 0, total: 0 })
  })

  it('names the weekdays and says what a group of trades was', () => {
    expect([weekdayName(1), weekdayName(5), weekdayName(7)]).toEqual(['Monday', 'Friday', 'Sunday'])
    expect(tradesLine(1, 100)).toBe('1 trade, 100% won')
    expect(tradesLine(12, null)).toBe('12 trades, — won')
  })
})

describe('a calendar of the days', () => {
  it('gives each day its profit and loss and its trades', () => {
    const day: DailyPerformance = {
      day_date: '2026-10-22',
      trade_count: 3,
      win_count: 2,
      loss_count: 1,
      profit_loss: -12.4,
      cumulative_profit_loss: 100,
      performance_percent: -1.2,
    }

    expect(calendarDays([day])).toEqual([{ date: '2026-10-22', value: -12.4, detail: '3 trades, 2 won' }])
  })

  it('takes as many weeks as the period spans, Monday to Sunday', () => {
    // Thursday 1 Oct to Thursday 22 Oct 2026: the weeks of 28 Sep, 5, 12 and 19 Oct.
    expect(weeksOf('2026-10-01', '2026-10-22')).toBe(4)
    // A Monday to the Sunday after it.
    expect(weeksOf('2026-10-19', '2026-10-25')).toBe(1)
    expect(weeksOf('2026-10-19', '2026-10-26')).toBe(2)
    // Ninety days.
    expect(weeksOf('2026-07-26', '2026-10-23')).toBe(14)
    expect(weeksOf(null, '2026-10-23')).toBe(53)
  })
})
