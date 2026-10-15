import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { PathChart } from '@/components/charts/PerformanceCharts'
import { Panel } from '@/components/Panel'
import { ActionBadge, PnlValue, QueryState } from '@/components/status'
import { api } from '@/lib/api'
import { formatPercent, formatTime } from '@/lib/format'
import type { BacktestTrade } from '@/lib/types'

import { pathView } from './model'

function Legend({ trailing }: { trailing: boolean }) {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ backgroundColor: 'var(--chart-series)' }} />
        The turbo&apos;s result at the end of each ten seconds
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="h-2.5 w-4 rounded-[2px] opacity-30" style={{ backgroundColor: 'var(--chart-series)' }} />
        Its worst and its best inside them
      </li>
      {trailing && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="w-4 border-t border-dashed border-muted-foreground" />
          Trailing stop, once armed
        </li>
      )}
    </ul>
  )
}

/**
 * One trade as the position monitor lived it: the turbo's result from the
 * entry to the exit, against the levels that could have sold it. The day
 * chart shows what the strategy saw; this shows why the monitor sold where
 * it did.
 */
export function TradePathPanel({ runId, leverage, trade }: { runId: string; leverage: number; trade: BacktestTrade }) {
  const pathQuery = useQuery({
    queryKey: ['backtest-trade-path', runId, leverage, trade.entry_time],
    queryFn: () => api.backtestTradePath(runId, leverage, trade.entry_time),
    staleTime: Infinity,
  })
  const path = pathQuery.data
  const view = useMemo(() => (path ? pathView(path) : null), [path])
  const points = useMemo(
    () =>
      (path?.points ?? []).map(([time, last, worst, best, trailing]) => ({ time, last, range: [worst, best] as [number, number], trailing })),
    [path],
  )
  const timezone = path?.timezone ?? 'UTC'
  const clock = (time: number) => formatTime(new Date(time * 1000).toISOString(), timezone)

  return (
    <Panel
      title="This trade, on the turbo"
      description="What the position monitor saw of it, every ten seconds from the entry to the exit, and the levels that could sell it."
    >
      <QueryState isPending={pathQuery.isPending} error={pathQuery.error} hasData={!!path && !!view}>
        {path && view && (
          <div className="flex flex-col gap-3">
            <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
              <ActionBadge action={trade.direction} />
              <span>on {trade.entry_reason},</span>
              <span className="tabular-nums text-muted-foreground">
                {formatTime(trade.entry_time, timezone)} to {formatTime(trade.exit_time, timezone)}:
              </span>
              <PnlValue value={trade.performance_percent} kind="percent" />
              <span className="text-muted-foreground">{trade.exit_reason}</span>
            </p>
            <Legend trailing={points.some((point) => point.trailing !== null)} />
            <PathChart points={points} lines={view.shown} domain={view.domain} clock={clock} />
            <p className="text-xs leading-relaxed text-muted-foreground">
              {path.day_before_percent !== 0 &&
                `The day was at ${formatPercent(path.day_before_percent, true)} before it: the day's target asked ${formatPercent(path.levels.day_target, true)} of this trade. `}
              {view.away.length > 0 &&
                `Too far from what the trade did to be drawn with it: ${view.away.map((level) => `${level.label.toLowerCase()} at ${formatPercent(level.value, true)}`).join(', ')}. `}
              {path.levels.trailing_activation === null
                ? 'The trailing stop was off.'
                : `The trailing stop arms at ${formatPercent(path.levels.trailing_activation, true)} and sells ${path.levels.trailing_drawdown} points under the best level seen.`}
            </p>
          </div>
        )}
      </QueryState>
    </Panel>
  )
}
