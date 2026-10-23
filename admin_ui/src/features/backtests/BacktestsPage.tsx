import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Columns3, FlaskConical, Plus, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { ButtonLink } from '@/components/ButtonLink'
import { PageHeader } from '@/components/Panel'
import { RouterLink } from '@/components/RouterLink'
import { ClaudeBadge, PnlValue, QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { ColumnDef } from '@/components/ui/advanced-data-table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { api } from '@/lib/api'
import { formatBytes, formatDateTime, formatDay, formatPercent } from '@/lib/format'
import type { BacktestRunListed, Experiment } from '@/lib/types'

import { collapseAttempts, comparable, failureLine, filterRuns, isActive, RUN_FILTER_NAMES, runFilterOf, sourceLabel, STATE_LABEL } from './model'
import type { RunFilter } from './model'
import { BacktestsDisabled, StateBadge } from './parts'
import { ResearchFiles } from './ResearchFiles'
import { TurboModelPanel } from './TurboModelPanel'

/** What each filter of the list offers: the first, empty, leaves the list whole. */
const FILTERS: Record<Exclude<keyof RunFilter, 'experiment'>, { label: string; options: [string, string][] }> = {
  state: { label: 'State', options: [['', 'Any'], ['active', 'Waiting or running'], ['done', 'Done'], ['failed', 'Failed'], ['stopped', 'Stopped']] },
  by: { label: 'Made by', options: [['', 'Anyone'], ['me', 'You'], ['claude', 'Claude']] },
  turbos: { label: 'Turbos', options: [['', 'Any'], ['recorded', 'Recorded'], ['modelled', 'Made up']] },
  code: { label: 'Code', options: [['', 'Any'], ['current', 'Today’s'], ['older', 'Older']] },
}

/**
 * The list narrowed by state, by who made the runs, by experiment, turbos and
 * code: in the address, so that a list narrowed can be opened elsewhere, kept or
 * sent. Search and sort stay the table's.
 */
function RunFilters({
  filter,
  experiments,
  onChange,
}: {
  filter: RunFilter
  experiments: { id: string; title: string }[]
  onChange: (name: keyof RunFilter, value: string) => void
}) {
  const narrowed = RUN_FILTER_NAMES.some((name) => filter[name] !== null)
  const field = (name: keyof RunFilter, label: string, options: [string, string][]) => (
    <label key={name} className="flex flex-col gap-1 text-xs">
      <span className="text-text-secondary">{label}</span>
      <Select
        value={filter[name] ?? ''}
        onChange={(event) => onChange(name, event.target.value)}
        options={options.map(([value, text]) => ({ value, label: text }))}
      />
    </label>
  )
  return (
    <div role="group" aria-label="Narrow the list" className="flex flex-wrap items-end gap-3">
      {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((name) => field(name, FILTERS[name].label, FILTERS[name].options))}
      {experiments.length > 0 &&
        field('experiment', 'Experiment', [['', 'Any'], ...experiments.map((entry): [string, string] => [entry.id, entry.title])])}
      {narrowed && (
        <ButtonLink to="/backtests" variant="ghost" size="sm">
          Every run
        </ButtonLink>
      )}
    </div>
  )
}

/** A run that waits or runs can neither be compared nor removed: it has no box. */
const selectableRun = (run: BacktestRunListed) => !isActive(run.status.state)
const runName = (run: BacktestRunListed) => run.name

function runColumns(
  timezone: string,
  earlier: ReadonlyMap<string, BacktestRunListed[]>,
  removeAttempts: (attempts: BacktestRunListed[]) => void,
): ColumnDef<BacktestRunListed>[] {
  return [
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
          <Attempts attempts={earlier.get(row.id) ?? []} timezone={timezone} onRemove={removeAttempts} />
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
            {/* On two lines rather than one: on one, it made the column the widest of the figures. */}
            {row.headline?.sample === 'too_few' && <p className="whitespace-normal text-xs text-warning">too few to conclude</p>}
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

/** The failed runs of the same request, folded under the run that came after them. */
function Attempts({
  attempts,
  timezone,
  onRemove,
}: {
  attempts: BacktestRunListed[]
  timezone: string
  onRemove: (attempts: BacktestRunListed[]) => void
}) {
  if (attempts.length === 0) return null
  return (
    <details className="mt-1 text-xs">
      <summary className="cursor-pointer text-muted-foreground">
        {attempts.length} earlier failed attempt{attempts.length === 1 ? '' : 's'}
      </summary>
      <ul className="mt-1 flex flex-col gap-1">
        {attempts.map((attempt) => (
          <li key={attempt.id}>
            <Link to={`/backtests/${attempt.id}`} className="underline-offset-2 hover:underline">
              {formatDateTime(attempt.status.created_at, timezone, false)}
            </Link>
            <span className="line-clamp-2 text-muted-foreground" title={failureLine(attempt)}>
              {failureLine(attempt)}
            </span>
          </li>
        ))}
      </ul>
      <Button variant="ghost" size="sm" className="mt-1" onClick={() => onRemove(attempts)}>
        Remove them
      </Button>
    </details>
  )
}

export function BacktestsPage() {
  const { timezone, backtests_enabled: enabled } = useSession()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const filter = runFilterOf(params)
  // Changing what is shown starts the table over: its pages, sort and search were of another list.
  const shownKey = RUN_FILTER_NAMES.map((name) => filter[name] ?? '').join('|')
  const setFilter = (name: keyof RunFilter, value: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value) next.set(name, value)
        else next.delete(name)
        return next
      },
      { replace: true },
    )
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  // The runs the removal dialog is about: those selected, or a run's earlier failed attempts.
  const [removing, setRemoving] = useState<BacktestRunListed[] | null>(null)
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
  // Names for the experiments the runs belong to.
  const experimentsQuery = useQuery({ queryKey: ['experiments'], queryFn: api.experiments, enabled, staleTime: 60_000 })
  const experiments = useMemo(() => {
    const titles = new Map((experimentsQuery.data?.experiments ?? []).map((entry: Experiment) => [entry.id, entry.title]))
    const ids = [...new Set((runs ?? []).flatMap((run) => (run.experiment ? [run.experiment] : [])))]
    return ids.map((id) => ({ id, title: titles.get(id) ?? id }))
  }, [runs, experimentsQuery.data])
  const { shown, earlier } = useMemo(() => collapseAttempts(filterRuns(runs ?? [], filter)), [runs, shownKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const columns = useMemo(
    () =>
      runColumns(timezone, earlier, (attempts) => {
        removeMutation.reset()
        setRemoving(attempts)
      }),
    // The mutation's reset is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [timezone, earlier],
  )
  // A run removed meanwhile, or now waiting or running, is no longer selected.
  const chosen = useMemo(() => (runs ?? []).filter((run) => selected.has(run.id) && selectableRun(run)), [runs, selected])
  const chosenIds = useMemo(() => new Set(chosen.map((run) => run.id)), [chosen])
  const compared = comparable(runs ?? [], chosenIds)
  const canCompare = compared.length >= 2 && compared.length <= MAX_COMPARED

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtests'] })
  const removeMutation = useMutation({
    mutationFn: (ids: string[]) => api.removeBacktests(ids),
    onSuccess: (outcome) => {
      void refresh()
      // What was removed is no longer selected; what could not be stays in the dialog, with why.
      const removed = new Set(outcome.removed)
      const kept = new Set(outcome.kept.map((entry) => entry.id))
      setSelected((current) => new Set([...current].filter((id) => !removed.has(id))))
      setRemoving(kept.size === 0 ? null : (current) => current?.filter((run) => kept.has(run.id)) ?? null)
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
                {runs.length > 0 && <RunFilters filter={filter} experiments={experiments} onChange={setFilter} />}
                <AdvancedDataTable<BacktestRunListed>
                  key={shownKey}
                  title="Runs"
                  searchPlaceholder="Search…"
                  emptyTitle={runs.length > 0 ? 'No run of these' : 'No backtest yet'}
                  emptyDescription={
                    runs.length > 0
                      ? 'No run is left once narrowed so: “Every run” shows them all again.'
                      : 'Ask for one with “New backtest”: a Pine script or the signals WATA received, on days the price recorder kept turbos.'
                  }
                  data={shown}
                  columns={columns}
                  getRowId={(row) => row.id}
                  defaultPageSize={10}
                  rowLabel={runName}
                  isRowSelectable={selectableRun}
                  selectedRowIds={chosenIds}
                  onSelectionChange={setSelected}
                  selectionBasket={{
                    noun: ['run', 'runs'],
                    linkComponent: RouterLink,
                    actions: [
                      {
                        id: 'compare',
                        label: canCompare ? `Compare ${compared.length}` : 'Compare',
                        icon: <Columns3 className="size-3.5" />,
                        href: canCompare ? `/backtests/compare?runs=${compared.map((run) => run.id).join(',')}` : undefined,
                        disabled: !canCompare,
                        title: canCompare ? undefined : `Two to ${MAX_COMPARED} finished runs can be compared`,
                      },
                      {
                        id: 'remove',
                        label: `Remove ${chosen.length}`,
                        icon: <Trash2 className="size-3.5" />,
                        variant: 'danger',
                        onClick: () => {
                          removeMutation.reset()
                          setRemoving(chosen)
                        },
                      },
                    ],
                  }}
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
        open={removing !== null}
        onClose={() => setRemoving(null)}
        title={`Remove ${removing?.length ?? 0} backtest${removing?.length === 1 ? '' : 's'}?`}
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRemoving(null)}>
              Keep them
            </Button>
            <Button
              variant="destructive"
              size="sm"
              isLoading={removeMutation.isPending}
              onClick={() => removing && removeMutation.mutate(removing.map((run) => run.id))}
            >
              Remove
            </Button>
          </>
        }
      >
        <p className="text-sm leading-relaxed text-text-secondary">
          Their signals, trades and days are deleted from the server. The recorded prices they ran on are kept.
        </p>
        <ul className="mt-3 flex max-h-48 flex-col gap-1 overflow-auto text-sm">
          {(removing ?? []).map((run) => (
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
