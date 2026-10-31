import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Badge,
  Button,
  ButtonLink,
  formatBytes,
  formatDateTime,
  formatDay,
  formatDuration,
  formatNumber,
  formatPercent,
  Hint,
  PageHeader,
  QueryState,
  Select,
  SettleModal,
  SignedValue,
} from 'ioiti-ui'
import type { BadgeTone, CheckState, ColumnDef } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { Columns3, FlaskConical, Newspaper, Plus, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { api } from '@/lib/api'
import type { BacktestRemaking, BacktestRunListed, Experiment } from '@/lib/types'

import {
  collapseAttempts,
  comparable,
  failureLine,
  filterRuns,
  isActive,
  remakable,
  RUN_FILTER_NAMES,
  runFilterOf,
  sourceLabel,
  STATE_LABEL,
  tagsInUse,
} from './model'
import type { RunFilter } from './model'
import { listedChecks, verdictOf } from './verdict'
import type { RunCheck, RunVerdict } from './verdict'
import { BacktestsDisabled, StateBadge } from './parts'
import { useHiddenRuns, useRemoveRuns } from './pending-removals-context'
import { ResearchFiles } from './ResearchFiles'
import { TurboModelPanel } from './TurboModelPanel'

/** What each filter of the list offers: the first, empty, leaves the list whole. */
const FILTERS: Record<Exclude<keyof RunFilter, 'experiment' | 'tag'>, { label: string; options: [string, string][] }> = {
  state: { label: 'State', options: [['', 'Any'], ['active', 'Waiting or running'], ['done', 'Done'], ['failed', 'Failed'], ['stopped', 'Stopped']] },
  verdict: { label: 'Verdict', options: [['', 'Any'], ['pass', 'Passes'], ['open', 'Checks open'], ['fail', 'Fails']] },
  by: { label: 'Made by', options: [['', 'Anyone'], ['me', 'You'], ['claude', 'Claude']] },
  turbos: { label: 'Turbos', options: [['', 'Any'], ['recorded', 'Recorded'], ['modelled', 'Made up']] },
  code: { label: 'Code', options: [['', 'Any'], ['current', 'Today’s'], ['older', 'Older']] },
}

/** A run's checks and what they say together, as its page says them (verdict.ts). */
interface Judged {
  checks: RunCheck[]
  verdict: RunVerdict
}

const VERDICT_BADGE: Record<CheckState, { tone: BadgeTone; label: (open: number) => string }> = {
  pass: { tone: 'success', label: () => 'Passes' },
  open: { tone: 'neutral', label: (open) => `${open} open` },
  fail: { tone: 'danger', label: () => 'Fails' },
}

/** The verdicts in an order to sort by: what passes first, what fails last. */
const VERDICT_RANK: Record<CheckState, number> = { pass: 3, open: 2, fail: 1 }

/**
 * The list narrowed by state, verdict, who made the runs, experiment, turbos,
 * code and tag: in the address, so that a list narrowed can be opened elsewhere,
 * kept or sent. Search and sort stay the table's.
 */
function RunFilters({
  filter,
  experiments,
  tags,
  onChange,
}: {
  filter: RunFilter
  experiments: { id: string; title: string }[]
  tags: string[]
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
      {(tags.length > 0 || filter.tag) &&
        field('tag', 'Tag', [['', 'Any'], ...[...new Set([...tags, ...(filter.tag ? [filter.tag] : [])])].map((tag): [string, string] => [tag, tag])])}
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
): ColumnDef<BacktestRunListed>[] {
  return [
    {
      id: 'run',
      header: 'Backtest',
      // Searched by its name, what it runs and its tags, and "Claude" for Claude's.
      accessorFn: (row) => [row.name, sourceLabel(row), ...row.tags, ...(madeByClaude(row) ? ['Claude'] : [])].join(' '),
      sortable: true,
      cell: ({ row }) => (
        // Wide enough to read a name on two lines at most: past it, the table scrolls in its frame.
        <div className="min-w-56 max-w-96 whitespace-normal">
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
 * The runs made with older code than today's, and their making again: offered
 * while some are, followed while they are made, said once they all were.
 */
function RemakeLine({
  stale,
  remaking,
  byId,
}: {
  stale: number
  remaking: BacktestRemaking | null
  byId: ReadonlyMap<string, BacktestRunListed>
}) {
  const queryClient = useQueryClient()
  const [asking, setAsking] = useState(false)
  const preview = useQuery({ queryKey: ['backtests-remake'], queryFn: api.remakePreview, enabled: asking })
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtests'] })
  const ask = useMutation({
    mutationFn: () => api.remakeBacktests({ stale: true }),
    onSuccess: () => {
      setAsking(false)
      void refresh()
    },
  })
  const stop = useMutation({ mutationFn: api.stopRemaking, onSuccess: () => void refresh() })
  const active = remaking !== null && (remaking.waiting > 0 || remaking.under_way.length > 0)

  let line = null
  if (remaking !== null && active) {
    const running = remaking.under_way.map((id) => byId.get(id)).filter((run): run is BacktestRunListed => Boolean(run))
    line = (
      <>
        <span>
          Making {remaking.total} run{remaking.total === 1 ? '' : 's'} again on today’s code: {remaking.made} made,{' '}
          {remaking.under_way.length} under way, {remaking.waiting} to go
          {remaking.failed.length > 0 && `, ${remaking.failed.length} could not be`}. Each waits until no other run does.
        </span>
        {running.map((run) => (
          <Link key={run.id} to={`/backtests/${run.id}`} className="underline-offset-2 hover:underline">
            {run.name}
          </Link>
        ))}
        <Button variant="ghost" size="sm" isLoading={stop.isPending} onClick={() => stop.mutate()}>
          Stop
        </Button>
      </>
    )
  } else if (remaking !== null) {
    line = (
      <>
        <span>
          {remaking.made} run{remaking.made === 1 ? ' was' : 's were'} made again on today’s code
          {remaking.failed.length > 0 &&
            `; ${remaking.failed.length} could not be, each said under the run it was to make again`}
          {remaking.stopped > 0 && `; ${remaking.stopped} stopped`}.
        </span>
        <Button variant="ghost" size="sm" isLoading={stop.isPending} onClick={() => stop.mutate()}>
          Dismiss
        </Button>
      </>
    )
  } else if (stale > 0) {
    line = (
      <>
        <span>
          {stale} run{stale === 1 ? ' was' : 's were'} made with older code than today’s: what {stale === 1 ? 'it' : 'they'} found may come
          out otherwise now.
        </span>
        <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
          Make {stale === 1 ? 'it' : 'them'} again
        </Button>
      </>
    )
  }
  const found = preview.data
  return (
    <>
      {line && (
        <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-secondary">
          {line}
        </div>
      )}
      <SettleModal
        open={asking}
        onClose={() => setAsking(false)}
        title="Make the runs made with older code again?"
        size="sm"
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>
              Not now
            </Button>
            <Button size="sm" isLoading={ask.isPending} disabled={!found || found.runs === 0} onClick={() => ask.mutate()}>
              Make {found?.runs === 1 ? 'it' : 'them'} again
            </Button>
          </>
        }
      >
        <QueryState isPending={preview.isPending} error={preview.error} hasData={Boolean(found)}>
          {found && (
            <div className="flex flex-col gap-2 text-sm leading-relaxed text-text-secondary">
              <p>
                {found.runs} run{found.runs === 1 ? '' : 's'}
                {found.claude > 0 && ` (${found.claude} of them Claude’s)`} {found.runs === 1 ? 'was' : 'were'} made with older code.
                Each is made again with its own settings, script and signals: only the code differs.
                {found.seconds > 0 && ` Their last makes took ${formatDuration(found.seconds * 1000)} together.`}
              </p>
              <p>
                They run one at a time, each once no other run waits: a run you ask for goes first. Nothing trades. The old runs
                stay, each folded under the run that made it again, and a rule’s reference moves to the new run.
              </p>
            </div>
          )}
        </QueryState>
        {ask.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {ask.error.message}
          </p>
        )}
      </SettleModal>
    </>
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
  const [clearing, setClearing] = useState(false)
  const hidden = useHiddenRuns()
  const removeRuns = useRemoveRuns()

  const runsQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    enabled,
    // A run that waits or runs changes state by itself: follow it closely.
    refetchInterval: (query) => (query.state.data?.items.some((run) => isActive(run.status.state)) ? 2_000 : 30_000),
  })
  // Without the runs whose removal waits for its Undo, or is on its way.
  const runs = useMemo(() => runsQuery.data?.items.filter((run) => !hidden.has(run.id)), [runsQuery.data, hidden])
  const storage = runsQuery.data?.storage
  // Names for the experiments the runs belong to.
  const experimentsQuery = useQuery({ queryKey: ['experiments'], queryFn: api.experiments, enabled, staleTime: 60_000 })
  const experiments = useMemo(() => {
    const titles = new Map((experimentsQuery.data?.experiments ?? []).map((entry: Experiment) => [entry.id, entry.title]))
    const ids = [...new Set((runs ?? []).flatMap((run) => (run.experiment ? [run.experiment] : [])))]
    return ids.map((id) => ({ id, title: titles.get(id) ?? id }))
  }, [runs, experimentsQuery.data])
  // Each run's checks, from the list itself, as its page makes them (verdict.ts).
  const judged = useMemo(() => {
    const found = new Map<string, Judged>()
    for (const run of runs ?? []) {
      const checks = listedChecks(run, runs ?? [])
      if (checks.length > 0) found.set(run.id, { checks, verdict: verdictOf(checks) })
    }
    return found
  }, [runs])
  const verdicts = useMemo(() => new Map([...judged].map(([id, found]) => [id, found.verdict.tone])), [judged])
  const byId = useMemo(() => new Map((runs ?? []).map((run) => [run.id, run])), [runs])
  // The runs being made again on today's code, by the run each makes again.
  const remaking = useMemo(
    () => new Map((runs ?? []).flatMap((run) => (run.remade_from && isActive(run.status.state) ? [[run.remade_from, run] as const] : []))),
    [runs],
  )
  const tags = useMemo(() => tagsInUse(runs ?? []), [runs])
  const { shown, earlier, before } = useMemo(() => collapseAttempts(filterRuns(runs ?? [], filter, verdicts)), [runs, shownKey, verdicts]) // eslint-disable-line react-hooks/exhaustive-deps

  // Selected runs, or a run's earlier failed attempts: gone from the list at once, with an Undo.
  const remove = useCallback(
    (removed: BacktestRunListed[]) => {
      removeRuns(removed.map((run) => ({ id: run.id, name: run.name })))
      const ids = new Set(removed.map((run) => run.id))
      setSelected((current) => new Set([...current].filter((id) => !ids.has(id))))
    },
    [removeRuns, setSelected],
  )
  // A tag of a row narrows the list to it, the rest of the address kept.
  const tagHref = useCallback(
    (tag: string) => {
      const next = new URLSearchParams(params)
      next.set('tag', tag)
      return `/backtests?${next.toString()}`
    },
    [params],
  )
  const columns = useMemo(
    () => runColumns(timezone, earlier, before, judged, byId, remaking, tagHref, remove),
    [timezone, earlier, before, judged, byId, remaking, tagHref, remove],
  )
  // A run removed meanwhile, or now waiting or running, is no longer selected.
  const chosen = useMemo(() => (runs ?? []).filter((run) => selected.has(run.id) && selectableRun(run)), [runs, selected])
  const chosenIds = useMemo(() => new Set(chosen.map((run) => run.id)), [chosen])
  const compared = comparable(runs ?? [], chosenIds)
  const canCompare = compared.length >= 2 && compared.length <= MAX_COMPARED

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['backtests'] })
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
        description="A Pine script, the signals WATA received or those a rule raised, traded as WATA trades on the turbos the price recorder kept. Nothing here trades."
        actions={
          enabled && (
            <>
              <ButtonLink to="/backtests/paper" variant="outline" size="sm" leftIcon={<Newspaper className="size-3.5" />}>
                Paper trading
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
                {runs.length > 0 && <RunFilters filter={filter} experiments={experiments} tags={tags} onChange={setFilter} />}
                <RemakeLine stale={runs.filter(remakable).length} remaking={runsQuery.data?.remaking ?? null} byId={byId} />
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
                        onClick: () => remove(chosen),
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
