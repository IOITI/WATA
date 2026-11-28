import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  cn,
  DateRangeFilter,
  ExportButton,
  formatDay,
  formatNumber,
  formatPercent,
  KpiGrid,
  KpiTile,
  lastDays,
  PageHeader,
  Panel,
  QueryState,
  rangeParams,
  SignedValue,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeaderCell,
  TableRow,
} from 'ioiti-ui'
import type { ColumnDef, CsvColumn, DateRange } from 'ioiti-ui'
import { useMemo, useState } from 'react'

import { DayCalendar } from '@/components/charts/Heatmaps'
import { CumulativeProfitChart, DailyProfitChart, ProfitLossLegend } from '@/components/charts/PerformanceCharts'
import { ActionBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { DailyPerformance, InstrumentPerformance, Performance } from '@/lib/types'

import { EdgeWatchPanel } from '@/features/edgeWatch/EdgeWatchPanel'

import { LivePaperPanel } from './LivePaperPanel'

const DAILY_COLUMNS: ColumnDef<DailyPerformance>[] = [
  {
    id: 'day',
    header: 'Day',
    accessorKey: 'day_date',
    sortable: true,
    cell: ({ row }) => formatDay(row.day_date),
  },
  { id: 'trades', header: 'Trades', accessorKey: 'trade_count', sortable: true, align: 'right' },
  { id: 'won', header: 'Won', accessorKey: 'win_count', sortable: true, align: 'right' },
  { id: 'lost', header: 'Lost', accessorKey: 'loss_count', sortable: true, align: 'right' },
  {
    id: 'profit_loss',
    header: 'Profit / loss',
    accessorKey: 'profit_loss',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <SignedValue value={row.profit_loss} kind="money" />,
  },
  {
    id: 'performance',
    header: 'Day performance',
    accessorKey: 'performance_percent',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <SignedValue value={row.performance_percent} kind="percent" />,
  },
  {
    id: 'cumulative',
    header: 'Cumulative',
    accessorKey: 'cumulative_profit_loss',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <SignedValue value={row.cumulative_profit_loss} kind="money" />,
  },
]

const DAILY_CSV: CsvColumn<DailyPerformance>[] = [
  { header: 'day', value: (row) => row.day_date },
  { header: 'trades', value: (row) => row.trade_count },
  { header: 'won', value: (row) => row.win_count },
  { header: 'lost', value: (row) => row.loss_count },
  { header: 'profit_loss', value: (row) => row.profit_loss },
  { header: 'day_performance_percent', value: (row) => row.performance_percent },
  { header: 'cumulative_profit_loss', value: (row) => row.cumulative_profit_loss },
]

const INSTRUMENT_COLUMNS: ColumnDef<InstrumentPerformance>[] = [
  { id: 'instrument', header: 'Instrument', accessorKey: 'instrument_name', sortable: true },
  {
    id: 'action',
    header: 'Direction',
    accessorKey: 'action',
    sortable: true,
    cell: ({ row }) => <ActionBadge action={row.action} />,
  },
  { id: 'trades', header: 'Trades', accessorKey: 'trade_count', sortable: true, align: 'right' },
  {
    id: 'profit_loss',
    header: 'Profit / loss',
    accessorKey: 'profit_loss',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <SignedValue value={row.profit_loss} kind="money" />,
  },
]

function Summary({ performance }: { performance: Performance }) {
  const { summary } = performance
  return (
    <KpiGrid>
      <KpiTile
        label="Profit and loss"
        value={<SignedValue value={summary.profit_loss} kind="money" className="font-semibold" />}
        hint={`${formatNumber(summary.trade_count)} closed trade${summary.trade_count === 1 ? '' : 's'}`}
      />
      <KpiTile
        label="Winning trades"
        value={formatPercent(summary.win_rate, false, 1)}
        hint={`${summary.win_count} won, ${summary.loss_count} lost`}
      />
      <KpiTile
        label="Winning days"
        value={
          <>
            {summary.winning_days}{' '}
            <span className="text-base font-normal text-muted-foreground">of {summary.trading_days}</span>
          </>
        }
        hint={
          summary.best_day && summary.worst_day ? (
            <>
              Best <SignedValue value={summary.best_day.profit_loss} kind="money" /> · worst{' '}
              <SignedValue value={summary.worst_day.profit_loss} kind="money" />
            </>
          ) : (
            'No trading day in this range'
          )
        }
      />
      <KpiTile
        label="Average trade"
        value={<SignedValue value={summary.average_trade_percent} kind="percent" className="font-semibold" />}
        hint="Mean performance of a closed trade"
      />
    </KpiGrid>
  )
}

function ByDirection({ performance }: { performance: Performance }) {
  if (performance.by_action.length === 0) {
    return <p className="py-4 text-center text-sm text-muted-foreground">No closed trade in this range.</p>
  }
  return (
    // Its six columns are wider than the narrowest phones: they scroll in their own frame there.
    <Table density="compact">
      <TableHead>
        <TableRow>
          <TableHeaderCell>Direction</TableHeaderCell>
          <TableHeaderCell numeric>Trades</TableHeaderCell>
          <TableHeaderCell numeric>Won</TableHeaderCell>
          <TableHeaderCell numeric>Lost</TableHeaderCell>
          <TableHeaderCell numeric>Average trade</TableHeaderCell>
          <TableHeaderCell numeric>Profit / loss</TableHeaderCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {performance.by_action.map((entry) => (
          <TableRow key={entry.action}>
            <TableCell>
              <ActionBadge action={entry.action} />
            </TableCell>
            <TableCell numeric>{entry.trade_count}</TableCell>
            <TableCell numeric>{entry.win_count}</TableCell>
            <TableCell numeric>{entry.loss_count}</TableCell>
            <TableCell className="text-right">
              <SignedValue value={entry.average_percent} kind="percent" />
            </TableCell>
            <TableCell className="text-right">
              <SignedValue value={entry.profit_loss} kind="money" />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function PerformancePage() {
  const { timezone, backtests_enabled: backtestsEnabled } = useSession()
  const [range, setRange] = useState<DateRange>(() => lastDays(90))
  const params = rangeParams(range)

  const performanceQuery = useQuery({
    queryKey: ['performance', params.from, params.to],
    queryFn: () => api.performance(params),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  const performance = performanceQuery.data

  // Most recent day first in the table; the charts read oldest to newest.
  const dailyNewestFirst = useMemo(() => [...(performance?.daily ?? [])].reverse(), [performance?.daily])
  // Largest losses and gains are what one looks for: the API sends them sorted by profit.
  const instruments = useMemo(() => [...(performance?.by_instrument ?? [])].reverse(), [performance?.by_instrument])

  return (
    <>
      <PageHeader
        title="Performance"
        description={`Closed trades from the live database, by the day they closed in ${timezone}.`}
        actions={
          <>
            <DateRangeFilter value={range} onChange={setRange} />
            <ExportButton name="wata-daily-performance" rows={performance?.daily ?? []} columns={DAILY_CSV} />
          </>
        }
      />

      <QueryState isPending={performanceQuery.isPending} error={performanceQuery.error} hasData={!!performance}>
        {performance && (
          // A new range keeps the previous figures in place, dimmed, until its own arrive.
          <div className={cn('flex flex-col gap-6 transition-opacity', performanceQuery.isPlaceholderData && 'opacity-60')}>
            <Summary performance={performance} />

            {/* Broken, or just unlucky: the live trades against the live reference. */}
            <EdgeWatchPanel owner="live" enabled={backtestsEnabled} />

            <LivePaperPanel enabled={backtestsEnabled} />

            {performance.daily.length === 0 ? (
              <Panel>
                <p className="py-10 text-center text-sm text-muted-foreground">No closed trade in this range.</p>
              </Panel>
            ) : (
              <>
                <Panel
                  title="Cumulative profit and loss"
                  description={
                    performance.from
                      ? 'Running total of realised profit, continuing from what was made before the range.'
                      : 'Running total of realised profit since the first trade.'
                  }
                >
                  <CumulativeProfitChart days={performance.daily} />
                </Panel>

                <Panel title="Profit and loss per day" action={<ProfitLossLegend />}>
                  <DailyProfitChart days={performance.daily} />
                </Panel>

                <Panel
                  title="Calendar"
                  description="The same days by week, Monday at the top: gains and losses each by size."
                >
                  <DayCalendar
                    daily={performance.daily}
                    from={performance.from}
                    to={performance.to}
                    label="Profit and loss of each day, as a calendar"
                  />
                </Panel>
              </>
            )}

            <Panel title="Long and short" bodyClassName="pt-2">
              <ByDirection performance={performance} />
            </Panel>

            <AdvancedDataTable<DailyPerformance>
              title="Days"
              searchPlaceholder="Search…"
              emptyTitle="No trading day in this range"
              data={dailyNewestFirst}
              columns={DAILY_COLUMNS}
              getRowId={(row) => row.day_date}
              defaultPageSize={10}
              selectable={false}
            />

            <AdvancedDataTable<InstrumentPerformance>
              title="Instruments"
              searchPlaceholder="Search instrument…"
              emptyTitle="Nothing traded in this range"
              data={instruments}
              columns={INSTRUMENT_COLUMNS}
              getRowId={(row) => `${row.instrument_name}-${row.action}`}
              defaultPageSize={10}
              selectable={false}
            />
          </div>
        )}
      </QueryState>
    </>
  )
}
