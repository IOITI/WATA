import { keepPreviousData, skipToken, useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { SpreadByTimeChart } from '@/components/charts/PerformanceCharts'
import { ExportButton } from '@/components/ExportButton'
import { KpiTile } from '@/components/KpiTile'
import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { daysBetween } from '@/features/prices/chartData'
import { api } from '@/lib/api'
import type { CsvColumn } from '@/lib/csv'
import { formatDay, formatNumber, formatPercent, formatTime } from '@/lib/format'
import type { AssetDayStatistics, AssetLimits, DataAsset } from '@/lib/types'
import { cn } from '@/lib/utils'

import { dearestSlot, periodFigures, statisticsDecimals } from './statistics'

interface StatisticsTabProps {
  asset: DataAsset
  from: string
  to: string
  limits: AssetLimits
  timeZone: string
}

function signed(value: number | null, decimals: number): string {
  if (value === null) return '—'
  return `${value > 0 ? '+' : ''}${formatNumber(value, decimals)}`
}

function dayColumns(decimals: number, timeZone: string, spread: boolean, volume: boolean): ColumnDef<AssetDayStatistics>[] {
  const price = (value: number | null) => (value === null ? '—' : formatNumber(value, decimals))
  const columns: ColumnDef<AssetDayStatistics>[] = [
    { id: 'day', header: 'Day', accessorKey: 'day', sortable: true, cell: ({ row }) => formatDay(row.day) },
    { id: 'open', header: 'Open', accessorKey: 'open', align: 'right', cell: ({ row }) => price(row.open) },
    { id: 'high', header: 'High', accessorKey: 'high', align: 'right', cell: ({ row }) => price(row.high) },
    { id: 'low', header: 'Low', accessorKey: 'low', align: 'right', cell: ({ row }) => price(row.low) },
    { id: 'close', header: 'Close', accessorKey: 'close', align: 'right', cell: ({ row }) => price(row.close) },
    {
      id: 'change',
      header: 'Change',
      accessorKey: 'change_percent',
      sortable: true,
      align: 'right',
      // Told by its sign, not by a colour: a price that fell is not a loss here.
      cell: ({ row }) => `${signed(row.change, decimals)} (${formatPercent(row.change_percent, true)})`,
    },
    {
      id: 'range',
      header: 'Range',
      accessorKey: 'range_percent',
      sortable: true,
      align: 'right',
      cell: ({ row }) => `${formatNumber(row.range, decimals)} (${formatPercent(row.range_percent)})`,
    },
  ]
  if (spread) {
    columns.push(
      {
        id: 'spread_average',
        header: 'Spread',
        accessorKey: 'spread_average',
        sortable: true,
        align: 'right',
        cell: ({ row }) => price(row.spread_average),
      },
      {
        id: 'spread_widest',
        header: 'Widest spread',
        accessorKey: 'spread_widest',
        sortable: true,
        align: 'right',
        // The widest of the period is in its tile: the table keeps to the width of a laptop.
        defaultHidden: true,
        cell: ({ row }) => price(row.spread_widest),
      },
    )
  }
  columns.push(
    {
      id: 'updates',
      header: 'Quote changes',
      accessorKey: 'updates',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatNumber(row.updates),
    },
    {
      id: 'covered',
      header: 'Covered',
      accessorKey: 'covered',
      sortable: true,
      align: 'right',
      cell: ({ row }) => (row.covered === null ? '—' : formatPercent(Math.floor(row.covered * 1000) / 10, false, 1)),
    },
  )
  if (volume) {
    columns.push({
      id: 'volume',
      header: 'Volume',
      accessorKey: 'volume',
      sortable: true,
      align: 'right',
      cell: ({ row }) => (row.volume === null ? 'Not recorded' : formatNumber(row.volume)),
    })
  }
  columns.push(
    { id: 'bars', header: 'Bars', accessorKey: 'bars', align: 'right', defaultHidden: true, cell: ({ row }) => formatNumber(row.bars) },
    {
      id: 'first',
      header: 'First bar',
      accessorKey: 'first',
      defaultHidden: true,
      cell: ({ row }) => formatTime(row.first, timeZone),
    },
    {
      id: 'last',
      header: 'Last bar',
      accessorKey: 'last',
      defaultHidden: true,
      cell: ({ row }) => formatTime(row.last, timeZone),
    },
  )
  return columns
}

const DAY_CSV: CsvColumn<AssetDayStatistics>[] = [
  { header: 'day', value: (row) => row.day },
  { header: 'first_bar_utc', value: (row) => row.first },
  { header: 'last_bar_utc', value: (row) => row.last },
  { header: 'bars', value: (row) => row.bars },
  { header: 'quote_changes', value: (row) => row.updates },
  { header: 'open', value: (row) => row.open },
  { header: 'high', value: (row) => row.high },
  { header: 'low', value: (row) => row.low },
  { header: 'close', value: (row) => row.close },
  { header: 'change', value: (row) => row.change },
  { header: 'change_percent', value: (row) => row.change_percent },
  { header: 'range', value: (row) => row.range },
  { header: 'range_percent', value: (row) => row.range_percent },
  { header: 'spread_average', value: (row) => row.spread_average },
  { header: 'spread_widest', value: (row) => row.spread_widest },
  { header: 'bars_without_ask', value: (row) => row.without_ask },
  { header: 'covered', value: (row) => row.covered },
  { header: 'volume', value: (row) => row.volume },
]

/**
 * Each day of the period summed up from its ten-second bars, and the spread
 * through the day: what a turbo costs to trade, and when.
 */
export function StatisticsTab({ asset, from, to, limits, timeZone }: StatisticsTabProps) {
  const tooLong = daysBetween(from, to) > limits.statistics
  const query = useQuery({
    queryKey: ['data-statistics', asset.ref, from, to],
    queryFn: tooLong ? skipToken : () => api.assetStatistics(asset.ref, { from, to }),
    placeholderData: keepPreviousData,
  })
  const statistics = query.data
  const days = useMemo(() => statistics?.days ?? [], [statistics])
  const figures = useMemo(() => periodFigures(days), [days])
  const decimals = statisticsDecimals(days)
  const volume = days.some((day) => day.volume !== null)
  const columns = useMemo(
    () => dayColumns(decimals, timeZone, statistics?.spread ?? false, volume),
    [decimals, timeZone, statistics?.spread, volume],
  )
  const dearest = statistics ? dearestSlot(statistics.spread_by_time) : null

  if (tooLong) {
    return (
      <Panel title="Statistics">
        <p className="text-sm text-muted-foreground">
          Statistics read every ten-second bar of the period: they are worked out for {limits.statistics} days at most.
          Choose a shorter period above.
        </p>
      </Panel>
    )
  }

  return (
    <QueryState isPending={query.isPending} error={query.error} hasData={!!statistics}>
      {statistics && (
        <div className={cn('flex flex-col gap-6 transition-opacity', query.isPlaceholderData && 'opacity-60')}>
          {days.length === 0 ? (
            <Panel title="Statistics">
              <p className="text-sm text-muted-foreground">No bar of this asset in the period.</p>
            </Panel>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <KpiTile
                  label="Change over the period"
                  value={signed(figures.change, decimals)}
                  hint={`${formatPercent(figures.changePercent, true)} from the first open to the last close`}
                />
                <KpiTile
                  label="Range of a day, on average"
                  value={figures.averageRange === null ? '—' : formatNumber(figures.averageRange, decimals)}
                  hint={`${formatPercent(figures.averageRangePercent)} of its open, over ${figures.days} day${figures.days === 1 ? '' : 's'}`}
                />
                <KpiTile
                  label="Spread, on average"
                  value={figures.spreadAverage === null ? '—' : formatNumber(figures.spreadAverage, decimals)}
                  hint={
                    statistics.spread
                      ? `Widest ${figures.spreadWidest === null ? '—' : formatNumber(figures.spreadWidest, decimals)}${
                          asset.provider === 'dukascopy' ? ': Dukascopy’s, not Saxo’s' : ''
                        }`
                      : 'Saxo quotes the index with one price'
                  }
                />
                <KpiTile
                  label="Trading time covered"
                  value={figures.covered === null ? '—' : formatPercent(Math.floor(figures.covered * 1000) / 10, false, 1)}
                  hint={`${formatNumber(figures.updates)} quote changes${figures.volume === null ? '' : `, volume ${formatNumber(figures.volume)}`}`}
                />
              </div>

              <AdvancedDataTable<AssetDayStatistics>
                title="Day by day"
                data={days}
                columns={columns}
                getRowId={(row) => row.day}
                selectable={false}
                defaultPageSize={10}
                searchPlaceholder="Search a day…"
              />
              <div className="-mt-3 flex justify-end">
                <ExportButton name={`wata-statistics-${asset.key}`} rows={days} columns={DAY_CSV} />
              </div>

              {statistics.spread && statistics.spread_by_time.length > 0 && (
                <Panel
                  title="Spread through the day"
                  description={`The average spread of each half hour, ${timeZone} time.${
                    dearest ? ` Dearest from ${dearest.slot}.` : ''
                  }`}
                >
                  <SpreadByTimeChart slots={statistics.spread_by_time} decimals={decimals} />
                  <details className="mt-3 text-xs text-muted-foreground">
                    <summary className="w-fit cursor-pointer rounded text-foreground/80 hover:text-foreground">
                      The figures
                    </summary>
                    <table className="mt-2 w-full max-w-xl text-left tabular-nums">
                      <thead>
                        <tr>
                          <th className="py-1 pr-4 font-medium">From</th>
                          <th className="py-1 pr-4 text-right font-medium">Average</th>
                          <th className="py-1 pr-4 text-right font-medium">Widest</th>
                          <th className="py-1 text-right font-medium">Bars</th>
                        </tr>
                      </thead>
                      <tbody>
                        {statistics.spread_by_time.map((slot) => (
                          <tr key={slot.slot} className="border-t border-border/50 text-foreground/80">
                            <td className="py-1 pr-4">{slot.slot}</td>
                            <td className="py-1 pr-4 text-right">{slot.spread_average === null ? '—' : formatNumber(slot.spread_average, decimals)}</td>
                            <td className="py-1 pr-4 text-right">{slot.spread_widest === null ? '—' : formatNumber(slot.spread_widest, decimals)}</td>
                            <td className="py-1 text-right">{formatNumber(slot.bars)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </details>
                </Panel>
              )}
            </>
          )}
        </div>
      )}
    </QueryState>
  )
}
