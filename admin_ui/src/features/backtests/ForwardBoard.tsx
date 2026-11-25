import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  Badge,
  Button,
  formatDay,
  formatNumber,
  formatPercent,
  Panel,
  QueryState,
  Select,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { useMemo, useState } from 'react'
import { Link } from 'react-router'

import { VERDICT_LABEL } from '@/features/edgeWatch/edgeWatch'
import { api } from '@/lib/api'
import type { Experiment, ForwardCheck } from '@/lib/types'

import { ENDED_SAID, forwardCheckPath, forwardTone, reasonsSaid } from './experiments'
import { RunPeekLink } from './RunPeek'

const COLUMNS: ColumnDef<ForwardCheck>[] = [
  {
    id: 'experiment',
    header: 'Conclusion',
    accessorFn: (row) => `${row.title ?? ''} ${row.family ?? ''}`,
    sortable: true,
    cell: ({ row }) => (
      <div className="min-w-0 max-w-[26rem] whitespace-normal">
        <Link to={forwardCheckPath(row.id)} className="font-medium underline-offset-2 hover:underline">
          {row.title ?? row.experiment}
        </Link>
        <p className="text-xs text-muted-foreground">
          {row.family} · candidate{' '}
          <RunPeekLink id={row.candidate} className="font-mono underline-offset-2 hover:underline">
            {row.candidate}
          </RunPeekLink>
        </p>
      </div>
    ),
  },
  {
    id: 'from',
    header: 'Judged from',
    accessorKey: 'start',
    sortable: true,
    cell: ({ row }) => (
      <div className="whitespace-nowrap">
        {formatDay(row.start)}
        <p className="text-xs text-muted-foreground">
          {row.days_tested} day{row.days_tested === 1 ? '' : 's'} judged
        </p>
      </div>
    ),
  },
  {
    id: 'trades',
    header: 'Trades',
    accessorFn: (row) => row.judgement?.n ?? 0,
    sortable: true,
    cell: ({ row }) => <span className="tabular-nums">{formatNumber(row.judgement?.n ?? 0)}</span>,
  },
  {
    id: 'result',
    header: 'Result',
    accessorFn: (row) => row.judgement?.cumulative ?? null,
    sortable: true,
    cell: ({ row }) => (row.judgement ? <span className="tabular-nums">{formatPercent(row.judgement.cumulative, true)}</span> : '—'),
  },
  {
    id: 'verdict',
    header: 'On later days',
    accessorFn: (row) => (row.judgement ? VERDICT_LABEL[row.judgement.verdict] : 'Nothing judged yet'),
    sortable: true,
    filterable: true,
    cell: ({ row }) => (
      <div className="min-w-0 max-w-80 whitespace-normal">
        <Badge tone={forwardTone(row.judgement?.verdict ?? null)}>
          {row.judgement ? VERDICT_LABEL[row.judgement.verdict] : 'Nothing judged yet'}
        </Badge>
        {row.judgement?.reasons[0] && <p className="mt-1 text-xs text-muted-foreground">{reasonsSaid(row.judgement.reasons.slice(0, 1))}</p>}
      </div>
    ),
  },
  {
    id: 'state',
    header: 'State',
    accessorFn: (row) => (row.state === 'active' ? 'Running' : 'Ended'),
    sortable: true,
    filterable: true,
    cell: ({ row }) =>
      row.state === 'active' ? (
        <Badge tone="success" dot>
          Running
        </Badge>
      ) : (
        <div className="whitespace-nowrap">
          <Badge tone="neutral">Ended</Badge>
          {row.why && <p className="mt-1 text-xs text-muted-foreground">{ENDED_SAID[row.why]}</p>}
        </div>
      ),
  },
]

/** The concluded experiments a forward check may be started for: those without one running, with a run of their own. */
function startable(experiments: Experiment[], checks: ForwardCheck[]): Experiment[] {
  const running = new Set(checks.filter((check) => check.state === 'active').map((check) => check.experiment))
  return experiments.filter((entry) => entry.verdict !== null && !running.has(entry.id) && entry.runs.some((run) => run.kind === 'run'))
}

/** A concluded experiment's run judged on the days after it, at the operator's word: one concluded before forward checks, or stopped. */
function StartCheck({ experiments, checks, full }: { experiments: Experiment[]; checks: ForwardCheck[]; full: boolean }) {
  const queryClient = useQueryClient()
  const offered = useMemo(() => startable(experiments, checks), [experiments, checks])
  const [experimentId, setExperimentId] = useState('')
  const [candidate, setCandidate] = useState('')
  const chosen = offered.find((entry) => entry.id === experimentId) ?? null
  const runs = (chosen?.runs ?? []).filter((run) => run.kind === 'run')
  const start = useMutation({
    mutationFn: api.startForwardCheck,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['forward-checks'] })
      void queryClient.invalidateQueries({ queryKey: ['experiments'] })
      setExperimentId('')
      setCandidate('')
    },
  })
  if (offered.length === 0) return null
  return (
    <Panel
      title="Judge a conclusion on later days"
      description="A kept conclusion starts its forward check by itself. One concluded before, or stopped, can be judged here: its run asked again each evening on the days after the latest its family had seen."
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex w-full flex-col gap-1 text-xs sm:w-72">
          <span className="text-text-secondary">Experiment</span>
          <Select
            value={experimentId}
            onChange={(event) => {
              setExperimentId(event.target.value)
              setCandidate('')
            }}
            options={[{ value: '', label: 'Choose a conclusion…' }, ...offered.map((entry) => ({ value: entry.id, label: entry.title }))]}
          />
        </label>
        <label className="flex w-full flex-col gap-1 text-xs sm:w-64">
          <span className="text-text-secondary">Its run to judge</span>
          <Select
            value={candidate}
            disabled={!chosen}
            onChange={(event) => setCandidate(event.target.value)}
            options={[{ value: '', label: chosen ? 'Choose one of its runs…' : '—' }, ...runs.map((run) => ({ value: run.run, label: run.run }))]}
          />
        </label>
        <Button
          size="sm"
          disabled={!experimentId || !candidate || full}
          pending={start.isPending}
          onClick={() => start.mutate({ experiment: experimentId, candidate })}
        >
          Start its forward check
        </Button>
      </div>
      {full && <p className="mt-2 text-xs text-muted-foreground">Ten checks run already: one must end, or be stopped, first.</p>}
      {start.error && (
        <Alert tone="danger" className="mt-3">
          <AlertDescription>{start.error.message}</AlertDescription>
        </Alert>
      )}
    </Panel>
  )
}

/** Every forward check: each kept conclusion judged on the days after it, which no run of its family had seen. */
export function ForwardBoard({ experiments }: { experiments: Experiment[] }) {
  const checksQuery = useQuery({ queryKey: ['forward-checks'], queryFn: api.forwardChecks, refetchInterval: 60_000 })
  const found = checksQuery.data
  return (
    <QueryState isPending={checksQuery.isPending} error={checksQuery.error} hasData={!!found}>
      {found && (
        <div className="flex flex-col gap-6">
          <p className="text-sm text-muted-foreground">
            {found.active} running of {found.most} at most. Each kept conclusion&apos;s candidate is asked again each evening
            on the days after it, which no run of its family had seen, and judged by Edge Watch&apos;s model of it: as
            expected, on watch, broken, or better than expected.
          </p>
          <AdvancedDataTable<ForwardCheck>
            title="Forward checks"
            searchPlaceholder="Search a conclusion…"
            emptyTitle="No forward check yet"
            emptyDescription="An experiment concluded kept, with the run it keeps, starts one: that run asked again each evening on the days after it."
            data={found.checks}
            columns={COLUMNS}
            getRowId={(row) => row.id}
            defaultPageSize={10}
            selectable={false}
          />
          <StartCheck experiments={experiments} checks={found.checks} full={found.active >= found.most} />
        </div>
      )}
    </QueryState>
  )
}
