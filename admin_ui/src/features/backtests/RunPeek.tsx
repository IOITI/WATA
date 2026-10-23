import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { PeekLink } from '@/components/PeekLink'
import { PnlValue } from '@/components/status'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { formatDay, formatNumber, formatPercent } from '@/lib/format'

import { sourceLabel } from './model'
import { StateBadge } from './parts'

/** What a run's link shows of it: read from the list of runs, which the Backtests pages already hold. */
export function RunPeek({ id }: { id: string }) {
  const runsQuery = useQuery({ queryKey: ['backtests'], queryFn: api.backtests })
  if (runsQuery.isPending) return <Skeleton className="h-20 w-full" />
  const run = runsQuery.data?.items.find((item) => item.id === id)
  if (!run) return <p>This run is not in the list of backtests: removed, or never made here.</p>
  const headline = run.headline
  const figures: [string, ReactNode][] = headline
    ? [
        ['Total', <PnlValue key="total" value={headline.total_percent} kind="percent" />],
        ['Trades', formatNumber(headline.trades)],
        ['Won', headline.win_rate === null ? '—' : formatPercent(headline.win_rate, false, 0)],
        ['Random runs beaten', headline.beaten === null ? '—' : formatPercent(headline.beaten, false, 0)],
        ['Days tested', formatNumber(headline.days_tested)],
      ]
    : []
  return (
    <div className="space-y-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{run.name}</p>
          <p className="truncate">
            {sourceLabel(run)} · {formatDay(run.from)} to {formatDay(run.to)}
          </p>
        </div>
        <StateBadge status={run.status} />
      </div>
      {figures.length > 0 && (
        <dl className="space-y-1">
          {figures.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-3">
              <dt>{label}</dt>
              <dd className="tabular-nums text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

/** A link to a run, its figures shown on hover, keyboard focus or a first tap. */
export function RunPeekLink({ id, className, children }: { id: string; className?: string; children: ReactNode }) {
  return (
    <PeekLink to={`/backtests/${id}`} content={() => <RunPeek id={id} />} className={className}>
      {children}
    </PeekLink>
  )
}
