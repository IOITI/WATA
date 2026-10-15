import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { KpiTile } from '@/components/KpiTile'
import { Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { api } from '@/lib/api'
import { formatDateTime, formatDay, formatNumber } from '@/lib/format'
import type { BacktestComparison, BacktestLeg, BacktestRun } from '@/lib/types'

import { heldLonger, journalSentence } from './model'

type Pair = BacktestComparison['pairs'][number]
type ComparedDay = BacktestComparison['days'][number]

/** A leg's moment as the formatters take one. */
function moment(seconds: number): string {
  return new Date(seconds * 1000).toISOString()
}

/** Points of percent: a distance, or with its sign a gap (the model minus WATA). */
function points(value: number | null | undefined, withSign = false): string {
  if (value === null || value === undefined) return formatNumber(value)
  const size = Math.abs(value)
  const text = `${formatNumber(size, 2)} pt${size === 1 ? '' : 's'}`
  if (value < 0) return `−${text}`
  return withSign && value > 0 ? `+${text}` : text
}

function pairColumns(timezone: string): ColumnDef<Pair>[] {
  return [
    {
      id: 'entered',
      header: 'Entered',
      accessorFn: (row) => row.real.entered_at,
      sortable: true,
      cell: ({ row }) => formatDateTime(moment(row.real.entered_at), timezone),
    },
    {
      id: 'direction',
      header: 'Direction',
      accessorFn: (row) => row.real.direction,
      sortable: true,
      cell: ({ row }) => <ActionBadge action={row.real.direction} />,
    },
    {
      id: 'real',
      header: 'WATA',
      accessorFn: (row) => row.real.performance,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.real.performance} kind="percent" />,
    },
    {
      id: 'model',
      header: 'Model',
      accessorFn: (row) => row.model.performance,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.model.performance} kind="percent" />,
    },
    {
      id: 'gap',
      header: 'Model minus WATA',
      accessorKey: 'gap',
      sortable: true,
      align: 'right',
      cell: ({ row }) => points(row.gap, true),
    },
    { id: 'real_exit', header: 'WATA left on', accessorFn: (row) => row.real.exit, sortable: true },
    {
      id: 'model_exit',
      header: 'Model left on',
      accessorFn: (row) => row.model.exit,
      sortable: true,
      cell: ({ row }) =>
        row.same_exit ? <span className="text-muted-foreground">the same</span> : <span>{row.model.exit}</span>,
    },
    {
      id: 'held',
      header: 'Model held it',
      accessorKey: 'left_later',
      sortable: true,
      align: 'right',
      cell: ({ row }) => heldLonger(row.left_later),
    },
  ]
}

const DAY_COLUMNS: ColumnDef<ComparedDay>[] = [
  { id: 'day', header: 'Day', accessorKey: 'day', sortable: true, cell: ({ row }) => formatDay(row.day) },
  {
    id: 'real',
    header: 'WATA',
    accessorKey: 'real_percent',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <PnlValue value={row.real_percent} kind="percent" />,
  },
  {
    id: 'model',
    header: 'Model',
    accessorKey: 'model_percent',
    sortable: true,
    align: 'right',
    cell: ({ row }) => <PnlValue value={row.model_percent} kind="percent" />,
  },
  {
    id: 'gap',
    header: 'Model minus WATA',
    accessorKey: 'gap',
    sortable: true,
    align: 'right',
    cell: ({ row }) => points(row.gap, true),
  },
  { id: 'real_trades', header: 'WATA trades', accessorKey: 'real_trades', sortable: true, align: 'right' },
  { id: 'model_trades', header: 'Model trades', accessorKey: 'model_trades', sortable: true, align: 'right' },
]

function Alone({ title, legs, timezone, none }: { title: string; legs: BacktestLeg[]; timezone: string; none: string }) {
  return (
    <div className="min-w-0">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {legs.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">{none}</p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5 text-sm">
          {legs.map((leg) => (
            <li key={`${leg.entered_at}-${leg.direction}`} className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="tabular-nums">{formatDateTime(moment(leg.entered_at), timezone)}</span>
              <ActionBadge action={leg.direction} />
              <PnlValue value={leg.performance} kind="percent" />
              <span className="text-muted-foreground">{leg.exit}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * A run of the signals WATA received, beside what WATA really did with them.
 * It is the model that is tested here, not a strategy.
 */
export function RealComparison({ run, leverage }: { run: BacktestRun; leverage: number }) {
  const comparisonQuery = useQuery({
    queryKey: ['backtest-comparison', run.id, leverage],
    queryFn: () => api.backtestComparison(run.id, leverage),
    staleTime: Infinity,
  })
  const compared = comparisonQuery.data
  const pairTable = useMemo(() => pairColumns(compared?.timezone ?? 'UTC'), [compared?.timezone])
  const withoutPrices = run.summary?.signals?.without_prices.length ?? 0

  return (
    <QueryState isPending={comparisonQuery.isPending} error={comparisonQuery.error} hasData={!!compared}>
      {compared && (
        <>
          <Panel
            title="Against what WATA really did"
            description="Each real trade beside the model's trade entered the same way within two minutes. The gaps found here are the margin every backtest is to be read with."
          >
            <div className="flex flex-col gap-5">
              <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
                <KpiTile
                  label="Real trades the model made too"
                  value={`${compared.summary.paired} of ${compared.summary.real_trades}`}
                  hint={`The model made ${compared.summary.model_trades} trade${compared.summary.model_trades === 1 ? '' : 's'}`}
                />
                <KpiTile
                  label="Half of them within"
                  value={points(compared.summary.median_gap)}
                  hint={`Nine in ten within ${points(compared.summary.ninth_gap)} of the real result`}
                />
                <KpiTile
                  label="Model minus WATA, on average"
                  value={points(compared.summary.average_gap, true)}
                  hint="Above zero, the model flatters"
                />
                <KpiTile
                  label="Ended the same way"
                  value={`${compared.summary.same_exit} of ${compared.summary.paired}`}
                  hint={
                    <>
                      All days: WATA <PnlValue value={compared.summary.real_total} kind="percent" />, model{' '}
                      <PnlValue value={compared.summary.model_total} kind="percent" />
                    </>
                  }
                />
              </div>
              <p className="text-sm text-muted-foreground">{journalSentence(compared.journal)}</p>
              {withoutPrices > 0 && (
                <Alert tone="warning">
                  <AlertDescription>
                    {withoutPrices} signal{withoutPrices === 1 ? '' : 's'} came at a moment the prices do not cover, and{' '}
                    {withoutPrices === 1 ? 'was' : 'were'} not replayed. A real trade made of one of them has no pair.
                  </AlertDescription>
                </Alert>
              )}
              <div className="grid gap-6 md:grid-cols-2">
                <Alone
                  title="Entered by WATA, not by the model"
                  legs={compared.only_real}
                  timezone={compared.timezone}
                  none="None."
                />
                <Alone
                  title="Entered by the model, not by WATA"
                  legs={compared.only_model}
                  timezone={compared.timezone}
                  none="None."
                />
              </div>
            </div>
          </Panel>

          <AdvancedDataTable<Pair>
            title="Real trades and the model's, pair by pair"
            searchPlaceholder="Search…"
            emptyTitle="No real trade was made by the model too"
            data={compared.pairs}
            columns={pairTable}
            getRowId={(row) => `${row.real.reference}-${row.real.entered_at}`}
            defaultPageSize={10}
            selectable={false}
          />

          <AdvancedDataTable<ComparedDay>
            title="Days of WATA and of the model"
            searchPlaceholder="Search…"
            emptyTitle="No day to compare"
            data={compared.days}
            columns={DAY_COLUMNS}
            getRowId={(row) => row.day}
            defaultPageSize={10}
            selectable={false}
          />
        </>
      )}
    </QueryState>
  )
}
