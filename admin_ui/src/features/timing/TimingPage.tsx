import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Button, cn, formatNumber, KpiGrid, KpiTile, PageHeader, Panel, QueryState, Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from 'ioiti-ui'
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
            <KpiGrid>
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
            </KpiGrid>

            <Panel
              title="Trades"
              description="Each column is the time since the trade started when that step was reached, in milliseconds. The turbo search and the balance read run side by side."
              bodyClassName="pt-2"
            >
              {timings.entries.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted-foreground">No trade was timed in this period.</p>
              ) : (
                <Table density="compact" className="min-w-[56rem]">
                  <TableHead>
                    <TableRow>
                      <TableHeaderCell>Time</TableHeaderCell>
                      <TableHeaderCell>Signal</TableHeaderCell>
                      {timings.steps.map((step) => (
                        <TableHeaderCell numeric key={step}>
                          {STEP_LABELS[step] ?? step}
                        </TableHeaderCell>
                      ))}
                      <TableHeaderCell numeric>Total</TableHeaderCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {timings.entries.map((entry, index) => (
                      <TableRow key={`${entry.timestamp}-${index}`}>
                        <TableCell className="whitespace-nowrap">{formatWallTime(entry.timestamp)}</TableCell>
                        <TableCell>
                          <ActionBadge action={entry.action} />
                        </TableCell>
                        {timings.steps.map((step) => (
                          <TableCell numeric key={step} className="text-muted-foreground">
                            {entry.steps[step] == null ? '—' : formatNumber(entry.steps[step])}
                          </TableCell>
                        ))}
                        <TableCell numeric className="font-medium">{milliseconds(entry.total_ms)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Panel>
          </div>
        )}
      </QueryState>
    </>
  )
}
