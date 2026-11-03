// The table of runs: the Backtests page's, and a script's page's (its Backtests tab). Each run with what
// it ran and made, its verdict from the list itself (verdict.ts), the runs it made again and the failed
// attempts folded under it, its star among the favourites, a basket to compare, put among the favourites
// or remove what is chosen.

import { AdvancedDataTable, Badge, Button, cn, formatDateTime, formatDay, formatNumber, formatPercent, Hint, SignedValue } from 'ioiti-ui'
import type { BadgeTone, CheckState, ColumnDef } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Columns3, Star, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { FavouriteStar } from '@/components/FavouriteStar'
import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import type { BacktestRunListed } from '@/lib/types'

import { collapseAttempts, comparable, failureLine, favouriteAction, isActive, sourceLabel, STATE_LABEL } from './model'
import { StateBadge } from './parts'
import { useHiddenRuns, useRemoveRuns } from './pending-removals-context'
import { useSetFavourites } from './useFavourites'
import type { Judged } from './verdict'

const VERDICT_BADGE: Record<CheckState, { tone: BadgeTone; label: (open: number) => string }> = {
  pass: { tone: 'success', label: () => 'Passes' },
  open: { tone: 'neutral', label: (open) => `${open} open` },
  fail: { tone: 'danger', label: () => 'Fails' },
}

/** The verdicts in an order to sort by: what passes first, what fails last. */
const VERDICT_RANK: Record<CheckState, number> = { pass: 3, open: 2, fail: 1 }

/** A run that waits or runs can neither be compared nor removed: it has no box. */
const selectableRun = (run: BacktestRunListed) => !isActive(run.status.state)
const runName = (run: BacktestRunListed) => run.name


/** Whether a listed run can be believed, check by check: the badge says it, its hint and its page say why. */
function VerdictCell({ run, judged, judges }: { run: BacktestRunListed; judged: Judged | null; judges: BacktestRunListed | null }) {
  const judgedId = run.stress_of ?? run.model_check_of
  if (judgedId) {
    // The run it judges is named when it is listed; a paper day is not, and its page says if it is gone.
    return (
      <span className="text-xs whitespace-normal text-muted-foreground">
        {run.stress_of ? 'Stresses' : 'Checks'}{' '}
        <Link to={`/backtests/${judgedId}`} className="underline-offset-2 hover:underline">
          {judges?.name ?? 'its run'}
        </Link>
      </span>
    )
  }
  if (!judged) return <span className="text-muted-foreground">-</span>
  const { checks, verdict } = judged
  const open = checks.filter((check) => check.state === 'open').length
  const passed = checks.filter((check) => check.state === 'pass').length
  const badge = VERDICT_BADGE[verdict.tone]
  return (
    <Hint content={verdict.sentence}>
      <Link to={`/backtests/${run.id}#trust`} className="inline-flex flex-col items-start gap-0.5 rounded-md">
        <Badge tone={badge.tone}>{badge.label(open)}</Badge>
        <span className="text-xs text-muted-foreground">
          {passed} of {checks.length} checks
        </span>
      </Link>
    </Hint>
  )
}

function runColumns(
  timezone: string,
  earlier: ReadonlyMap<string, BacktestRunListed[]>,
  before: ReadonlyMap<string, BacktestRunListed[]>,
  judged: ReadonlyMap<string, Judged>,
  byId: ReadonlyMap<string, BacktestRunListed>,
  remaking: ReadonlyMap<string, BacktestRunListed>,
  tagHref: (tag: string) => string,
  removeAttempts: (attempts: BacktestRunListed[]) => void,
  toggleFavourite: (run: BacktestRunListed) => void,
): ColumnDef<BacktestRunListed>[] {
  return [
    {
      id: 'run',
      header: 'Backtest',
      // Searched by its name, what it runs and its tags, "Claude" for Claude's and "favourite" for a favourite.
      accessorFn: (row) =>
        [row.name, sourceLabel(row), ...row.tags, ...(madeByClaude(row) ? ['Claude'] : []), ...(row.favourite ? ['favourite'] : [])].join(' '),
      sortable: true,
      cell: ({ row }) => (
        // Wide enough in the table to read a name on two lines at most: past it, the table scrolls in its frame.
        // Its floor in the table only: a card on a phone is narrower.
        <div className="max-w-96 whitespace-normal [td_&]:min-w-56">
          <FavouriteStar on={Boolean(row.favourite)} name={row.name} onToggle={() => toggleFavourite(row)} className="-ml-1 mr-1 align-[-0.4em]" />
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
              <Link key={tag} to={tagHref(tag)} aria-label={`Only the runs tagged ${tag}`} className="rounded-full">
                <Badge tone="neutral">{tag}</Badge>
              </Link>
            ))}
            {row.stale && (
              <Hint content="Made by other code than this admin's: it could come out otherwise today" wrap>
                <span className="text-warning">made with other code</span>
              </Hint>
            )}
            {row.remade_from && <span>made again on today’s code</span>}
          </p>
          {remaking.has(row.id) && (
            <p className="text-xs text-muted-foreground">
              Being made again on today’s code:{' '}
              <Link to={`/backtests/${remaking.get(row.id)!.id}`} className="underline-offset-2 hover:underline">
                {STATE_LABEL[remaking.get(row.id)!.status.state].toLowerCase()}
              </Link>
            </p>
          )}
          <Before runs={before.get(row.id) ?? []} timezone={timezone} onRemove={removeAttempts} />
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
      id: 'verdict',
      header: 'Verdict',
      // What passes first; the runs not judged (not done, or judging another) after those that fail.
      accessorFn: (row) => {
        const found = judged.get(row.id)
        return found ? VERDICT_RANK[found.verdict.tone] : null
      },
      sortable: true,
      cell: ({ row }) => (
        <VerdictCell run={row} judged={judged.get(row.id) ?? null} judges={byId.get(row.stress_of ?? row.model_check_of ?? '') ?? null} />
      ),
    },
    {
      id: 'days',
      header: 'Days',
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
      cell: ({ row }) => <SignedValue value={row.headline?.total_percent} kind="percent" />,
    },
    {
      id: 'money',
      header: 'In money',
      accessorFn: (row) => row.headline?.profit_loss ?? null,
      sortable: true,
      align: 'right',
      cell: ({ row }) => <SignedValue value={row.headline?.profit_loss} kind="money" />,
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

/** The runs a run made again on today's code, folded under it with what they found on their older code. */
function Before({
  runs,
  timezone,
  onRemove,
}: {
  runs: BacktestRunListed[]
  timezone: string
  onRemove: (runs: BacktestRunListed[]) => void
}) {
  if (runs.length === 0) return null
  return (
    <details className="mt-1 text-xs">
      <summary className="cursor-pointer text-muted-foreground">
        The run{runs.length === 1 ? '' : 's'} before, made with older code
      </summary>
      <ul className="mt-1 flex flex-col gap-1">
        {runs.map((run) => (
          <li key={run.id} className="flex flex-wrap items-baseline gap-x-2">
            <Link to={`/backtests/${run.id}`} className="underline-offset-2 hover:underline">
              {formatDateTime(run.status.created_at, timezone, false)}
            </Link>
            <span className="text-muted-foreground">
              {run.headline?.total_percent !== null && run.headline?.total_percent !== undefined
                ? `${formatPercent(run.headline.total_percent, true)} in ${formatNumber(run.headline.trades ?? 0)} trades`
                : 'no figures'}
            </span>
          </li>
        ))}
      </ul>
      <Button variant="ghost" size="sm" className="mt-1" onClick={() => onRemove(runs)}>
        Remove {runs.length === 1 ? 'it' : 'them'}
      </Button>
    </details>
  )
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

/**
 * The runs of ``rows`` (narrowed by the page), folded and judged with ``all``,
 * every run listed with them: what made whom again, what each stresses or
 * checks, what is being made again.
 */
export function RunsTable({
  rows,
  all,
  judged,
  tableKey,
  tagHref,
  emptyTitle,
  emptyDescription,
}: {
  rows: readonly BacktestRunListed[]
  all: readonly BacktestRunListed[]
  judged: ReadonlyMap<string, Judged>
  /** What is shown: a change starts the table over, its pages, sort and search being of another list. */
  tableKey: string
  /** Where a tag of a row leads: the runs of that tag. */
  tagHref: (tag: string) => string
  emptyTitle: string
  emptyDescription: string
}) {
  const { timezone } = useSession()
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const hidden = useHiddenRuns()
  const removeRuns = useRemoveRuns()
  const { mutate: favour } = useSetFavourites()
  const toggleFavourite = useCallback((run: BacktestRunListed) => favour({ ids: [run.id], favourite: !run.favourite }), [favour])
  // Without the runs whose removal waits for its Undo, or is on its way.
  const listed = useMemo(() => all.filter((run) => !hidden.has(run.id)), [all, hidden])
  const byId = useMemo(() => new Map(listed.map((run) => [run.id, run])), [listed])
  // The runs being made again on today's code, by the run each makes again.
  const remaking = useMemo(
    () => new Map(listed.flatMap((run) => (run.remade_from && isActive(run.status.state) ? [[run.remade_from, run] as const] : []))),
    [listed],
  )
  const { shown, earlier, before } = useMemo(() => collapseAttempts(rows.filter((run) => !hidden.has(run.id))), [rows, hidden])

  // Selected runs, or a run's earlier failed attempts: gone from the list at once, with an Undo.
  const remove = useCallback(
    (removed: BacktestRunListed[]) => {
      removeRuns(removed.map((run) => ({ id: run.id, name: run.name })))
      const ids = new Set(removed.map((run) => run.id))
      setSelected((current) => new Set([...current].filter((id) => !ids.has(id))))
    },
    [removeRuns, setSelected],
  )
  const columns = useMemo(
    () => runColumns(timezone, earlier, before, judged, byId, remaking, tagHref, remove, toggleFavourite),
    [timezone, earlier, before, judged, byId, remaking, tagHref, remove, toggleFavourite],
  )
  // A run removed meanwhile, or now waiting or running, is no longer selected.
  const chosen = useMemo(() => listed.filter((run) => selected.has(run.id) && selectableRun(run)), [listed, selected])
  const chosenIds = useMemo(() => new Set(chosen.map((run) => run.id)), [chosen])
  const compared = comparable(listed, chosenIds)
  const canCompare = compared.length >= 2 && compared.length <= MAX_COMPARED
  // Out of the favourites once every run chosen is in them, else in.
  const favouring = favouriteAction(chosen)

  return (
    <AdvancedDataTable<BacktestRunListed>
      key={tableKey}
      title="Runs"
      searchPlaceholder="Search…"
      emptyTitle={emptyTitle}
      emptyDescription={emptyDescription}
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
            id: 'favourite',
            label: favouring.label,
            icon: <Star className={cn('size-3.5', !favouring.favourite && 'fill-current')} />,
            onClick: () => favour({ ids: chosen.map((run) => run.id), favourite: favouring.favourite }),
          },
          {
            id: 'remove',
            label: `Remove ${chosen.length}`,
            icon: <Trash2 className="size-3.5" />,
            variant: 'danger',
            onClick: () => remove(chosen),
          },
        ],
      }}
    />
  )
}
