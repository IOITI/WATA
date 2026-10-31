import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button, ButtonLink, formatBytes, formatDuration, PageHeader, QueryState, Select, SettleModal } from 'ioiti-ui'
import { FlaskConical, Newspaper, Plus } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { MAX_COMPARED } from '@/components/charts/compare'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { BacktestRemaking, BacktestRunListed, Experiment } from '@/lib/types'

import { filterRuns, isActive, remakable, RUN_FILTER_NAMES, runFilterOf, tagsInUse } from './model'
import type { RunFilter } from './model'
import { BacktestsDisabled } from './parts'
import { useHiddenRuns } from './pending-removals-context'
import { ResearchFiles } from './ResearchFiles'
import { RunsTable } from './RunsTable'
import { TurboModelPanel } from './TurboModelPanel'
import { judgedRuns } from './verdict'

/** What each filter of the list offers: the first, empty, leaves the list whole. */
const FILTERS: Record<Exclude<keyof RunFilter, 'experiment' | 'tag'>, { label: string; options: [string, string][] }> = {
  state: { label: 'State', options: [['', 'Any'], ['active', 'Waiting or running'], ['done', 'Done'], ['failed', 'Failed'], ['stopped', 'Stopped']] },
  verdict: { label: 'Verdict', options: [['', 'Any'], ['pass', 'Passes'], ['open', 'Checks open'], ['fail', 'Fails']] },
  by: { label: 'Made by', options: [['', 'Anyone'], ['me', 'You'], ['claude', 'Claude']] },
  turbos: { label: 'Turbos', options: [['', 'Any'], ['recorded', 'Recorded'], ['modelled', 'Made up']] },
  code: { label: 'Code', options: [['', 'Any'], ['current', 'Today’s'], ['older', 'Older']] },
}

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
  const { backtests_enabled: enabled } = useSession()
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
  const [clearing, setClearing] = useState(false)
  const hidden = useHiddenRuns()

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
  const judged = useMemo(() => judgedRuns(runs ?? []), [runs])
  const verdicts = useMemo(() => new Map([...judged].map(([id, found]) => [id, found.verdict.tone])), [judged])
  const byId = useMemo(() => new Map((runs ?? []).map((run) => [run.id, run])), [runs])
  const tags = useMemo(() => tagsInUse(runs ?? []), [runs])
  const narrowed = useMemo(() => filterRuns(runs ?? [], filter, verdicts), [runs, shownKey, verdicts]) // eslint-disable-line react-hooks/exhaustive-deps

  // A tag of a row narrows the list to it, the rest of the address kept.
  const tagHref = useCallback(
    (tag: string) => {
      const next = new URLSearchParams(params)
      next.set('tag', tag)
      return `/backtests?${next.toString()}`
    },
    [params],
  )

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
                <RunsTable
                  rows={narrowed}
                  all={runs}
                  judged={judged}
                  tableKey={shownKey}
                  tagHref={tagHref}
                  emptyTitle={runs.length > 0 ? 'No run of these' : 'No backtest yet'}
                  emptyDescription={
                    runs.length > 0
                      ? 'No run is left once narrowed so: “Every run” shows them all again.'
                      : 'Ask for one with “New backtest”: a Pine script or the signals WATA received, on days the price recorder kept turbos.'
                  }
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
