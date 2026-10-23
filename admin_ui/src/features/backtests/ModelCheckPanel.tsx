import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Scale } from 'lucide-react'

import { Panel } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { BacktestModelCheck, BacktestRun } from '@/lib/types'

import { isActive } from './model'
import { StateBadge } from './parts'
import { RunPeekLink } from './RunPeek'
import { modelCheckSentences } from './turboModel'

function Sentences({ found }: { found: BacktestModelCheck }) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {modelCheckSentences(found).map((sentence) => (
        <li key={sentence}>{sentence}</li>
      ))}
    </ul>
  )
}

/** A model check's own page: its trades beside those of the recorded run it made again. */
function CheckResult({ run }: { run: BacktestRun }) {
  const found = run.summary?.model_check
  return (
    <Panel
      title="Made-up turbos beside the recorded ones"
      description="The run it checks made again on turbos made up from the index, by the measure of when it was asked: the same signals on the same days, without random entries."
    >
      {found ? <Sentences found={found} /> : <p className="text-sm text-muted-foreground">Nothing was compared: the run it checks kept no trade.</p>}
    </Panel>
  )
}

/**
 * How far the turbos made up from the index are from the recorded ones: a
 * finished run on the recorded turbos made again on made-up ones (a model
 * check), the newest shown with what its trades say beside these.
 */
function CheckAction({ run }: { run: BacktestRun }) {
  const queryClient = useQueryClient()
  const optionsQuery = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, staleTime: 60_000 })
  const listQuery = useQuery({
    queryKey: ['backtests'],
    queryFn: api.backtests,
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.model_check_of === run.id && isActive(item.status.state)) ? 2_000 : false),
  })
  const latest = (listQuery.data?.items ?? []).find((item) => item.model_check_of === run.id)
  const done = latest?.status.state === 'done'
  const checkQuery = useQuery({ queryKey: ['backtest', latest?.id], queryFn: () => api.backtest(latest?.id ?? ''), enabled: done })
  const ask = useMutation({
    mutationFn: () => api.modelCheckBacktest(run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['backtests'] }),
  })
  const measured = Boolean(optionsQuery.data?.turbo_model)
  const asking = latest && isActive(latest.status.state)
  const found = checkQuery.data?.summary?.model_check

  return (
    <Panel
      title="Made-up turbos beside these"
      description="This run made again on turbos made up from the index: how far a run on made-up turbos is from the recorded ones, on the same signals. Check it before trusting a long history on made-up turbos."
      action={
        <Button
          size="sm"
          variant="outline"
          leftIcon={<Scale className="size-3.5" />}
          isLoading={ask.isPending}
          disabled={!measured || Boolean(asking)}
          onClick={() => ask.mutate()}
        >
          {latest ? 'Check it again' : 'Check the turbo model'}
        </Button>
      }
    >
      <QueryState isPending={listQuery.isPending} error={listQuery.error} hasData={!!listQuery.data}>
        {!latest ? (
          <p className="text-sm text-muted-foreground">
            {measured || optionsQuery.isPending
              ? 'Not checked yet: one run of the same signals on made-up turbos.'
              : 'The turbos are not measured yet: measure them on the Backtests page first.'}
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StateBadge status={latest.status} />
              <RunPeekLink id={latest.id} className="font-medium underline-offset-4 hover:underline">
                {latest.name}
              </RunPeekLink>
            </div>
            {done && found && <Sentences found={found} />}
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

/** On a model check, what it found; on a run of the recorded turbos, the way to check the model on it. */
export function ModelCheckPanel({ run }: { run: BacktestRun }) {
  if (run.model_check_of) return <CheckResult run={run} />
  if ((run.turbos ?? 'recorded') !== 'recorded') return null
  return <CheckAction run={run} />
}
