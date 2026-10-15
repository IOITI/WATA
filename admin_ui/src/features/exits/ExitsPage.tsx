import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { ProfitByGroupChart, ProfitLossLegend } from '@/components/charts/PerformanceCharts'
import { DateRangeFilter } from '@/components/DateRangeFilter'
import { KpiTile } from '@/components/KpiTile'
import { PageHeader, Panel } from '@/components/Panel'
import { PnlValue, QueryState } from '@/components/status'
import type { DateRange } from '@/components/ui/calendar'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { lastDays, rangeParams } from '@/lib/dateRange'
import { formatNumber, formatPercent } from '@/lib/format'
import type { Exits, GroupStats } from '@/lib/types'
import { cn } from '@/lib/utils'

function GroupTable<T extends GroupStats>({
  groups,
  header,
  label,
}: {
  groups: T[]
  header: string
  label: (group: T) => string
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 pr-4 font-medium">{header}</th>
            <th className="py-2 pr-4 text-right font-medium">Trades</th>
            <th className="py-2 pr-4 text-right font-medium">Won</th>
            <th className="py-2 pr-4 text-right font-medium">Average result</th>
            <th className="py-2 pr-4 text-right font-medium">Average best level</th>
            <th className="py-2 text-right font-medium">Profit / loss</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40">
          {groups.map((group) => (
            <tr key={label(group)}>
              <td className="py-2.5 pr-4 font-medium">{label(group)}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{group.trade_count}</td>
              <td className="py-2.5 pr-4 text-right tabular-nums">{formatPercent(group.win_rate, false, 0)}</td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={group.average_percent} kind="percent" />
              </td>
              <td className="py-2.5 pr-4 text-right">
                <PnlValue value={group.average_best_percent} kind="percent" />
              </td>
              <td className="py-2.5 text-right">
                <PnlValue value={group.profit_loss} kind="money" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function hourLabel(hour: number): string {
  return `${String(hour).padStart(2, '0')}h`
}

function ExitsContent({ exits }: { exits: Exits }) {
  const { summary } = exits
  if (summary.trade_count === 0) {
    return (
      <Panel>
        <p className="py-10 text-center text-sm text-muted-foreground">No closed trade in this range.</p>
      </Panel>
    )
  }
  const holding = exits.by_holding_time.filter((bucket) => bucket.trade_count > 0)

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
        <KpiTile
          label="Average result"
          value={<PnlValue value={summary.average_percent} kind="percent" className="font-semibold" />}
          hint={`${formatNumber(summary.trade_count)} closed trades`}
        />
        <KpiTile
          label="Average best level"
          value={<PnlValue value={summary.average_best_percent} kind="percent" className="font-semibold" />}
          hint="The highest performance a trade reached while open"
        />
        <KpiTile
          label="Given back"
          value={summary.average_given_back === null ? '—' : `${summary.average_given_back.toFixed(2)} pts`}
          hint="Average gap between the best level and the close"
        />
        <KpiTile
          label="In profit, closed at a loss"
          value={formatPercent(summary.turned_negative_percent, false, 0)}
          hint={`${summary.turned_negative_count} trades went positive, then closed negative`}
        />
      </div>

      <Panel
        title="By exit"
        description="What closed the trades: the monitor's rules, a signal or the end of day, the operator, or Saxo itself."
        bodyClassName="pt-2"
      >
        <GroupTable groups={exits.by_reason} header="Exit" label={(group) => group.reason} />
      </Panel>

      <Panel
        title="By hour of entry"
        description={`Profit and loss of the trades opened in each hour (${exits.timezone}).`}
        action={<ProfitLossLegend profit="Profit" loss="Loss" />}
      >
        <ProfitByGroupChart groups={exits.by_hour} label={(group) => hourLabel(group.hour)} />
        <div className="mt-4">
          <GroupTable groups={exits.by_hour} header="Hour" label={(group) => hourLabel(group.hour)} />
        </div>
      </Panel>

      <Panel title="By holding time" bodyClassName="pt-2">
        <GroupTable groups={holding} header="Held for" label={(group) => group.bucket} />
      </Panel>
    </>
  )
}

export function ExitsPage() {
  const { timezone } = useSession()
  const [range, setRange] = useState<DateRange>(() => lastDays(90))
  const params = rangeParams(range)

  const exitsQuery = useQuery({
    queryKey: ['exits', params.from, params.to],
    queryFn: () => api.exits(params),
    refetchInterval: 120_000,
    placeholderData: keepPreviousData,
  })
  const exits = exitsQuery.data

  return (
    <>
      <PageHeader
        title="Exits"
        description={`How closed trades ended, by the day they closed in ${timezone}.`}
        actions={<DateRangeFilter value={range} onChange={setRange} />}
      />
      <QueryState isPending={exitsQuery.isPending} error={exitsQuery.error} hasData={!!exits}>
        {exits && (
          <div className={cn('flex flex-col gap-6 transition-opacity', exitsQuery.isPlaceholderData && 'opacity-60')}>
            <ExitsContent exits={exits} />
          </div>
        )}
      </QueryState>
    </>
  )
}
