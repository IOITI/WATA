import { ActivityHeatmap, formatMoney, formatNumber, formatPercent, SignedValue } from 'ioiti-ui'

import type { DailyPerformance, WeekdayHourStats } from '@/lib/types'

import { calendarDays, GAIN_LOSS, weekdayHourGrid, weekdayName, weeksOf } from './heatmapData'

const money = (value: number) => formatMoney(value, 'EUR', true)

const KEYS = 'The arrow keys go from one cell to the next.'

/**
 * Profit and loss by the weekday and the hour trades were opened, gains and
 * losses on two scales, with the same figures as a table under it.
 */
export function WeekdayHourHeatmap({ rows, label }: { rows: WeekdayHourStats[]; label: string }) {
  const grid = weekdayHourGrid(rows)
  if (grid.trades === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No trade to place.</p>
  }
  const listed = rows.filter((row) => row.trade_count > 0)
  return (
    <div className="flex flex-col gap-4">
      <ActivityHeatmap
        mode="hours"
        matrix={grid.values}
        details={grid.details}
        rowLabels={grid.weekdays.map((day) => weekdayName(day).slice(0, 3))}
        hours={grid.hours}
        diverging={GAIN_LOSS}
        format={money}
        emptyText="No trade opened"
        legend={['Loss', 'Profit']}
        summary={`${money(grid.total)} over ${formatNumber(grid.trades)} trade${grid.trades === 1 ? '' : 's'}`}
        ariaLabel={`${label}. ${KEYS} The figures are also in the table under it.`}
      />
      <details className="text-xs">
        <summary className="w-fit cursor-pointer rounded-md text-muted-foreground hover:text-foreground">
          The figures as a table
        </summary>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[26rem] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">Opened</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Trades</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Won</th>
                <th scope="col" className="py-2 text-right font-medium">Profit / loss</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {listed.map((row) => (
                <tr key={`${row.weekday}:${row.hour}`}>
                  <th scope="row" className="py-2 pr-4 font-normal">
                    {weekdayName(row.weekday)}, {String(row.hour).padStart(2, '0')}:00
                  </th>
                  <td className="py-2 pr-4 text-right tabular-nums">{formatNumber(row.trade_count)}</td>
                  <td className="py-2 pr-4 text-right tabular-nums">{formatPercent(row.win_rate, false, 0)}</td>
                  <td className="py-2 text-right">
                    <SignedValue value={row.profit_loss} kind="money" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  )
}

/**
 * Each day's profit and loss as a calendar, a column a week from Monday, the
 * days of the period only. The days are in the Days table of the same page.
 */
export function DayCalendar({ daily, from, to, label }: { daily: DailyPerformance[]; from: string | null; to: string | null; label: string }) {
  const last = to ?? daily[daily.length - 1]?.day_date ?? null
  const won = daily.filter((day) => day.profit_loss > 0).length
  const lost = daily.filter((day) => day.profit_loss < 0).length
  return (
    <ActivityHeatmap
      mode="calendar"
      data={calendarDays(daily)}
      weekStart={1}
      startDate={from ?? undefined}
      endDate={last ?? undefined}
      weeks={last ? weeksOf(from, last) : 53}
      diverging={GAIN_LOSS}
      format={money}
      emptyText="No trade closed"
      legend={['Loss', 'Profit']}
      summary={`${formatNumber(won)} day${won === 1 ? '' : 's'} won, ${formatNumber(lost)} lost`}
      ariaLabel={`${label}. ${KEYS} The days are also in the Days table.`}
    />
  )
}
