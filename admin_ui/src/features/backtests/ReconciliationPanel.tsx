import { useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, formatDay, formatNumber, formatPercent, Panel, QueryState, SignedValue, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'

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
              <Table density="dense" aria-label="Trades beside WATA's positions">
                <TableHead>
                  <TableRow>
                    <TableHeaderCell scope="col">
                      Trade
                    </TableHeaderCell>
                    <TableHeaderCell numeric scope="col">
                      Bought at, backtest / WATA
                    </TableHeaderCell>
                    <TableHeaderCell numeric scope="col">
                      Seconds after WATA
                    </TableHeaderCell>
                    <TableHeaderCell numeric scope="col">
                      Sold at, backtest / WATA
                    </TableHeaderCell>
                    <TableHeaderCell numeric scope="col">
                      Result, backtest
                    </TableHeaderCell>
                    <TableHeaderCell numeric scope="col">
                      Result, WATA
                    </TableHeaderCell>
                    <TableHeaderCell scope="col">
                      Sold on
                    </TableHeaderCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {found.pairs.map((pair) => (
                    <TableRow key={pair.signal_uuid} className="border-border/50">
                      <TableHeaderCell scope="row" className="whitespace-nowrap">
                        #{pair.trade_id} · {formatDay(pair.day, false)} · {pair.direction}
                        {pair.same_turbo === false && <span className="block text-xs font-normal text-warning">another turbo</span>}
                      </TableHeaderCell>
                      <TableCell numeric className="whitespace-nowrap">
                        {price(pair.entry_price.backtest)} / {price(pair.entry_price.real)}
                      </TableCell>
                      <TableCell numeric>{pair.entry_seconds === null ? '-' : formatNumber(pair.entry_seconds, 1)}</TableCell>
                      <TableCell numeric className="whitespace-nowrap">
                        {price(pair.exit_price.backtest)} / {price(pair.exit_price.real)}
                        {pair.exit_price_percent !== null && pair.exit_price_percent !== undefined && (
                          <span className="block text-xs text-muted-foreground">{formatPercent(pair.exit_price_percent, true)}</span>
                        )}
                      </TableCell>
                      <TableCell numeric>
                        <SignedValue value={pair.result.backtest} kind="percent" />
                      </TableCell>
                      <TableCell numeric>
                        <SignedValue value={pair.result.real} kind="percent" />
                      </TableCell>
                      <TableCell className="text-left text-xs">
                        {pair.same_exit ? (
                          pair.exit.backtest
                        ) : (
                          <span className="text-warning">
                            {pair.exit.backtest || '-'} / WATA: {pair.exit.real || '-'}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
