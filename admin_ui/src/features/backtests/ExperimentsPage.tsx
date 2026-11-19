import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  AnimatedTabs,
  Badge,
  Button,
  formatDateTime,
  formatDay,
  formatNumber,
  FormItem,
  FormLabel,
  Input,
  PageHeader,
  Panel,
  QueryState,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { RouterLink } from 'ioiti-ui/react-router'
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { ClaudeBadge } from '@/components/status'
import { useSession } from '@/features/auth/auth-context'
import { madeByClaude } from '@/features/system/claude'
import { api } from '@/lib/api'
import type { Experiment, ExperimentReview, ResearchLockbox } from '@/lib/types'

import {
  BAR_SAID,
  criterionText,
  EXPERIMENTS_TABS,
  experimentsTab,
  familyTrials,
  forwardCheckPath,
  forwardSentence,
  forwardTone,
  lockboxText,
  REVIEW_SAID,
  RUNS_SHOWN,
  VERDICT,
} from './experiments'
import { ChallengersBoard } from './ChallengersBoard'
import { ForwardBoard } from './ForwardBoard'
import { RunPeekLink } from './RunPeek'
import { TrackRecordPanel } from './TrackRecordPanel'

function experimentColumns(timezone: string, trials: Map<string, number>): ColumnDef<Experiment>[] {
  return [
    {
      id: 'experiment',
      header: 'Experiment',
      accessorFn: (row) => `${row.title} ${row.hypothesis} ${row.family}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[30rem] whitespace-normal">
          <span className="font-medium">{row.title}</span>
          {madeByClaude(row) && <ClaudeBadge className="ml-2" />}
          <p className="text-xs text-muted-foreground">{row.hypothesis}</p>
          {row.plan && <p className="mt-1 text-xs text-muted-foreground">Plan: {row.plan}</p>}
          {row.motivated_by && row.motivated_by.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              Its idea came from {row.motivated_by.map((day) => formatDay(day)).join(', ')}: its runs are judged without them too.
            </p>
          )}
          {row.criteria && row.criteria.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">Set before any run: {row.criteria.map(criterionText).join('; ')}.</p>
          )}
        </div>
      ),
    },
    {
      id: 'family',
      header: 'Family',
      accessorKey: 'family',
      sortable: true,
      cell: ({ row }) => (
        <div className="whitespace-nowrap">
          <Badge tone="neutral">{row.family}</Badge>
          <p className="mt-1 text-xs text-muted-foreground">{trials.get(row.family) ?? 0} tried in all</p>
        </div>
      ),
    },
    {
      id: 'opened',
      header: 'Opened',
      accessorKey: 'opened_at',
      sortable: true,
      cell: ({ row }) => formatDateTime(row.opened_at, timezone, false),
    },
    {
      id: 'runs',
      header: 'Runs',
      accessorFn: (row) => row.runs.length,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-64 whitespace-normal text-xs">
          <span className="tabular-nums">
            {row.runs.length > 0 ? (
              // Every one of them, in the list of runs narrowed to this experiment.
              <Link to={`/backtests?experiment=${row.id}`} className="underline-offset-2 hover:underline">
                {row.runs.length} run{row.runs.length === 1 ? '' : 's'}
              </Link>
            ) : (
              '0 runs'
            )}
            , {row.trials} tried
          </span>
          <ul className="mt-1 flex flex-col gap-0.5">
            {row.runs.slice(-RUNS_SHOWN).map((entry) => (
              <li key={`${entry.run}-${entry.at}`}>
                <RunPeekLink id={entry.run} className="font-mono underline-offset-2 hover:underline">
                  {entry.run}
                </RunPeekLink>{' '}
                <span className="text-muted-foreground">
                  {entry.kind}
                  {entry.lockbox && ', on the lock box'}
                </span>
              </li>
            ))}
            {row.elsewhere.map((entry) => (
              <li key={`elsewhere-${entry.at}`} className="line-clamp-3 text-muted-foreground" title={entry.note}>
                +{formatNumber(entry.trials)} tried elsewhere: {entry.note}
              </li>
            ))}
          </ul>
        </div>
      ),
    },
    {
      id: 'verdict',
      header: 'Conclusion',
      accessorFn: (row) => (row.verdict ? VERDICT[row.verdict].label : 'Open'),
      sortable: true,
      filterable: true,
      filterOptions: ['Open', ...Object.values(VERDICT).map((entry) => entry.label)].map((label) => ({ value: label, label })),
      cell: ({ row }) => (
        <div className="min-w-0 max-w-80 whitespace-normal">
          {row.verdict ? <Badge tone={VERDICT[row.verdict].tone}>{VERDICT[row.verdict].label}</Badge> : <Badge tone="neutral" dot>Open</Badge>}
          {row.notes && <p className="mt-1 text-xs text-muted-foreground">{row.notes}</p>}
        </div>
      ),
    },
    {
      id: 'candidate',
      header: 'Candidate',
      accessorFn: (row) => row.checks?.bar ?? '',
      sortable: true,
      cell: ({ row }) =>
        row.candidate ? (
          <div className="min-w-0 max-w-64 whitespace-normal text-xs">
            <RunPeekLink id={row.candidate} className="font-mono underline-offset-2 hover:underline">
              {row.candidate}
            </RunPeekLink>
            {row.checks?.bar && (
              <div className="mt-1">
                <Badge tone={BAR_SAID[row.checks.bar].tone}>{BAR_SAID[row.checks.bar].label}</Badge>
              </div>
            )}
            {row.checks && row.checks.failed.length > 0 && <p className="mt-1 text-muted-foreground">Failed: {row.checks.failed.join(', ')}</p>}
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      id: 'forward',
      header: 'On later days',
      accessorFn: (row) => row.forward_check?.verdict ?? '',
      sortable: true,
      cell: ({ row }) =>
        row.forward_check ? (
          <div className="min-w-0 max-w-64 whitespace-normal text-xs">
            <Link to={forwardCheckPath(row.forward_check.id)} className="underline-offset-2 hover:underline">
              <Badge tone={forwardTone(row.forward_check.verdict)}>{row.forward_check.state === 'active' ? 'Judged' : 'Was judged'}</Badge>
            </Link>
            <p className="mt-1 text-muted-foreground">{forwardSentence(row.forward_check)}</p>
            {row.review && <ReviewLine review={row.review} />}
          </div>
        ) : row.review ? (
          <div className="min-w-0 max-w-64 whitespace-normal text-xs">
            <ReviewLine review={row.review} />
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
  ]
}

/** A skeptic's review of a kept conclusion, linked to its conversation. */
function ReviewLine({ review }: { review: ExperimentReview }) {
  const said = REVIEW_SAID[review.tone ?? 'none']
  return (
    <div className="mt-1.5">
      <Link to={`/agent/${encodeURIComponent(review.conversation)}`} className="underline-offset-2 hover:underline">
        <Badge tone={said.tone}>{said.label}</Badge>
      </Link>
      {review.line && <p className="mt-1 text-muted-foreground">{review.line}</p>}
    </div>
  )
}

function LockboxPanel({ box }: { box: ResearchLockbox | null }) {
  const queryClient = useQueryClient()
  const [start, setStart] = useState(box?.from ?? '')
  const [end, setEnd] = useState(box?.to ?? '')
  const saveMutation = useMutation({
    mutationFn: api.setLockbox,
    onSuccess: (saved) => {
      void queryClient.invalidateQueries({ queryKey: ['experiments'] })
      setStart(saved.lockbox?.from ?? '')
      setEnd(saved.lockbox?.to ?? '')
    },
  })

  return (
    <Panel
      title="Lock box"
      description="Days no study or backtest of Claude's may read, kept for a candidate's last check, which the journal notes. Claude may only widen it; you may move it or clear it."
    >
      <div className="flex flex-col gap-4">
        <p className="text-sm">
          {lockboxText(box)}
          {box?.set_by && (
            <span className="text-muted-foreground">
              {' '}
              Set by {box.set_by === 'claude' ? 'Claude' : 'you'}
              {box.set_at && `, ${formatDay(box.set_at.slice(0, 10))}`}.
            </span>
          )}
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <FormItem className="w-full sm:w-48">
            <FormLabel htmlFor="lockbox-from">From</FormLabel>
            <Input id="lockbox-from" type="date" value={start} onChange={(event) => setStart(event.target.value)} />
          </FormItem>
          <FormItem className="w-full sm:w-56">
            <FormLabel htmlFor="lockbox-to">To (empty: every day after)</FormLabel>
            <Input id="lockbox-to" type="date" value={end} min={start || undefined} onChange={(event) => setEnd(event.target.value)} />
          </FormItem>
          <Button
            size="sm"
            disabled={!start}
            isLoading={saveMutation.isPending}
            onClick={() => saveMutation.mutate({ start, end: end || null })}
          >
            Save the lock box
          </Button>
          {box && (
            <Button variant="ghost" size="sm" onClick={() => saveMutation.mutate({ start: null, end: null })}>
              Clear it
            </Button>
          )}
        </div>
        {saveMutation.error && (
          <Alert tone="danger">
            <AlertDescription>{saveMutation.error.message}</AlertDescription>
          </Alert>
        )}
      </div>
    </Panel>
  )
}

/**
 * The experiment journal the AI agent and Claude keep on the server, and the lock box (``?tab=journal``); each kept
 * conclusion judged on the days after it (``?tab=forward``), and against the live configuration on the same days
 * (``?tab=challengers``); how each family's and maker's conclusions held (``?tab=record``).
 */
export function ExperimentsPage() {
  const { timezone } = useSession()
  const [params, setParams] = useSearchParams()
  const tab = experimentsTab(params.get('tab'))
  const experimentsQuery = useQuery({ queryKey: ['experiments'], queryFn: api.experiments, refetchInterval: 30_000 })
  const found = experimentsQuery.data
  const columns = useMemo(
    () => experimentColumns(timezone, familyTrials(found?.experiments ?? [])),
    [timezone, found],
  )
  const setTab = (next: string) => {
    const changed = new URLSearchParams(params)
    if (next === 'journal') changed.delete('tab')
    else changed.set('tab', next)
    setParams(changed, { replace: true })
  }

  return (
    <>
      <PageHeader
        title="Experiments"
        description="The journal of the research: each idea with its hypothesis, the runs that tried it, how many configurations they tried, what was concluded, and how what was kept held on the days after it. Nothing here trades."
      />
      <AnimatedTabs
        tabs={EXPERIMENTS_TABS.map((entry) => ({
          id: entry.id,
          label: entry.label,
          href: entry.id === 'journal' ? '/backtests/experiments' : `/backtests/experiments?tab=${entry.id}`,
          // The checks running, said only when there are some.
          badge: entry.id === 'forward' && found?.forward?.active ? found.forward.active : undefined,
        }))}
        activeTab={tab}
        onChange={setTab}
        linkComponent={RouterLink}
        renderContent={false}
      />
      <QueryState isPending={experimentsQuery.isPending} error={experimentsQuery.error} hasData={!!found}>
        {found && tab === 'forward' && <ForwardBoard experiments={found.experiments} />}
        {found && tab === 'challengers' && <ChallengersBoard />}
        {found && tab === 'record' && found.track_record && <TrackRecordPanel record={found.track_record} />}
        {found && tab === 'journal' && (
          <div className="flex flex-col gap-6">
            <LockboxPanel key={`${found.lockbox?.from ?? ''}-${found.lockbox?.to ?? ''}`} box={found.lockbox} />
            <AdvancedDataTable<Experiment>
              title="Experiments"
              searchPlaceholder="Search an idea…"
              emptyTitle="No experiment yet"
              emptyDescription="Claude opens one before each backtest it asks for (exp_new), and concludes it when it is done."
              data={found.experiments}
              columns={columns}
              getRowId={(row) => row.id}
              defaultPageSize={10}
              selectable={false}
            />
          </div>
        )}
      </QueryState>
    </>
  )
}
