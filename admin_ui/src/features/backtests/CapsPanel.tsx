import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, Panel, QueryState } from 'ioiti-ui'

import { api } from '@/lib/api'

import { RunPeekLink } from './RunPeek'
import { capsSentences, tailRows } from './why'

/**
 * Where a run's result comes from: beside its twin made again without the day's caps (no target, no levels, no
 * breakers), each against its random entries, and the tail the caps hide, the twin's days replayed under them
 * (src/admin/day_tail.py).
 */
export function CapsPanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient()
  const decompositionQuery = useQuery({
    queryKey: ['backtest-decomposition', runId],
    queryFn: () => api.backtestDecomposition(runId),
    refetchInterval: (query) => (query.state.data?.making ? 15_000 : false),
  })
  const make = useMutation({
    mutationFn: () => api.makeWithoutCaps(runId),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['backtest-decomposition', runId] }),
  })
  const answer = decompositionQuery.data
  const found = answer?.decomposition ?? null
  return (
    <Panel
      title="The day's caps"
      description="A day's target, its levels and its breakers shape its trades: a run that stops at its target can show every day positive while its entries have no edge. Its twin without them says what the caps make of its entries."
    >
      <QueryState isPending={decompositionQuery.isPending} error={decompositionQuery.error} hasData={!!answer}>
        {answer && (
          <div className="flex flex-col gap-4">
            {!found && !answer.making && (
              <div className="flex flex-wrap items-center gap-3">
                <Button size="sm" isLoading={make.isPending} onClick={() => make.mutate()}>
                  Make it again without the day&apos;s caps
                </Button>
                <span className="text-xs text-muted-foreground">The same days and signals, its random entries as they were.</span>
              </div>
            )}
            {answer.making && (
              <p className="text-sm">
                Its twin is being made:{' '}
                <RunPeekLink id={answer.making} className="font-mono underline-offset-2 hover:underline">
                  {answer.making}
                </RunPeekLink>
                .
              </p>
            )}
            {make.error && (
              <Alert tone="danger">
                <AlertDescription>{make.error.message}</AlertDescription>
              </Alert>
            )}
            {found && (
              <>
                <ul className="flex flex-col gap-1 text-sm">
                  {capsSentences(found).map((sentence) => (
                    <li key={sentence}>{sentence}</li>
                  ))}
                </ul>
                <p className="text-xs text-muted-foreground">
                  Its twin:{' '}
                  <RunPeekLink id={found.twin} className="font-mono underline-offset-2 hover:underline">
                    {found.twin}
                  </RunPeekLink>
                  . Below, its {found.tail.days_drawn_from} days drawn whole {found.tail.draws.toLocaleString('en')} times and replayed under this run&apos;s caps: with each trade&apos;s best point first, and on its results alone.
                </p>
                <div className="relative overflow-x-auto">
                  <table className="w-full min-w-[32rem] text-sm">
                    <thead>
                      <tr className="text-left text-xs text-muted-foreground">
                        <th className="py-1 pr-3 font-normal">Of its days</th>
                        <th className="py-1 pr-3 font-normal">Its own</th>
                        <th className="py-1 pr-3 font-normal">Best point first</th>
                        <th className="py-1 font-normal">Results alone</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tailRows(found.tail).map((row) => (
                        <tr key={row.label} className="border-t border-border">
                          <td className="py-1 pr-3">{row.label}</td>
                          <td className="py-1 pr-3 tabular-nums">{row.observed}</td>
                          <td className="py-1 pr-3 tabular-nums">{row.bestFirst}</td>
                          <td className="py-1 tabular-nums">{row.results}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-muted-foreground">
                  A model: its days are drawn as if independent, and a trade the caps refuse changes nothing of the trades after it.
                </p>
              </>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
