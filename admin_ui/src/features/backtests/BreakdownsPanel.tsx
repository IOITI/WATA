import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ProfitByGroupChart } from '@/components/charts/PerformanceCharts'
import { Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatMoney, formatNumber, formatPercent } from '@/lib/format'
import type { BacktestBreakdowns, BacktestGroup } from '@/lib/types'

type Dimension = keyof BacktestBreakdowns['groups']

const DIMENSIONS: { id: Dimension; label: string; name: (group: BacktestGroup) => string }[] = [
  { id: 'hour', label: 'Hour of entry', name: (group) => `${String(group.group).padStart(2, '0')}:00` },
  { id: 'weekday', label: 'Weekday', name: (group) => String(group.group) },
  { id: 'direction', label: 'Direction', name: (group) => String(group.group) },
  { id: 'entry_reason', label: 'Entered on', name: (group) => String(group.group) },
  { id: 'confidence', label: 'Confidence', name: (group) => String(group.group) },
  { id: 'holding_time', label: 'Held for', name: (group) => String(group.group) },
  { id: 'exit', label: 'Left on', name: (group) => String(group.group) },
]

/**
 * The trades of a run by what could explain them. The bars are what each
 * group weighs: the sum of its results, each trade as the whole stake.
 */
export function BreakdownsPanel({ runId, leverage, withMoney }: { runId: string; leverage: number; withMoney: boolean }) {
  const [dimension, setDimension] = useState<Dimension>('hour')
  const breakdownsQuery = useQuery({
    queryKey: ['backtest-breakdowns', runId, leverage],
    queryFn: () => api.backtestBreakdowns(runId, leverage),
    staleTime: Infinity,
  })
  const breakdowns = breakdownsQuery.data
  const chosen = DIMENSIONS.find((candidate) => candidate.id === dimension) ?? DIMENSIONS[0]
  const groups = breakdowns?.groups[dimension] ?? []
  const summary = breakdowns?.summary

  return (
    <Panel
      title="Where the result comes from"
      description="The trades by the hour and the weekday they were entered on, their direction, what they were entered and left on, their confidence and how long they were held."
    >
      <QueryState isPending={breakdownsQuery.isPending} error={breakdownsQuery.error} hasData={!!breakdowns}>
        {breakdowns && summary && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Trades grouped by">
              {DIMENSIONS.map((candidate) => (
                <Button
                  key={candidate.id}
                  variant={candidate.id === dimension ? 'primary' : 'outline'}
                  size="sm"
                  aria-pressed={candidate.id === dimension}
                  onClick={() => setDimension(candidate.id)}
                >
                  {candidate.label}
                </Button>
              ))}
            </div>

            {groups.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No trade to group.</p>
            ) : (
              <>
                <ProfitByGroupChart
                  // The sum of the results of each group, in points of percent.
                  groups={groups.map((group) => ({ ...group, profit_loss: group.total_percent, money: group.profit_loss }))}
                  label={chosen.name}
                  unit="percent"
                  more={(group) => [
                    { label: 'On average', value: formatPercent(group.average_percent, true) },
                    ...(withMoney ? [{ label: 'In money', value: formatMoney(group.money, 'EUR', true) }] : []),
                  ]}
                />
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th scope="col" className="py-2 pr-4 font-medium">
                          {chosen.label}
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Trades
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Winning
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Per trade
                        </th>
                        <th scope="col" className="py-2 pr-4 text-right font-medium">
                          Best level, on average
                        </th>
                        <th scope="col" className="py-2 text-right font-medium">
                          Sum of the results
                        </th>
                        {withMoney && (
                          <th scope="col" className="py-2 pl-4 text-right font-medium">
                            In money
                          </th>
                        )}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {groups.map((group) => (
                        <tr key={String(group.group)}>
                          <th scope="row" className="py-2.5 pr-4 font-normal">
                            {chosen.name(group)}
                          </th>
                          <td className="py-2.5 pr-4 text-right tabular-nums">{formatNumber(group.trade_count)}</td>
                          <td className="py-2.5 pr-4 text-right tabular-nums">{formatPercent(group.win_rate, false, 0)}</td>
                          <td className="py-2.5 pr-4 text-right">
                            <PnlValue value={group.average_percent} kind="percent" />
                          </td>
                          <td className="py-2.5 pr-4 text-right tabular-nums">
                            {formatPercent(group.average_best_percent, true)}
                          </td>
                          <td className="py-2.5 text-right">
                            <PnlValue value={group.total_percent} kind="percent" />
                          </td>
                          {withMoney && (
                            <td className="py-2.5 pl-4 text-right">
                              <PnlValue value={group.profit_loss} kind="money" />
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {summary.trade_count > 0 && (
              <p className="text-sm leading-relaxed text-muted-foreground">
                On average a trade closed {formatNumber(summary.average_given_back, 2)} points of percent under the
                best level it had reached. {formatNumber(summary.turned_negative_count)} of the{' '}
                {formatNumber(summary.trade_count)} had been in profit before closing at a loss.
              </p>
            )}
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
