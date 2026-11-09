import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, Panel, QueryState } from 'ioiti-ui'
import { Dices, Gauge } from 'lucide-react'

import { api } from '@/lib/api'
import type { BacktestRun, BacktestRunListed } from '@/lib/types'

import { isActive } from './model'
import { StateBadge } from './parts'
import { noiseSentences, STRESS_AXES, stressVerdict } from './robustness'
import { RunPeekLink } from './RunPeek'
import { METRICS } from './sweep'
import { SweepTable } from './SweepPanel'

/** A stress of the run, its state and its page. */
function Latest({ latest }: { latest: BacktestRunListed }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <StateBadge status={latest.status} />
      <RunPeekLink id={latest.id} className="font-medium underline-offset-4 hover:underline">
        {latest.name}
      </RunPeekLink>
    </div>
  )
}

/**
 * How much of a run's result its fills hold: the run made again with each
 * order filled later and at a worse price (a stress sweep), and with each fill
 * moved at random (a fill-noise stress), the newest of each shown.
 */
export function StressPanel({ run }: { run: BacktestRun }) {
  const queryClient = useQueryClient()
  const listQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => (item.stress_of === run.id || item.noise_of === run.id) && isActive(item.status.state)) ? 2_000 : false,
  })
  const items = listQuery.data?.items ?? []
  const latest = items.find((item) => item.stress_of === run.id)
  const noisy = items.find((item) => item.noise_of === run.id)
  const done = latest?.status.state === 'done'
  const sweepQuery = useQuery({
    queryKey: ['backtest-sweep', latest?.id],
    queryFn: () => api.backtestSweep(latest?.id ?? ''),
    enabled: done,
    staleTime: Infinity,
  })
  // A fill-noise stress says its spread in its summary.
  const noiseQuery = useQuery({
    queryKey: ['backtest', noisy?.id],
    queryFn: () => api.backtest(noisy?.id ?? ''),
    enabled: noisy?.status.state === 'done',
    staleTime: Infinity,
  })
  const ask = useMutation({
    mutationFn: (kind: 'fills' | 'noise') => api.stressBacktest(run.id, kind),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtests'] }),
  })
  const verdict = sweepQuery.data ? stressVerdict(sweepQuery.data) : null
  const levels = noiseQuery.data?.summary?.noise ?? []
  const own = run.summary?.figures?.total_percent ?? null

  return (
    <Panel
      title="Later and dearer fills"
      description="The same signals on the same days, each order filled up to 15 s later and up to 0.05 € a turbo dearer than this run was. A result that only lives at its own fills will not live with real ones."
      action={
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            leftIcon={<Gauge className="size-3.5" />}
            isLoading={ask.isPending && ask.variables === 'fills'}
            disabled={Boolean(latest && isActive(latest.status.state))}
            onClick={() => ask.mutate('fills')}
          >
            {latest ? 'Stress it again' : 'Stress this run'}
          </Button>
          <Button
            size="sm"
            variant="outline"
            leftIcon={<Dices className="size-3.5" />}
            isLoading={ask.isPending && ask.variables === 'noise'}
            disabled={Boolean(noisy && isActive(noisy.status.state))}
            onClick={() => ask.mutate('noise')}
          >
            Move its fills at random
          </Button>
        </div>
      }
    >
      <QueryState isPending={listQuery.isPending} error={listQuery.error} hasData={!!listQuery.data}>
        <div className="flex flex-col gap-6">
          {!latest ? (
            <p className="text-sm text-muted-foreground">Not stressed yet: sixteen combinations, one replay of the script.</p>
          ) : (
            <div className="flex flex-col gap-4">
              <Latest latest={latest} />
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
          <section aria-label="Fills moved at random" className="flex flex-col gap-3">
            <h3 className="text-xs font-medium text-muted-foreground">Fills moved at random</h3>
            {!noisy ? (
              <p className="text-sm text-muted-foreground">
                Not asked yet: each fill moved at random by up to 0.1, 0.3 and 0.5 % of its price, either way, each drawn four times. What the
                total keeps of its luck.
              </p>
            ) : (
              <>
                <Latest latest={noisy} />
                {noisy.status.state === 'done' && (
                  <QueryState isPending={noiseQuery.isPending} error={noiseQuery.error} hasData={!!noiseQuery.data}>
                    {levels.length > 0 ? (
                      <ul className="flex flex-col gap-1.5 text-sm leading-relaxed">
                        {noiseSentences(levels, own).map((sentence) => (
                          <li key={sentence}>{sentence}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-sm text-muted-foreground">It gave no total to compare.</p>
                    )}
                  </QueryState>
                )}
              </>
            )}
          </section>
        </div>
        {ask.error && (
          <p role="alert" className="mt-2 text-sm text-negative">
            {ask.error.message}
          </p>
        )}
      </QueryState>
    </Panel>
  )
}
