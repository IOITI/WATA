import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  AlertTitle,
  Badge,
  Button,
  ConfirmAction,
  formatDateTime,
  formatDay,
  formatNumber,
  formatPercent,
  PageHeader,
  Panel,
  QueryState,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { useSession } from '@/features/auth/auth-context'
import { VERDICT_LABEL } from '@/features/edgeWatch/edgeWatch'
import { TrackCone, TrackFigures } from '@/features/edgeWatch/EdgeWatchPanel'
import { api, isNotFound } from '@/lib/api'
import type { ForwardCheckLine } from '@/lib/types'

import { ENDED_SAID, FORWARD_CHECK_NOT_FOUND, forwardTone, reasonsSaid } from './experiments'
import { RunPeekLink } from './RunPeek'

function historyColumns(timezone: string): ColumnDef<ForwardCheckLine>[] {
  return [
    { id: 'at', header: 'Judged', accessorKey: 'at', sortable: true, cell: ({ row }) => formatDateTime(row.at, timezone, false) },
    { id: 'to', header: 'Up to', accessorFn: (row) => row.to ?? '', cell: ({ row }) => (row.to ? formatDay(row.to) : '—') },
    { id: 'days', header: 'Days', accessorKey: 'days_tested', cell: ({ row }) => <span className="tabular-nums">{row.days_tested}</span> },
    { id: 'n', header: 'Trades', accessorKey: 'n', cell: ({ row }) => <span className="tabular-nums">{formatNumber(row.n)}</span> },
    {
      id: 'result',
      header: 'Result',
      accessorKey: 'cumulative',
      cell: ({ row }) => <span className="tabular-nums">{formatPercent(row.cumulative, true)}</span>,
    },
    { id: 'verdict', header: 'Verdict', accessorKey: 'verdict', cell: ({ row }) => <Badge tone={forwardTone(row.verdict)}>{VERDICT_LABEL[row.verdict]}</Badge> },
    {
      id: 'run',
      header: 'Its run',
      accessorKey: 'run',
      cell: ({ row }) => (
        <RunPeekLink id={row.run} className="font-mono text-xs underline-offset-2 hover:underline">
          {row.run}
        </RunPeekLink>
      ),
    },
  ]
}

/**
 * A kept conclusion judged on the days after it: its candidate asked again each evening on the days no run of its
 * family had seen, its trades set against Edge Watch's model of the candidate (src/admin/forward_checks.py).
 */
export function ForwardCheckPage() {
  const { checkId = '' } = useParams()
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const [stopping, setStopping] = useState(false)
  const checkQuery = useQuery({ queryKey: ['forward-check', checkId], queryFn: () => api.forwardCheck(checkId), refetchInterval: 60_000 })
  const check = checkQuery.data
  const stop = useMutation({
    mutationFn: () => api.stopForwardCheck(checkId),
    onSuccess: () => {
      setStopping(false)
      void queryClient.invalidateQueries({ queryKey: ['forward-check', checkId] })
      void queryClient.invalidateQueries({ queryKey: ['forward-checks'] })
      void queryClient.invalidateQueries({ queryKey: ['experiments'] })
    },
  })

  if (isNotFound(checkQuery.error)) return <NotFoundState {...FORWARD_CHECK_NOT_FOUND} />

  const judged = check?.judgement ?? null
  const track = judged ? { ...judged, path: check?.path ?? [] } : null
  return (
    <>
      <PageHeader
        title={check ? `Forward check: ${check.title ?? check.experiment}` : 'Forward check'}
        description={check ? `${check.family ?? ''} · judged from ${formatDay(check.start)} on the days no run of its family had seen` : undefined}
        actions={
          check?.state === 'active' && (
            <Button variant="secondary" size="sm" onClick={() => setStopping(true)}>
              Stop it
            </Button>
          )
        }
      />
      <QueryState isPending={checkQuery.isPending} error={checkQuery.error} hasData={!!check}>
        {check && (
          <div className="flex flex-col gap-6">
            <Panel title="On later days">
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={forwardTone(judged?.verdict ?? null)}>{judged ? VERDICT_LABEL[judged.verdict] : 'Nothing judged yet'}</Badge>
                  {check.state === 'ended' ? (
                    <Badge tone="neutral">Ended{check.why ? `: ${ENDED_SAID[check.why]}` : ''}</Badge>
                  ) : (
                    <Badge tone="success" dot>
                      Judged each evening
                    </Badge>
                  )}
                </div>
                {judged && judged.reasons.length > 0 && <p className="text-sm">{reasonsSaid(judged.reasons)}</p>}
                {!judged && (
                  <p className="text-sm text-muted-foreground">
                    Its first run is asked once the market is closed, after the day&apos;s nightly runs, while nothing else waits.
                  </p>
                )}
                {check.last?.error && (
                  <Alert tone="warning">
                    <AlertTitle>Its last run was not asked</AlertTitle>
                    <AlertDescription>{check.last.error}</AlertDescription>
                  </Alert>
                )}
                {track && check.model?.usable && <TrackFigures track={track} />}
                {track && <TrackCone cone={check.cone} track={track} label="Its trades on later days" />}
              </div>
            </Panel>
            <Panel title="What it judges">
              <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs text-muted-foreground">Candidate</dt>
                  <dd>
                    <RunPeekLink id={check.candidate} className="font-mono underline-offset-2 hover:underline">
                      {check.candidate}
                    </RunPeekLink>
                    {check.candidate !== check.first_candidate && (
                      <span className="text-xs text-muted-foreground"> (made again from {check.first_candidate})</span>
                    )}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Experiment</dt>
                  <dd>
                    <Link to="/backtests/experiments" className="underline-offset-2 hover:underline">
                      {check.title ?? check.experiment}
                    </Link>
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Started</dt>
                  <dd>
                    {formatDateTime(check.started_at, timezone, false)}
                    {check.by && <span className="text-xs text-muted-foreground"> by {check.by}</span>}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Its model</dt>
                  <dd>
                    {check.model?.reference
                      ? `${check.model.reference.trades} trades over ${check.model.reference.days} days, ${formatPercent(check.model.reference.mean, true)} a trade`
                      : '—'}
                  </dd>
                </div>
              </dl>
            </Panel>
            <AdvancedDataTable<ForwardCheckLine>
              title="Each evening"
              emptyTitle="Not judged yet"
              data={check.history}
              columns={historyColumns(timezone)}
              getRowId={(row) => `${row.run}-${row.at}`}
              defaultPageSize={10}
              selectable={false}
            />
          </div>
        )}
      </QueryState>
      <ConfirmAction
        open={stopping}
        title="Stop this forward check"
        description="Its runs are no longer asked: what it judged stays, written in the journal, and its place is freed for another."
        warning="Nothing that trades changes."
        gesture="button"
        confirmLabel="Stop it"
        pending={stop.isPending}
        error={stop.error?.message ?? null}
        onConfirm={() => stop.mutate()}
        onClose={() => setStopping(false)}
      />
    </>
  )
}
