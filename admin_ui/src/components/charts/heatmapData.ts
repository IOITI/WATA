import { formatNumber, formatPercent } from 'ioiti-ui'
import type { DivergingColors, HeatDay } from 'ioiti-ui'

import type { DailyPerformance, WeekdayHourStats } from '@/lib/types'

/** Gains and losses by size, smallest first: ioiti-ui's `theme.css` says how they were checked for colour-blind readers. */
export const GAIN_LOSS: DivergingColors = {
  positive: [1, 2, 3, 4].map((step) => `var(--chart-gain-${step})`),
  negative: [1, 2, 3, 4].map((step) => `var(--chart-loss-${step})`),
}

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

/** An ISO weekday's name (1 is Monday). */
export function weekdayName(weekday: number): string {
  return WEEKDAYS[weekday - 1] ?? String(weekday)
}

/** "3 trades, 67% won": what a group of trades was, under its result. */
export function tradesLine(trades: number, won: number | null): string {
  return `${formatNumber(trades)} trade${trades === 1 ? '' : 's'}, ${formatPercent(won, false, 0)} won`
}

export interface WeekdayHourGrid {
  /** ISO weekdays, a row each. */
  weekdays: number[]
  /** A column each. */
  hours: number[]
  /** Profit and loss by row and column; null where no trade was opened. */
  values: (number | null)[][]
  details: (string | null)[][]
  trades: number
  total: number
}

/**
 * The rows and columns of a weekday × hour heatmap: Monday to Friday, and a
 * weekend day only if a trade was opened on it; every hour from the first one
 * a trade was opened in to the last, those between that had none included.
 */
export function weekdayHourGrid(rows: WeekdayHourStats[]): WeekdayHourGrid {
  const traded = rows.filter((row) => row.trade_count > 0)
  if (traded.length === 0) return { weekdays: [], hours: [], values: [], details: [], trades: 0, total: 0 }
  const weekdays = [1, 2, 3, 4, 5, 6, 7].filter((day) => day <= 5 || traded.some((row) => row.weekday === day))
  const first = Math.min(...traded.map((row) => row.hour))
  const last = Math.max(...traded.map((row) => row.hour))
  const hours = Array.from({ length: last - first + 1 }, (_, index) => first + index)
  const at = new Map(traded.map((row) => [`${row.weekday}:${row.hour}`, row]))
  return {
    weekdays,
    hours,
    values: weekdays.map((day) => hours.map((hour) => at.get(`${day}:${hour}`)?.profit_loss ?? null)),
    details: weekdays.map((day) =>
      hours.map((hour) => {
        const row = at.get(`${day}:${hour}`)
        return row ? tradesLine(row.trade_count, row.win_rate) : null
      }),
    ),
    trades: traded.reduce((sum, row) => sum + row.trade_count, 0),
    total: traded.reduce((sum, row) => sum + row.profit_loss, 0),
  }
}

/** The days of a calendar heatmap: each day's profit and loss, its trades under it. */
export function calendarDays(daily: DailyPerformance[]): HeatDay[] {
  return daily.map((day) => ({
    date: day.day_date,
    value: day.profit_loss,
    detail: `${formatNumber(day.trade_count)} trade${day.trade_count === 1 ? '' : 's'}, ${formatNumber(day.win_count)} won`,
  }))
}

const DAY_MS = 86_400_000

/**
 * How many weeks (starting on Mondays) a calendar needs to show every day
 * from `from` to `to` (YYYY-MM-DD); a year's when the period has no start.
 */
export function weeksOf(from: string | null, to: string): number {
  if (!from) return 53
  const start = Date.parse(`${from}T00:00:00Z`)
  const end = Date.parse(`${to}T00:00:00Z`)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 53
  // Back to the Monday of the first day's week (getUTCDay: 0 is Sunday).
  const monday = start - ((new Date(start).getUTCDay() + 6) % 7) * DAY_MS
  return Math.floor((end - monday) / DAY_MS / 7) + 1
}
