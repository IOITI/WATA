import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Columns3, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { PageHeader } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatDay, formatPercent } from '@/lib/format'
import type { BacktestRunListed } from '@/lib/types'

import { formatBytes, isActive, isSweep, runName, STATE_LABEL } from './model'
import { BacktestsDisabled, StateBadge } from './parts'
import { PricesPanel } from './PricesPanel'
import { TurboPanel } from './TurboPanel'

/** A run whose figures can be set beside another's: finished, and one backtest, not a sweep of them. */
function isComparable(run: BacktestRunListed): boolean {
  return run.status.state === 'done' && !isSweep(run)
}

const OLD_DAYS = 30

function runColumns(
  timezone: string,
  selected: ReadonlySet<string>,
  toggle: (runId: string) => void,
): ColumnDef<BacktestRunListed>[] {
  return [
    {
      id: 'select',
      header: 'Select',
      width: '5rem',
      cell: ({ row }) =>
        // A run that waits or runs can neither be compared nor removed.
        isActive(row.status.state) ? null : (
          <Checkbox aria-label={`Select: ${runName(row)}`} checked={selected.has(row.id)} onChange={() => toggle(row.id)} />
        ),
    },
    {
      id: 'run',
      header: 'Backtest',
      // Searched by its name and by its tags.
      accessorFn: (row) => [runName(row), ...(row.tags ?? [])].join(' '),
      sortable: true,
      cell: ({ row }) => (
        // A long name takes two lines, not the width the figures need.
        <div className="min-w-0 max-w-80 whitespace-normal">
          <Link to={`/backtests/${row.id}`} className="font-medium underline-offset-4 hover:underline">
            {runName(row)}
          </Link>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>
              {formatDay(row.from)} to {formatDay(row.to)}
            </span>
            {row.tags?.map((tag) => (
              <Badge key={tag} tone="neutral">
                {tag}
              </Badge>
            ))}
            {(row.stale?.code || row.stale?.prices) && (
              <span className="text-warning" title="The code or the prices changed since: it would come out otherwise today">
                stale
              </span>
            )}
          </p>
        </div>
      ),
    },
    {
      id: 'asked',
      header: 'Asked',
      accessorFn: (row) => row.status.created_at ?? '',
      sortable: true,
      cell: ({ row }) => formatDateTime(row.status.created_at, timezone, false),
    },
    {
      id: 'state',
      header: 'State',
      accessorFn: (row) => STATE_LABEL[row.status.state],
      sortable: true,
      cell: ({ row }) => <StateBadge status={row.status} />,
    },
    {
      id: 'trades',
      header: 'Trades',
      accessorFn: (row) => row.headline?.trades ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => (
        <div>
          {row.headline?.trades ?? '-'}
          {row.headline?.sample === 'too_few' && <p className="text-xs text-warning">too few to conclude</p>}
        </div>
      ),
    },
    {
      id: 'win_rate',
      header: 'Winning',
      accessorFn: (row) => row.headline?.win_rate ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatPercent(row.headline?.win_rate, false, 1),
    },
    {
      id: 'result',
      header: 'All days',
      accessorFn: (row) => row.headline?.total_percent ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.headline?.total_percent} kind="percent" />,
    },
    {
      id: 'beaten',
      // The share of the same days traded with entries at random that did worse.
      header: 'Beats random',
      accessorFn: (row) => row.headline?.beaten ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => formatPercent(row.headline?.beaten, false, 0),
    },
    {
      id: 'drawdown',
      header: 'Worst drawdown',
      accessorFn: (row) => row.headline?.worst_drawdown ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.headline?.worst_drawdown} kind="percent" />,
    },
  ]
}

export function BacktestsPage() {
  const { timezone, backtests_enabled: enabled } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [removing, setRemoving] = useState(false)
  // When the page was opened: what "older than" is counted from.
  const [openedAt] = useState(() => Date.now())

  const runsQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    enabled,
    // A run that waits or runs changes state by itself: follow it closely.
    refetchInterval: (query) =>
      query.state.data?.items.some((run) => isActive(run.status.state)) ? 2_000 : 30_000,
  })
  const runs = runsQuery.data?.items

  const storageQuery = useQuery({ queryKey: ['backtest-storage'], queryFn: api.backtestStorage, enabled, staleTime: 60_000 })
  const storage = storageQuery.data

  const columns = useMemo(
    () =>
      runColumns(timezone, selected, (runId) =>
        setSelected((current) => {
          const next = new Set(current)
          if (!next.delete(runId)) next.add(runId)
          return next
        }),
      ),
    [timezone, selected],
  )
  // A run removed meanwhile is no longer selected.
  const chosen = (runs ?? []).filter((run) => selected.has(run.id))
  // No more than the chart of a comparison has colours for.
  const comparable = chosen.filter(isComparable).map((run) => run.id)
  const canCompare = comparable.length >= 2 && comparable.length <= MAX_COMPARED
  const settled = (runs ?? []).filter((run) => !isActive(run.status.state))
  const unfinished = settled.filter((run) => run.status.state !== 'done')
  const old = settled.filter(
    (run) => run.status.created_at && openedAt - Date.parse(run.status.created_at) > OLD_DAYS * 86_400_000,
  )

  const removeMutation = useMutation({
    mutationFn: () => api.removeBacktests(chosen.map((run) => run.id)),
    onSuccess: (outcome) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      void queryClient.invalidateQueries({ queryKey: ['backtest-storage'] })
      setSelected(new Set(outcome.kept.map((entry) => entry.id)))
      if (outcome.kept.length === 0) setRemoving(false)
    },
  })

  return (
    <>
      <PageHeader
        title="Backtests"
        description="A strategy, or the signals WATA received, replayed on prices of the past with WATA's own rules. Nothing here trades."
        actions={
          enabled && (
            <>
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Trash2 className="size-3.5" />}
                disabled={chosen.length === 0}
                onClick={() => {
                  removeMutation.reset()
                  setRemoving(true)
                }}
              >
                Remove{chosen.length > 0 ? ` ${chosen.length}` : ''}
              </Button>
              <Button
                variant="outline"
                size="sm"
                leftIcon={<Columns3 className="size-3.5" />}
                disabled={!canCompare}
                onClick={() => navigate(`/backtests/compare?runs=${comparable.join(',')}`)}
              >
                Compare{canCompare ? ` ${comparable.length}` : ''}
              </Button>
              <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => navigate('/backtests/new')}>
                New backtest
              </Button>
            </>
          )
        }
      />

      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <>
          <QueryState isPending={runsQuery.isPending} error={runsQuery.error} hasData={!!runs}>
            {runs && (
              <div className="flex flex-col gap-2">
                <AdvancedDataTable<BacktestRunListed>
                  title="Runs"
                  searchPlaceholder="Search…"
                  emptyTitle="No backtest yet"
                  emptyDescription="Ask for one with “New backtest”. It needs prices: see below."
                  data={runs}
                  columns={columns}
                  getRowId={(row) => row.id}
                  defaultPageSize={10}
                  selectable={false}
                />
                <p className="text-xs text-muted-foreground">
                  The figures are those of the middle leverage each run was made with. A run shows them all. “Beats
                  random” is the share of the same days traded with entries at random that did worse: near 50%, the
                  entries have shown nothing. Two to {MAX_COMPARED} finished backtests can be compared; a sweep shows
                  its own combinations side by side.
                </p>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
                  {storage && (
                    <span>
                      {storage.runs.count} run{storage.runs.count === 1 ? '' : 's'} take {formatBytes(storage.runs.bytes)}{' '}
                      on disk; {storage.prices.days} day{storage.prices.days === 1 ? '' : 's'} of prices,{' '}
                      {formatBytes(storage.prices.bars_bytes + storage.prices.fetched_bytes)}.
                    </span>
                  )}
                  {unfinished.length > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => setSelected(new Set(unfinished.map((run) => run.id)))}>
                      Select the {unfinished.length} failed or stopped
                    </Button>
                  )}
                  {old.length > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => setSelected(new Set(old.map((run) => run.id)))}>
                      Select the {old.length} older than {OLD_DAYS} days
                    </Button>
                  )}
                  {selected.size > 0 && (
                    <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                      Select none
                    </Button>
                  )}
                </div>
              </div>
            )}
          </QueryState>

          <PricesPanel />
          <TurboPanel />
        </>
      )}

      <SettleModal
        open={removing}
        onClose={() => setRemoving(false)}
        title={`Remove ${chosen.length} backtest${chosen.length === 1 ? '' : 's'}?`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRemoving(false)}>
              Keep them
            </Button>
            <Button variant="destructive" size="sm" isLoading={removeMutation.isPending} onClick={() => removeMutation.mutate()}>
              Remove
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          Their figures, trades and days are deleted from the server. The prices they ran on are kept.
        </p>
        <ul className="mt-3 flex max-h-48 flex-col gap-1 overflow-auto text-sm">
          {chosen.map((run) => (
            <li key={run.id} className="truncate">
              {runName(run)}
            </li>
          ))}
        </ul>
        {removeMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {removeMutation.error.message}
          </p>
        )}
        {removeMutation.data && removeMutation.data.kept.length > 0 && (
          <p role="alert" className="mt-2 text-sm text-warning">
            {removeMutation.data.kept.length} could not be removed: {removeMutation.data.kept[0].reason}.
          </p>
        )}
      </SettleModal>
    </>
  )
}
