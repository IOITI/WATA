import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Gauge } from 'lucide-react'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { BacktestRun } from '@/lib/types'

import { isActive } from './model'
import { StateBadge } from './parts'
import { STRESS_AXES, stressVerdict } from './robustness'
import { RunPeekLink } from './RunPeek'
import { METRICS } from './sweep'
import { SweepTable } from './SweepPanel'

/**
 * How much of a run's result its fills hold: the run made again with each
 * order filled later and at a worse price (a stress sweep), the newest shown.
 */
export function StressPanel({ run }: { run: BacktestRun }) {
  const queryClient = useQueryClient()
  const listQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.stress_of === run.id && isActive(item.status.state)) ? 2_000 : false),
  })
  const latest = (listQuery.data?.items ?? []).find((item) => item.stress_of === run.id)
  const done = latest?.status.state === 'done'
  const sweepQuery = useQuery({
    queryKey: ['backtest-sweep', latest?.id],
    queryFn: () => api.backtestSweep(latest?.id ?? ''),
    enabled: done,
    staleTime: Infinity,
  })
  const ask = useMutation({
    mutationFn: () => api.stressBacktest(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtests'] }),
  })
  const verdict = sweepQuery.data ? stressVerdict(sweepQuery.data) : null
  const asking = latest && isActive(latest.status.state)

  return (
    <Panel
      title="Later and dearer fills"
      description="The same signals on the same days, each order filled up to 15 s later and up to 0.05 € a turbo dearer than this run was. A result that only lives at its own fills will not live with real ones."
      action={
        <Button size="sm" variant="outline" leftIcon={<Gauge className="size-3.5" />} isLoading={ask.isPending} disabled={Boolean(asking)} onClick={() => ask.mutate()}>
          {latest ? 'Stress it again' : 'Stress this run'}
        </Button>
      }
    >
      <QueryState isPending={listQuery.isPending} error={listQuery.error} hasData={!!listQuery.data}>
        {!latest ? (
          <p className="text-sm text-muted-foreground">Not stressed yet: sixteen combinations, one replay of the script.</p>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StateBadge status={latest.status} />
              <RunPeekLink id={latest.id} className="font-medium underline-offset-4 hover:underline">
                {latest.name}
              </RunPeekLink>
            </div>
            {done && (
              <QueryState isPending={sweepQuery.isPending} error={sweepQuery.error} hasData={!!sweepQuery.data}>
                {sweepQuery.data && (
                  <>
                    {verdict && (
                      <Alert tone={verdict.holds === false ? 'warning' : 'neutral'}>
                        <AlertDescription>{verdict.sentence}</AlertDescription>
                      </Alert>
                    )}
                    <SweepTable sweep={sweepQuery.data} metric={METRICS[0]} part="whole" labels={STRESS_AXES} marked={0} />
                  </>
                )}
              </QueryState>
            )}
          </div>
        )}
        {ask.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {ask.error.message}
          </p>
        )}
      </QueryState>
    </Panel>
  )
}
