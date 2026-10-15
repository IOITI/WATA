import { useMemo } from 'react'

import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { formatNumber, formatSpan } from '@/lib/format'
import type { PriceBarRow, PriceRangeRow } from '@/lib/types'

import { chartTime, formatChartClock, formatChartTime, intervalLabel, RANGE } from './chartData'
import type { Side } from './chartData'
import { rangeBarState } from './chartModel'
import type { RangeChart, TimeChart } from './chartModel'

/** The same bars as the chart, as figures: what a reader who cannot use the chart reads instead. */

function timeColumns(timeZone: string, decimals: number, side: Side): ColumnDef<PriceBarRow>[] {
  const price = (header: string, index: number, shown: boolean): ColumnDef<PriceBarRow> => ({
    id: header.toLowerCase().replace(' ', '_'),
    header,
    accessorFn: (row) => row[index],
    sortable: true,
    align: 'right',
    defaultHidden: !shown,
    cell: ({ row }) => formatNumber(row[index] as number | null, decimals),
  })
  // The four prices of the side on the chart, and the close of the other; the rest is in the column list.
  const bid = side === 'bid'
  return [
    {
      id: 'time',
      header: 'Time',
      accessorFn: (row) => row[0],
      sortable: true,
      cell: ({ row }) => formatChartTime(chartTime(row[0], timeZone)),
    },
    price('Bid open', 1, bid),
    price('Bid high', 2, bid),
    price('Bid low', 3, bid),
    price('Bid close', 4, true),
    price('Ask open', 5, !bid),
    price('Ask high', 6, !bid),
    price('Ask low', 7, !bid),
    price('Ask close', 8, true),
    {
      id: 'spread',
      header: 'Spread',
      accessorFn: (row) => (row[8] === null ? null : row[8] - row[4]),
      sortable: true,
      align: 'right',
      cell: ({ value }) => formatNumber(value, decimals),
    },
    { id: 'updates', header: 'Quote changes', accessorFn: (row) => row[9], sortable: true, align: 'right' },
  ]
}

export function TimeBarsTable({ chart }: { chart: TimeChart }) {
  const newestFirst = useMemo(() => [...chart.bars].reverse(), [chart.bars])
  const columns = useMemo(
    () => timeColumns(chart.timeZone, chart.decimals, chart.side),
    [chart.timeZone, chart.decimals, chart.side],
  )
  return (
    <AdvancedDataTable<PriceBarRow>
      // The columns shown by default follow the side on the chart.
      key={chart.side}
      title={`Bars of ${intervalLabel(chart.interval)}`}
      searchPlaceholder="Search…"
      emptyTitle="No bar in this period"
      data={newestFirst}
      columns={columns}
      getRowId={(row) => String(row[0])}
      defaultPageSize={10}
      selectable={false}
    />
  )
}

// A complete bar is the rule and says nothing; the exceptions are named.
const STATES = { complete: '', growing: 'In progress', cut: 'Cut by a pause' }

function rangeColumns(chart: RangeChart): ColumnDef<PriceRangeRow>[] {
  const lastTime = chart.rows[chart.rows.length - 1]?.[RANGE.t]
  const price = (header: string, index: 1 | 2 | 3 | 4): ColumnDef<PriceRangeRow> => ({
    id: header.toLowerCase(),
    header,
    accessorFn: (row) => row[index],
    sortable: true,
    align: 'right',
    cell: ({ row }) => formatNumber(row[index], chart.decimals),
  })
  const columns: ColumnDef<PriceRangeRow>[] = [
    {
      id: 'began',
      header: 'Began',
      accessorFn: (row) => row[RANGE.began],
      sortable: true,
      cell: ({ row }) => formatChartTime(chartTime(row[RANGE.began], chart.timeZone)),
    },
    {
      id: 'ended',
      header: 'Ended',
      accessorFn: (row) => row[RANGE.ended],
      sortable: true,
      cell: ({ row }) => formatChartClock(chartTime(row[RANGE.ended], chart.timeZone)),
    },
    price('Open', 1),
    price('High', 2),
    price('Low', 3),
    price('Close', 4),
    {
      id: 'lasted',
      header: 'Lasted',
      accessorFn: (row) => row[RANGE.ended] - row[RANGE.began],
      sortable: true,
      align: 'right',
      cell: ({ value }) => formatSpan(value),
    },
    {
      id: 'state',
      header: 'State',
      accessorFn: (row) => STATES[rangeBarState(row, row[RANGE.t] === lastTime)],
      sortable: true,
    },
  ]
  if (chart.indexName) {
    columns.push({
      id: 'index',
      header: chart.indexName,
      accessorFn: (row) => row[RANGE.index],
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatNumber(row[RANGE.index], chart.indexDecimals),
    })
  }
  return columns
}

export function RangeBarsTable({ chart }: { chart: RangeChart }) {
  const newestFirst = useMemo(() => [...chart.rows].reverse(), [chart.rows])
  const columns = useMemo(() => rangeColumns(chart), [chart])
  return (
    <AdvancedDataTable<PriceRangeRow>
      title={`Range bars of ${chart.size.toFixed(chart.decimals)} (${chart.ticks}R)`}
      searchPlaceholder="Search…"
      emptyTitle="No bar in this period"
      data={newestFirst}
      columns={columns}
      // The place the chart gave the bar: the one thing no two bars share.
      getRowId={(row) => String(row[RANGE.t])}
      defaultPageSize={10}
      selectable={false}
    />
  )
}
