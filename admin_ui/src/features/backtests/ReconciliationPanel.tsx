import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, formatDay, formatNumber, Panel, QueryState, SignedValue } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { BacktestReconciliation } from '@/lib/types'

import { reconciliationVerdict } from './reconciliation'

function isReconciliation(value: unknown): value is BacktestReconciliation {
  return typeof value === 'object' && value !== null && 'summary' in value && 'pairs' in value
}

function price(value: number | null): string {
  return value === null ? '-' : formatNumber(value, 2)
}

/** A replay of the signals WATA received, set beside what WATA really did with each: how far to discount a backtest. */
export function ReconciliationPanel({ runId }: { runId: string }) {
  const reconciliationQuery = useQuery({
    queryKey: ['backtest-reconciliation', runId],
    queryFn: () => api.backtestReconciliation(runId),
    staleTime: Infinity,
  })
  const found = isReconciliation(reconciliationQuery.data) ? reconciliationQuery.data : null
  const verdict = found ? reconciliationVerdict(found.summary) : null
  return (
    <Panel
      title="Against real trades"
      description="Each trade of the replay beside WATA's real position of the same signal, and the trades only one side made: how far a backtest's figures are from what WATA does."
    >
      <QueryState isPending={reconciliationQuery.isPending} error={reconciliationQuery.error} hasData={!!reconciliationQuery.data}>
        {!found || !verdict ? (
          <p className="text-sm text-muted-foreground">Not set beside WATA’s positions: the run was made before they were kept with it.</p>
        ) : (
          <div className="flex flex-col gap-4">
            <Alert tone={verdict.tone === 'close' ? 'success' : verdict.tone === 'apart' ? 'warning' : 'neutral'}>
              <AlertDescription>
                {verdict.sentences.map((sentence) => (
                  <span key={sentence} className="block">
                    {sentence}
                  </span>
                ))}
              </AlertDescription>
            </Alert>
            {found.pairs.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full whitespace-nowrap text-sm" aria-label="Trades beside WATA's positions">
                  <thead>
                    <tr className="text-xs text-muted-foreground">
                      <th scope="col" className="p-2 text-left font-medium">
                        Trade
                      </th>
                      <th scope="col" className="p-2 text-right font-medium">
                        Bought at, backtest / WATA
                      </th>
                      <th scope="col" className="p-2 text-right font-medium">
                        Seconds after WATA
                      </th>
                      <th scope="col" className="p-2 text-right font-medium">
                        Result, backtest
                      </th>
                      <th scope="col" className="p-2 text-right font-medium">
                        Result, WATA
                      </th>
                      <th scope="col" className="p-2 text-left font-medium">
                        Sold on
                      </th>
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {found.pairs.map((pair) => (
                      <tr key={pair.signal_uuid} className="border-t border-border/50">
                        <th scope="row" className="p-2 text-left font-medium whitespace-nowrap">
                          #{pair.trade_id} · {formatDay(pair.day, false)} · {pair.direction}
                          {pair.same_turbo === false && <span className="block text-xs font-normal text-warning">another turbo</span>}
                        </th>
                        <td className="p-2 text-right whitespace-nowrap">
                          {price(pair.entry_price.backtest)} / {price(pair.entry_price.real)}
                        </td>
                        <td className="p-2 text-right">{pair.entry_seconds === null ? '-' : formatNumber(pair.entry_seconds, 1)}</td>
                        <td className="p-2 text-right">
                          <SignedValue value={pair.result.backtest} kind="percent" />
                        </td>
                        <td className="p-2 text-right">
                          <SignedValue value={pair.result.real} kind="percent" />
                        </td>
                        <td className="p-2 text-left text-xs">
                          {pair.same_exit ? (
                            pair.exit.backtest
                          ) : (
                            <span className="text-warning">
                              {pair.exit.backtest || '-'} / WATA: {pair.exit.real || '-'}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
