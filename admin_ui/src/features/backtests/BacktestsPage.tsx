import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Columns3, FlaskConical, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { ButtonLink } from '@/components/ButtonLink'
import { PageHeader } from '@/components/Panel'
import { ClaudeBadge, PnlValue, QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { api } from '@/lib/api'
import { formatBytes, formatDateTime, formatDay, formatPercent } from '@/lib/format'
import type { BacktestRunListed } from '@/lib/types'

import { comparable, isActive, sourceLabel, STATE_LABEL } from './model'
import { BacktestsDisabled, StateBadge } from './parts'
import { ResearchFiles } from './ResearchFiles'
import { TurboModelPanel } from './TurboModelPanel'

function runColumns(timezone: string, selected: ReadonlySet<string>, toggle: (runId: string) => void): ColumnDef<BacktestRunListed>[] {
  return [
    {
      id: 'select',
      header: 'Select',
      width: '5rem',
      cell: ({ row }) =>
        // A run that waits or runs can neither be compared nor removed.
        isActive(row.status.state) ? null : (
          <Checkbox aria-label={`Select: ${row.name}`} checked={selected.has(row.id)} onChange={() => toggle(row.id)} />
        ),
    },
    {
      id: 'run',
      header: 'Backtest',
      // Searched by its name, what it runs and its tags, and "Claude" for Claude's.
      accessorFn: (row) => [row.name, sourceLabel(row), ...row.tags, ...(madeByClaude(row) ? ['Claude'] : [])].join(' '),
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-96 whitespace-normal">
          <Link to={`/backtests/${row.id}`} className="font-medium underline-offset-4 hover:underline">
            {row.name}
          </Link>
          {row.kind === 'sweep' && (
            <Badge tone="neutral" className="ml-2">
              Sweep
            </Badge>
          )}
          {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
          <p className="text-xs text-muted-foreground">{sourceLabel(row)}</p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>
              {formatDay(row.from)} to {formatDay(row.to)}
            </span>
            {row.tags.map((tag) => (
              <Badge key={tag} tone="neutral">
                {tag}
              </Badge>
            ))}
            {row.stale && (
              <span className="text-warning" title="Made by other code than this admin's: it could come out otherwise today">
                made with other code
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
      cell: ({ row }) => (
        <div>
          <StateBadge status={row.status} />
          {row.position !== null && <p className="text-xs text-muted-foreground">number {row.position} in line</p>}
        </div>
      ),
    },
    {
      id: 'days',
      header: 'Days traded',
      accessorFn: (row) => row.headline?.days_tested ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => row.headline?.days_tested ?? '-',
    },
    {
      id: 'trades',
      header: 'Trades',
      accessorFn: (row) => row.headline?.trades ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) =>
        row.kind === 'sweep' ? (
          <span className="text-muted-foreground">{row.headline?.cells ?? '-'} combinations</span>
        ) : (
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
      id: 'money',
      header: 'In money',
      accessorFn: (row) => row.headline?.profit_loss ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <PnlValue value={row.headline?.profit_loss} kind="money" />,
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
  ]
}

export function BacktestsPage() {
  const { timezone, backtests_enabled: enabled } = useSession()
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [removing, setRemoving] = useState(false)
  const [clearing, setClearing] = useState(false)

  const runsQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    enabled,
    // A run that waits or runs changes state by itself: follow it closely.
    refetchInterval: (query) => (query.state.data?.items.some((run) => isActive(run.status.state)) ? 2_000 : 30_000),
  })
  const runs = runsQuery.data?.items
  const storage = runsQuery.data?.storage

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
  const compared = comparable(runs ?? [], selected)
  const canCompare = compared.length >= 2 && compared.length <= MAX_COMPARED

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtests'] })
  const removeMutation = useMutation({
    mutationFn: () => api.removeBacktests(chosen.map((run) => run.id)),
    onSuccess: (outcome) => {
      void refresh()
      setSelected(new Set(outcome.kept.map((entry) => entry.id)))
      if (outcome.kept.length === 0) setRemoving(false)
    },
  })
  const clearMutation = useMutation({
    mutationFn: api.removeFormerBacktests,
    onSuccess: () => {
      void refresh()
      setClearing(false)
    },
  })

  return (
    <>
      <PageHeader
        title="Backtests"
        description="A Pine script, or the signals WATA received, traded as WATA trades on the turbos the price recorder kept. Nothing here trades."
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
              <ButtonLink
                to={canCompare ? `/backtests/compare?runs=${compared.map((run) => run.id).join(',')}` : null}
                variant="outline"
                size="sm"
                leftIcon={<Columns3 className="size-3.5" />}
              >
                Compare{canCompare ? ` ${compared.length}` : ''}
              </ButtonLink>
              <ButtonLink to="/backtests/experiments" variant="outline" size="sm" leftIcon={<FlaskConical className="size-3.5" />}>
                Experiments
              </ButtonLink>
              <ButtonLink to="/backtests/new" size="sm" leftIcon={<Plus className="size-3.5" />}>
                New backtest
              </ButtonLink>
            </>
          )
        }
      />

      {!enabled ? (
        <BacktestsDisabled />
      ) : (
        <div className="flex flex-col gap-6">
          <QueryState isPending={runsQuery.isPending} error={runsQuery.error} hasData={!!runs}>
            {runs && (
              <div className="flex flex-col gap-2">
                <AdvancedDataTable<BacktestRunListed>
                  title="Runs"
                  searchPlaceholder="Search…"
                  emptyTitle="No backtest yet"
                  emptyDescription="Ask for one with “New backtest”: a Pine script or the signals WATA received, on days the price recorder kept turbos."
                  data={runs}
                  columns={columns}
                  getRowId={(row) => row.id}
                  defaultPageSize={10}
                  selectable={false}
                />
                <p className="text-xs text-muted-foreground">
                  Every trade buys a turbo the price recorder kept, at its recorded ask, and sells it at its recorded bid.
                  “Beats random” is the share of the same days traded with entries at random that did worse: near 50 %, the
                  entries have shown nothing. Two to {MAX_COMPARED} finished runs can be compared.
                </p>
                {storage && (
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
                    <span>
                      {storage.runs.count} run{storage.runs.count === 1 ? '' : 's'} take {formatBytes(storage.runs.bytes)} on disk.
                    </span>
                    {storage.former.bytes > 0 && (
                      <>
                        <span>
                          The former backtester left {storage.former.count} run{storage.former.count === 1 ? '' : 's'} (
                          {formatBytes(storage.former.bytes)}), which nothing reads any more.
                        </span>
                        <Button variant="ghost" size="sm" onClick={() => setClearing(true)}>
                          Remove them
                        </Button>
                      </>
                    )}
                    {selected.size > 0 && (
                      <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                        Select none
                      </Button>
                    )}
                  </div>
                )}
                <ResearchFiles />
              </div>
            )}
          </QueryState>

          <TurboModelPanel />
        </div>
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
          Their signals, trades and days are deleted from the server. The recorded prices they ran on are kept.
        </p>
        <ul className="mt-3 flex max-h-48 flex-col gap-1 overflow-auto text-sm">
          {chosen.map((run) => (
            <li key={run.id} className="truncate">
              {run.name}
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

      <SettleModal
        open={clearing}
        onClose={() => setClearing(false)}
        title="Remove the former backtester's runs?"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setClearing(false)}>
              Keep them
            </Button>
            <Button variant="destructive" size="sm" isLoading={clearMutation.isPending} onClick={() => clearMutation.mutate()}>
              Remove
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          The runs of the backtester this one replaced, on a model of the turbo, and its measures of the turbo. Nothing
          reads them any more. The prices downloaded from Dukascopy are kept.
        </p>
        {clearMutation.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {clearMutation.error.message}
          </p>
        )}
      </SettleModal>
    </>
  )
}
