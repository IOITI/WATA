import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Button, cn, formatNumber, KpiTile, PageHeader, Panel, QueryState } from 'ioiti-ui'
import { useState } from 'react'

import { ActionBadge } from '@/components/status'
import { api } from '@/lib/api'
import { formatWallTime } from '@/lib/wallTime'

const PERIODS = [7, 30, 90]

const STEP_LABELS: Record<string, string> = {
  scale_calculated: 'Size scale',
  turbo_found: 'Turbo found',
  spending_power_fetched: 'Balance read',
  amount_calculated: 'Quantity',
  order_placed: 'Order placed',
  position_confirmed: 'Position confirmed',
  db_persisted: 'Saved',
}

function milliseconds(value: number | null | undefined): string {
  return value == null ? '—' : `${formatNumber(value)} ms`
}

export function TimingPage() {
  const [days, setDays] = useState(30)
  const timingQuery = useQuery({
    queryKey: ['execution-timing', days],
    queryFn: () => api.executionTimings(days),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
  const timings = timingQuery.data

  return (
    <>
      <PageHeader
        title="Execution timing"
        description="How long the trader took over each trade, from the moment it started executing it."
        actions={PERIODS.map((period) => (
          <Button
            key={period}
            variant={period === days ? 'primary' : 'outline'}
            size="sm"
            aria-pressed={period === days}
            onClick={() => setDays(period)}
          >
            {period} days
          </Button>
        ))}
      />

      <QueryState isPending={timingQuery.isPending} error={timingQuery.error} hasData={!!timings}>
        {timings && (
          <div className={cn('flex flex-col gap-6 transition-opacity', timingQuery.isPlaceholderData && 'opacity-60')}>
            <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
              <KpiTile label="Trades timed" value={formatNumber(timings.summary.trade_count)} hint={`Last ${days} days`} />
              <KpiTile
                label="Typical trade"
                value={milliseconds(timings.summary.median_total_ms)}
                hint="Median, start to last step"
              />
              <KpiTile
                label="Slow trade"
                value={milliseconds(timings.summary.p90_total_ms)}
                hint="Nine trades in ten were faster"
              />
              <KpiTile
                label="Slowest"
                value={milliseconds(timings.summary.slowest_total_ms)}
                hint="The longest of them"
              />
            </div>

            <Panel
              title="Trades"
              description="Each column is the time since the trade started when that step was reached, in milliseconds. The turbo search and the balance read run side by side."
              bodyClassName="pt-2"
            >
              {timings.entries.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">No trade was timed in this period.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[56rem] text-left text-xs">
                    <thead className="text-muted-foreground">
                      <tr>
                        <th className="py-2 pr-4 font-medium">Time</th>
                        <th className="py-2 pr-4 font-medium">Signal</th>
                        {timings.steps.map((step) => (
                          <th key={step} className="py-2 pr-4 text-right font-medium">
                            {STEP_LABELS[step] ?? step}
                          </th>
                        ))}
                        <th className="py-2 text-right font-medium">Total</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/40">
                      {timings.entries.map((entry, index) => (
                        <tr key={`${entry.timestamp}-${index}`}>
                          <td className="whitespace-nowrap py-2.5 pr-4">{formatWallTime(entry.timestamp)}</td>
                          <td className="py-2.5 pr-4">
                            <ActionBadge action={entry.action} />
                          </td>
                          {timings.steps.map((step) => (
                            <td key={step} className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">
                              {entry.steps[step] == null ? '—' : formatNumber(entry.steps[step])}
                            </td>
                          ))}
                          <td className="py-2.5 text-right font-medium tabular-nums">{milliseconds(entry.total_ms)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </div>
        )}
      </QueryState>
    </>
  )
}
