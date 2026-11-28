import { ActivityHeatmap, formatMoney, formatNumber, formatPercent, SignedValue, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'

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
        aria-label={`${label}. ${KEYS} The figures are also in the table under it.`}
      />
      <details className="text-xs">
        <summary className="w-fit cursor-pointer rounded-md text-muted-foreground hover:text-foreground">
          The figures as a table
        </summary>
        <Table density="compact" wrapperClassName="mt-3" className="min-w-[26rem]">
          <TableHead>
            <TableRow>
              <TableHeaderCell scope="col">Opened</TableHeaderCell>
              <TableHeaderCell numeric scope="col">Trades</TableHeaderCell>
              <TableHeaderCell numeric scope="col">Won</TableHeaderCell>
              <TableHeaderCell numeric scope="col">Profit / loss</TableHeaderCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {listed.map((row) => (
              <TableRow key={`${row.weekday}:${row.hour}`}>
                <TableHeaderCell scope="row" className="font-normal">
                  {weekdayName(row.weekday)}, {String(row.hour).padStart(2, '0')}:00
                </TableHeaderCell>
                <TableCell numeric className="py-2">{formatNumber(row.trade_count)}</TableCell>
                <TableCell numeric className="py-2">{formatPercent(row.win_rate, false, 0)}</TableCell>
                <TableCell className="py-2 text-right">
                  <SignedValue value={row.profit_loss} kind="money" />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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
      aria-label={`${label}. ${KEYS} The days are also in the Days table.`}
    />
  )
}
