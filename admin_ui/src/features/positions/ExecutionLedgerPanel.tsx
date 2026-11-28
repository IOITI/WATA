import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { cn, KpiTile, Panel, QueryState, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'

import { api } from '@/lib/api'
import type { DateRangeParams } from '@/lib/api'

import { deliveryText, ledgerLines, ledgerTiles, pointsText, saxoText } from './executionLedger'
import type { Tone } from './executionLedger'

const TONES: Record<Tone, string> = { negative: 'text-negative', positive: 'text-positive', neutral: '' }

/**
 * What the real trades of the period lost between their signal and their fills (src/admin/execution_ledger.py):
 * the turbo's move while the signal travelled, the price paid above the ask the order was sized on, the price sold
 * at under the one the sale was decided on; by exit, and the signal's way to WATA.
 */
export function ExecutionLedgerPanel({ params }: { params: DateRangeParams }) {
  const query = useQuery({
    queryKey: ['execution-ledger', params.from, params.to],
    queryFn: () => api.executionLedger(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  })
  const ledger = query.data

  return (
    <Panel
      title="What execution cost"
      description="Each closed trade of the period, from its signal to its fills: points of its result lost on the way, a cost positive."
      bodyClassName="pt-2"
    >
      <QueryState isPending={query.isPending} error={query.error} hasData={!!ledger}>
        {ledger &&
          (ledger.summary.trades === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No trade was closed in this period.</p>
          ) : (
            <div className={cn('space-y-4 transition-opacity', query.isPlaceholderData && 'opacity-60')}>
              <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
                {ledgerTiles(ledger).map((tile) => (
                  <KpiTile key={tile.key} label={tile.label} value={<span className={TONES[tile.tone]}>{tile.value}</span>} hint={tile.hint} />
                ))}
              </div>
              {ledger.by_exit.length > 0 && (
                <Table density="compact" className="min-w-[30rem]">
                  <TableHead>
                    <TableRow>
                      <TableHeaderCell>By exit</TableHeaderCell>
                      <TableHeaderCell numeric>Trades</TableHeaderCell>
                      <TableHeaderCell numeric>Result, typical</TableHeaderCell>
                      <TableHeaderCell numeric>Cost, typical</TableHeaderCell>
                      <TableHeaderCell numeric>Exit slippage</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {ledgerLines(ledger.by_exit).map((line) => (
                      <TableRow key={line.group}>
                        <TableCell className="font-medium">{line.group}</TableCell>
                        <TableCell numeric>{line.trades}</TableCell>
                        <TableCell numeric>{line.result}</TableCell>
                        <TableCell numeric className={cn(TONES[line.costTone])}>{line.cost}</TableCell>
                        <TableCell numeric className={cn(TONES[line.exitTone])}>{line.exit}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
              <p className="text-xs text-muted-foreground">
                {deliveryText(ledger.by_source) ? `Signals reached WATA in, typically: ${deliveryText(ledger.by_source)}. ` : ''}
                {saxoText(ledger)}. The spread paid at entry was {pointsText(ledger.summary.spread_percent.median)} typically,
                already in each result. Times by Saxo’s clock and WATA’s are not one clock; a TradingView signal’s way is
                known from v0.85.0 on.
              </p>
            </div>
          ))}
      </QueryState>
    </Panel>
  )
}
