import { skipToken, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, RotateCw, Square, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'

import { CumulativeProfitChart, DailyProfitChart, ProfitLossLegend } from '@/components/charts/PerformanceCharts'
import { ExportButton } from '@/components/ExportButton'
import { KpiTile } from '@/components/KpiTile'
import { PageHeader, Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FormControl, FormDescription, FormItem, FormLabel, Input } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { CsvColumn } from '@/lib/csv'
import { cn } from '@/lib/utils'
import { formatDateTime, formatDay, formatMoney, formatNumber, formatPercent, formatTime } from '@/lib/format'
import type {
  BacktestDay,
  BacktestFigures,
  BacktestRefusal,
  BacktestRun,
  BacktestTrade,
} from '@/lib/types'

import { BreakdownsPanel } from './BreakdownsPanel'
import {
  dailyFromBacktest,
  executionSentence,
  figuresAt,
  formatBytes,
  isActive,
  isReplay,
  isSweep,
  localDay,
  middleLeverage,
  parameterChanges,
  ruleChanges,
  runName,
  sampleWarning,
  staleSentence,
  tally,
  wording,
} from './model'
import { BacktestsDisabled, BackToRuns, ChangeList, LeveragePicker, StateBadge } from './parts'
import { RealComparison } from './RealComparison'
import { SweepResults } from './SweepResults'
import { TrustPanel } from './TrustPanel'

function tradeColumns(timezone: string, runId: string, leverage: number, withMoney: boolean): ColumnDef<BacktestTrade>[] {
  return [
    {
      id: 'entry',
      header: 'Entered',
      accessorKey: 'entry_time',
      sortable: true,
      cell: ({ row }) => (
        <Link
          to={`/backtests/${runId}/days/${localDay(row.entry_time, timezone)}?leverage=${leverage}&trade=${encodeURIComponent(row.entry_time)}`}
          className="underline-offset-4 hover:underline"
          title="This trade on the chart of its day, and how the turbo went"
        >
          {formatDateTime(row.entry_time, timezone)}
        </Link>
      ),
    },
    {
      id: 'exit',
      header: 'Left',
      accessorKey: 'exit_time',
      sortable: true,
      cell: ({ row }) => formatTime(row.exit_time, timezone),
    },
    {
      id: 'direction',
      header: 'Direction',
      accessorKey: 'direction',
      sortable: true,
      cell: ({ row }) => <ActionBadge action={row.direction} />,
    },
    { id: 'entry_reason', header: 'Entered on', accessorKey: 'entry_reason', sortable: true },
    {
      id: 'performance',
      header: 'Result',
      accessorKey: 'performance_percent',
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.performance_percent} kind="percent" />,
    },
    ...(withMoney
      ? ([
          {
            id: 'profit_loss',
            header: 'In money',
            accessorFn: (row) => row.profit_loss ?? null,
            sortable: true,
            align: 'right',
            cell: ({ row }) => <PnlValue value={row.profit_loss} kind="money" />,
          },
          {
            id: 'stake',
            header: 'Put on it',
            accessorFn: (row) => row.stake ?? null,
            sortable: true,
            align: 'right',
            defaultHidden: true,
            cell: ({ row }) => `${formatMoney(row.stake)} (${formatPercent(row.scale_percent, false, 0)})`,
          },
        ] satisfies ColumnDef<BacktestTrade>[])
      : []),
    {
      id: 'best',
      header: 'Best level',
      accessorKey: 'max_performance_percent',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatPercent(row.max_performance_percent, true),
    },
    {
      id: 'worst',
      header: 'Worst level',
      accessorFn: (row) => row.min_performance_percent ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatPercent(row.min_performance_percent, true),
    },
    { id: 'exit_reason', header: 'Left on', accessorKey: 'exit_reason', sortable: true },
    {
      id: 'points',
      header: 'Index points',
      accessorKey: 'index_points',
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatNumber(row.index_points, 1),
    },
    {
      id: 'entry_index',
      header: 'Index at entry',
      accessorKey: 'entry_index',
      align: 'right',
      defaultHidden: true,
      cell: ({ row }) => formatNumber(row.entry_index, 1),
    },
    {
      id: 'exit_index',
      header: 'Index at exit',
      accessorKey: 'exit_index',
      align: 'right',
      defaultHidden: true,
      cell: ({ row }) => formatNumber(row.exit_index, 1),
    },
    {
      id: 'confidence',
      header: 'Confidence',
      accessorKey: 'confidence',
      sortable: true,
      align: 'right',
      defaultHidden: true,
      cell: ({ row }) => formatNumber(row.confidence, 2),
    },
  ]
}

const TRADE_CSV: CsvColumn<BacktestTrade>[] = [
  { header: 'entry_time', value: (row) => row.entry_time },
  { header: 'exit_time', value: (row) => row.exit_time },
  { header: 'direction', value: (row) => row.direction },
  { header: 'entry_reason', value: (row) => row.entry_reason },
  { header: 'confidence', value: (row) => row.confidence },
  { header: 'entry_index', value: (row) => row.entry_index },
  { header: 'exit_index', value: (row) => row.exit_index },
  { header: 'index_points', value: (row) => row.index_points },
  { header: 'performance_percent', value: (row) => row.performance_percent },
  { header: 'max_performance_percent', value: (row) => row.max_performance_percent },
  { header: 'min_performance_percent', value: (row) => row.min_performance_percent ?? null },
  { header: 'exit_reason', value: (row) => row.exit_reason },
  { header: 'scale_percent', value: (row) => row.scale_percent ?? null },
  { header: 'stake', value: (row) => row.stake ?? null },
  { header: 'profit_loss', value: (row) => row.profit_loss ?? null },
]

function dayColumns(runId: string, leverage: number, withMoney: boolean): ColumnDef<BacktestDay>[] {
  const money: ColumnDef<BacktestDay>[] = withMoney
    ? [
        {
          id: 'profit_loss',
          header: 'In money',
          accessorFn: (row) => row.profit_loss ?? null,
          sortable: true,
          align: 'right',
          cell: ({ row }) => <PnlValue value={row.profit_loss} kind="money" />,
        },
        {
          id: 'equity',
          header: 'Account after it',
          accessorFn: (row) => row.equity ?? null,
          sortable: true,
          align: 'right',
          cell: ({ row }) => formatMoney(row.equity),
        },
      ]
    : []
  return [
    {
      id: 'day',
      header: 'Day',
      accessorKey: 'day',
      sortable: true,
      cell: ({ row }) => (
        <Link
          to={`/backtests/${runId}/days/${row.day}?leverage=${leverage}`}
          className="underline-offset-4 hover:underline"
          title="The chart of this day"
        >
          {formatDay(row.day)}
        </Link>
      ),
    },
    { id: 'entries', header: 'Entries', accessorKey: 'entries', sortable: true, align: 'right' },
    { id: 'closed', header: 'Trades closed', accessorKey: 'closed', sortable: true, align: 'right' },
    {
      id: 'result',
      header: 'Day result',
      accessorKey: 'day_percent',
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.day_percent} kind="percent" />,
    },
    ...money,
  ]
}

function refusalColumns(timezone: string): ColumnDef<BacktestRefusal>[] {
  return [
    {
      id: 'time',
      header: 'When',
      accessorKey: 'time',
      sortable: true,
      cell: ({ row }) => formatDateTime(row.time, timezone),
    },
    {
      id: 'direction',
      header: 'Direction',
      accessorKey: 'direction',
      sortable: true,
      cell: ({ row }) => <ActionBadge action={row.direction} />,
    },
    { id: 'reason', header: 'Refused by', accessorKey: 'reason', sortable: true },
  ]
}

function Figures({ figures }: { figures: BacktestFigures }) {
  const money = figures.money
  return (
    <div className={cn('grid grid-cols-2 gap-3 sm:gap-4', money ? 'xl:grid-cols-5' : 'xl:grid-cols-4')}>
      <KpiTile
        label="All days, one applied on the other"
        value={<PnlValue value={figures.total_percent} kind="percent" className="font-semibold" />}
        hint={`Best day ${formatPercent(figures.best_day, true)} · worst ${formatPercent(figures.worst_day, true)}`}
      />
      <KpiTile
        label="Winning trades"
        value={formatPercent(figures.win_rate, false, 1)}
        hint={`Average win ${formatPercent(figures.average_win, true)} · average loss ${formatPercent(figures.average_loss, true)}`}
      />
      <KpiTile
        label="Trades"
        value={formatNumber(figures.trades)}
        hint={`On ${figures.days_with_an_entry} of ${figures.days} day${figures.days === 1 ? '' : 's'} tested`}
      />
      <KpiTile
        label="Worst drawdown"
        value={<PnlValue value={figures.worst_drawdown} kind="percent" className="font-semibold" />}
        hint="Deepest fall from a peak of the days put end to end"
      />
      {money && (
        <KpiTile
          label={`In money, on ${formatMoney(money.capital)}`}
          value={<PnlValue value={money.profit_loss} kind="money" className="font-semibold" />}
          hint={
            <>
              {formatPercent(money.return_percent, true)} at the size WATA gives each trade (
              {formatPercent(money.average_scale_percent, false, 0)} on average) · deepest fall{' '}
              {formatMoney(money.worst_drawdown)}
              {money.unfunded > 0 ? ` · ${money.unfunded} entr${money.unfunded === 1 ? 'y' : 'ies'} it could not buy` : ''}
            </>
          }
          className="max-xl:col-span-2"
        />
      )}
    </div>
  )
}

function Tally({ title, counts, none }: { title: string; counts: Record<string, number>; none: string }) {
  const rows = tally(counts)
  return (
    <div className="min-w-0">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{none}</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-2">
          {rows.map((row) => (
            <li key={row.reason} className="text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="truncate">{row.reason}</span>
                <span className="shrink-0 tabular-nums">{row.count}</span>
              </div>
              <div aria-hidden className="mt-1 h-1 overflow-hidden rounded-full bg-foreground/10">
                <div className="h-full rounded-full bg-foreground/60" style={{ width: `${row.share * 100}%` }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Every leverage of the run side by side: the same decisions, another turbo. */
function Leverages({ run }: { run: BacktestRun }) {
  const rows = run.summary?.runs ?? []
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">Points for 1.00</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 pr-4 text-right font-medium">Winning</th>
            <th className="py-2 pr-4 text-right font-medium">Average win</th>
            <th className="py-2 pr-4 text-right font-medium">Average loss</th>
            <th className="py-2 pr-4 text-right font-medium">Per trade</th>
            <th className="py-2 pr-4 text-right font-medium">All days</th>
            <th className="py-2 pr-4 text-right font-medium">Worst drawdown</th>
            <th className="py-2 text-right font-medium">Random runs beaten</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {rows.map(({ turbo, summary }) => (
            <tr key={turbo.points_per_unit}>
              <td className="py-2.5 pr-4 tabular-nums">{turbo.points_per_unit}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{summary.trades}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{formatPercent(summary.win_rate, false, 1)}</td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.average_win} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.average_loss} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.expectancy} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.total_percent} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={summary.worst_drawdown} kind="percent" />
              </td>
              <td className="py-2.5 text-right tabular-nums">{formatPercent(summary.against_random?.beaten, false, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Progress({ run }: { run: BacktestRun }) {
  const { state, error } = run.status
  if (state === 'queued') {
    return (
      <Alert tone="info">
        <AlertTitle>Waiting for its turn</AlertTitle>
        <AlertDescription>
          {run.waiting_before === 0
            ? 'It is the next one.'
            : `${run.waiting_before} backtest${run.waiting_before === 1 ? '' : 's'} will be made before it.`}{' '}
          One runs at a time.
        </AlertDescription>
      </Alert>
    )
  }
  if (state === 'running') {
    return (
      <Alert tone="info" aria-live="polite">
        <AlertTitle>Running</AlertTitle>
        <AlertDescription>
          {run.progress ? `Replaying ${formatDay(run.progress.day)}.` : 'Starting.'} This page follows it.
        </AlertDescription>
      </Alert>
    )
  }
  if (state === 'failed') {
    return (
      <Alert tone="danger" role="alert">
        <AlertTitle>The backtest failed</AlertTitle>
        <AlertDescription>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">{run.output.join('\n') || error}</pre>
        </AlertDescription>
      </Alert>
    )
  }
  if (state === 'cancelled' || state === 'interrupted') {
    return (
      <Alert tone="warning">
        <AlertTitle>{state === 'cancelled' ? 'Stopped before it finished' : 'Interrupted'}</AlertTitle>
        <AlertDescription>{error ?? 'It has no result. Run it again to get one.'}</AlertDescription>
      </Alert>
    )
  }
  return null
}

function Asked({ run }: { run: BacktestRun }) {
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, staleTime: 300_000 })
  const strategy = optionsQuery.data?.strategies.find((candidate) => candidate.name === run.strategy)
  const replay = isReplay(run) || run.replays === 'signals'
  const what = replay ? 'The signals WATA received' : wording(run.strategy)
  const fills = executionSentence(run.execution)
  return (
    <Panel
      title="What was asked"
      description={`${what}, ${formatDay(run.from)} to ${formatDay(run.to)}. Turbo paid ${run.turbo.entry_price}, spread ${run.turbo.spread}.`}
    >
      {(fills || (run.axes && run.axes.length > 0)) && (
        <div className="mb-5 flex flex-col gap-2 text-sm">
          {run.axes?.map((axis) => (
            <p key={`${axis.kind}-${axis.name}`}>
              <span className="text-muted-foreground">Tried for {wording(axis.name).toLowerCase()}:</span>{' '}
              <span className="tabular-nums">{axis.values.map(String).join(', ')}</span>
            </p>
          ))}
          {fills && <p>{fills}</p>}
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-2">
        {replay && isSweep(run) ? (
          <div>
            <h3 className="mb-2 text-xs font-medium text-muted-foreground">Replayed</h3>
            <p className="text-sm">The signals of WATA&apos;s journal for the period, once for each value tried.</p>
          </div>
        ) : replay ? (
          <div>
            <h3 className="mb-2 text-xs font-medium text-muted-foreground">Replayed</h3>
            <p className="text-sm">
              {formatNumber(run.signals)} signal{run.signals === 1 ? '' : 's'} of WATA&apos;s journal, each acted on at
              the first ten seconds after it. {formatNumber(run.real_trades)} real trade
              {run.real_trades === 1 ? '' : 's'} of the period {run.real_trades === 1 ? 'is' : 'are'} kept with the run
              to be set beside the model&apos;s.
            </p>
          </div>
        ) : (
          <div>
            <h3 className="mb-2 text-xs font-medium text-muted-foreground">Strategy parameters changed</h3>
            <ChangeList changes={parameterChanges(run, strategy?.parameters)} none="None: the strategy's defaults." />
          </div>
        )}
        <div>
          <h3 className="mb-2 text-xs font-medium text-muted-foreground">Rules changed for this run</h3>
          <ChangeList
            changes={ruleChanges(run, optionsQuery.data?.rules.editable)}
            none="None: the rules of the live configuration, as it was then."
          />
        </div>
      </div>
    </Panel>
  )
}

function Results({ run }: { run: BacktestRun }) {
  const { timezone } = useSession()
  const [leverage, setLeverage] = useState(() => middleLeverage(run.turbo.leverages))
  const figures = figuresAt(run, leverage)

  const tradesQuery = useQuery({
    queryKey: ['backtest-trades', run.id, leverage],
    queryFn: () => api.backtestTrades(run.id, leverage),
    staleTime: Infinity,
  })
  const daysQuery = useQuery({
    queryKey: ['backtest-days', run.id, leverage],
    queryFn: () => api.backtestDays(run.id, leverage),
    staleTime: Infinity,
  })
  const refusalsQuery = useQuery({
    queryKey: ['backtest-refusals', run.id, leverage],
    queryFn: () => api.backtestRefusals(run.id, leverage),
    staleTime: Infinity,
  })
  const trades = tradesQuery.data?.items
  const days = daysQuery.data?.items
  const refusals = refusalsQuery.data?.items

  const withMoney = !!figures?.money
  const daily = useMemo(() => dailyFromBacktest(days ?? [], trades ?? [], timezone), [days, trades, timezone])
  const tradeTable = useMemo(() => tradeColumns(timezone, run.id, leverage, withMoney), [timezone, run.id, leverage, withMoney])
  const dayTable = useMemo(() => dayColumns(run.id, leverage, withMoney), [run.id, leverage, withMoney])
  const refusalTable = useMemo(() => refusalColumns(timezone), [timezone])
  // Most recent first in the tables; the charts read oldest to newest.
  const tradesNewestFirst = useMemo(() => [...(trades ?? [])].reverse(), [trades])
  const daysNewestFirst = useMemo(() => [...(days ?? [])].reverse(), [days])

  const without = run.summary?.days_without_prices ?? []
  const incomplete = run.summary?.days_incomplete ?? []
  const thin = figures ? sampleWarning(figures.sample, figures.trades, figures.days) : null

  return (
    <>
      {(without.length > 0 || incomplete.length > 0) && (
        <Alert tone="warning">
          <AlertTitle>Not every day of the period was tested</AlertTitle>
          <AlertDescription>
            {without.length > 0 && (
              <>
                No prices for {without.length} weekday{without.length === 1 ? '' : 's'} (not fetched, or the market was
                closed): {without.map((day) => formatDay(day, false)).join(', ')}.{' '}
              </>
            )}
            {incomplete.length > 0 && (
              <>
                Prices cover only part of the market hours on {incomplete.map((day) => formatDay(day, false)).join(', ')}
                .
              </>
            )}
          </AlertDescription>
        </Alert>
      )}

      <LeveragePicker leverages={run.turbo.leverages} value={leverage} onChange={setLeverage} />

      {thin && (
        <Alert tone={figures?.sample === 'too_few' ? 'warning' : 'info'}>
          <AlertDescription>{thin}</AlertDescription>
        </Alert>
      )}

      {figures && <Figures figures={figures} />}

      {isReplay(run) && <RealComparison run={run} leverage={leverage} />}

      {figures && <TrustPanel figures={figures} />}

      <Panel
        title="Every leverage of this run"
        description="The same decisions on another turbo. A conclusion that holds on one line only is not one."
        bodyClassName="pt-2"
      >
        <Leverages run={run} />
      </Panel>

      <QueryState
        isPending={daysQuery.isPending || tradesQuery.isPending}
        error={daysQuery.error ?? tradesQuery.error}
        hasData={!!days && !!trades}
      >
        {daily.length === 0 ? (
          <Panel>
            <p className="py-10 text-center text-sm text-muted-foreground">No day with prices in this period.</p>
          </Panel>
        ) : (
          <>
            <Panel
              title="All days so far, one applied on the other"
              description="In percent, as WATA counts a day: each trade as the whole stake. Position sizing is not applied."
            >
              <CumulativeProfitChart days={daily} unit="percent" />
            </Panel>
            <Panel title="Result of each day" action={<ProfitLossLegend />}>
              <DailyProfitChart days={daily} unit="percent" />
            </Panel>
          </>
        )}
      </QueryState>

      {figures && figures.trades > 0 && <BreakdownsPanel runId={run.id} leverage={leverage} withMoney={withMoney} />}

      {figures && (
        <Panel title="Why" description="What the entries were made on, what ended the trades, and which rules refused entries.">
          <div className="grid gap-6 md:grid-cols-3">
            <Tally title="Entered on" counts={figures.entries} none="No entry." />
            <Tally title="Left on" counts={figures.exits} none="No trade." />
            <Tally title="Entries refused by" counts={figures.refusals} none="None refused." />
          </div>
        </Panel>
      )}

      <div className="flex justify-end">
        <ExportButton name={`wata-backtest-trades-${leverage}`} rows={trades ?? []} columns={TRADE_CSV} />
      </div>
      <AdvancedDataTable<BacktestTrade>
        title="Trades"
        searchPlaceholder="Search…"
        emptyTitle="No trade in this run"
        emptyDescription="A trade opens the chart of its day, with how the turbo went from its entry to its exit."
        data={tradesNewestFirst}
        columns={tradeTable}
        getRowId={(row) => row.entry_time}
        defaultPageSize={10}
        selectable={false}
      />

      <AdvancedDataTable<BacktestDay>
        title="Days"
        searchPlaceholder="Search…"
        emptyTitle="No day tested"
        emptyDescription="A day opens its chart: what the strategy saw and did."
        data={daysNewestFirst}
        columns={dayTable}
        getRowId={(row) => row.day}
        defaultPageSize={10}
        selectable={false}
      />

      <AdvancedDataTable<BacktestRefusal>
        title="Entries refused"
        searchPlaceholder="Search…"
        emptyTitle="No entry was refused"
        data={refusals ?? []}
        columns={refusalTable}
        getRowId={(row, index) => `${row.time}-${index}`}
        defaultPageSize={10}
        selectable={false}
      />
    </>
  )
}

export function BacktestRunPage() {
  const { backtests_enabled: enabled, timezone } = useSession()
  const { runId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [removing, setRemoving] = useState(false)
  const [naming, setNaming] = useState<{ note: string; tags: string } | null>(null)

  const runQuery = useQuery({
    queryKey: ['backtest', runId],
    queryFn: runId && enabled ? () => api.backtest(runId) : skipToken,
    refetchInterval: (query) => (query.state.data && isActive(query.state.data.status.state) ? 2_000 : false),
  })
  const run = runQuery.data

  const cancelMutation = useMutation({
    mutationFn: () => api.cancelBacktest(runId ?? ''),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['backtest', runId] })
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
    },
  })
  const renameMutation = useMutation({
    mutationFn: (asked: { note: string; tags: string }) =>
      api.renameBacktest(runId ?? '', { note: asked.note.trim(), tags: asked.tags.split(',').map((tag) => tag.trim()).filter(Boolean) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['backtest', runId] })
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      setNaming(null)
    },
  })
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteBacktest(runId ?? ''),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      navigate('/backtests')
    },
  })

  return (
    <>
      <BackToRuns />
      <PageHeader
        title={run ? runName(run) : 'Backtest'}
        description={
          run && (
            <span className="flex flex-wrap items-center gap-2">
              <StateBadge status={run.status} />
              {run.tags?.map((tag) => (
                <Badge key={tag} tone="neutral">
                  {tag}
                </Badge>
              ))}
              <span>
                Asked {formatDateTime(run.status.created_at, timezone)}
                {run.summary ? `, ${formatNumber(run.summary.bars)} bars replayed in ${run.summary.seconds} s` : ''}
                {run.sweep?.bars ? `, ${formatNumber(run.sweep.bars)} bars replayed in ${run.sweep.seconds} s` : ''}
                {run.bytes !== undefined ? ` · ${formatBytes(run.bytes)} on disk` : ''}
              </span>
            </span>
          )
        }
        actions={
          run && (
            <>
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Pencil className="size-3.5" />}
                onClick={() => {
                  renameMutation.reset()
                  setNaming({ note: run.note, tags: (run.tags ?? []).join(', ') })
                }}
              >
                Name and tags
              </Button>
              <Button
                variant="outline"
                size="sm"
                leftIcon={<RotateCw className="size-3.5" />}
                onClick={() => navigate(`/backtests/new?from=${run.id}`)}
              >
                Run again with changes
              </Button>
              {isActive(run.status.state) ? (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Square className="size-3.5" />}
                  isLoading={cancelMutation.isPending}
                  onClick={() => cancelMutation.mutate()}
                >
                  Stop
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Trash2 className="size-3.5" />}
                  onClick={() => {
                    deleteMutation.reset()
                    setRemoving(true)
                  }}
                >
                  Remove
                </Button>
              )}
            </>
          )
        }
      />

      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <QueryState isPending={runQuery.isPending} error={runQuery.error} hasData={!!run}>
          {run && (
            <>
              {cancelMutation.error && (
                <Alert tone="danger" role="alert">
                  <AlertDescription>{cancelMutation.error.message}</AlertDescription>
                </Alert>
              )}
              <Progress run={run} />
              {staleSentence(run.stale) && (
                <Alert tone="warning">
                  <AlertTitle>Stale</AlertTitle>
                  <AlertDescription>{staleSentence(run.stale)}</AlertDescription>
                </Alert>
              )}
              <Asked run={run} />
              {run.status.state === 'done' && run.summary && <Results key={run.id} run={run} />}
              {run.status.state === 'done' && isSweep(run) && run.sweep && <SweepResults key={run.id} run={run} />}
            </>
          )}
        </QueryState>
      )}

      <SettleModal
        open={removing}
        onClose={() => setRemoving(false)}
        title="Remove this backtest?"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRemoving(false)}>
              Keep it
            </Button>
            <Button variant="destructive" size="sm" isLoading={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              Remove
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          Its figures, trades and days are deleted from the server. The prices it ran on are kept.
        </p>
        {deleteMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {deleteMutation.error.message}
          </p>
        )}
      </SettleModal>

      <SettleModal
        open={naming !== null}
        onClose={() => setNaming(null)}
        title="Name and tags"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setNaming(null)}>
              Cancel
            </Button>
            <Button size="sm" isLoading={renameMutation.isPending} onClick={() => naming && renameMutation.mutate(naming)}>
              Save
            </Button>
          </>
        }
      >
        {naming && (
          <div className="flex flex-col gap-4">
            <FormItem>
              <FormLabel htmlFor="run-note">What this run is for</FormLabel>
              <FormControl>
                <Input id="run-note" value={naming.note} onChange={(event) => setNaming({ ...naming, note: event.target.value })} />
              </FormControl>
              <FormDescription>Shown in the list in place of what it changed.</FormDescription>
            </FormItem>
            <FormItem>
              <FormLabel htmlFor="run-tags">Tags</FormLabel>
              <FormControl>
                <Input id="run-tags" value={naming.tags} onChange={(event) => setNaming({ ...naming, tags: event.target.value })} />
              </FormControl>
              <FormDescription>Separated by commas. The list can be searched by them.</FormDescription>
            </FormItem>
            {renameMutation.error && (
              <p role="alert" className="text-sm text-negative">
                {renameMutation.error.message}
              </p>
            )}
          </div>
        )}
      </SettleModal>
    </>
  )
}
