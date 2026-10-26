import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import { DateRangeFilter } from '@/components/DateRangeFilter'
import { ExportButton } from '@/components/ExportButton'
import { PageHeader } from '@/components/Panel'
import { RouterLink } from '@/components/RouterLink'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import { AnimatedTabs } from '@/components/ui/animated-tabs'
import type { DateRange } from '@/components/ui/calendar'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { ALL_TIME, rangeParams } from '@/lib/dateRange'
import type { Position } from '@/lib/types'
import { cn } from '@/lib/utils'

import { POSITION_CSV, positionColumns } from './columns'
import { LivePositionChart } from './LivePositionChart'
import { PositionChart } from './PositionChart'
import { PositionOrders } from './PositionOrders'

type StatusFilter = 'open' | 'closed' | 'all'

// Each tab has its address (`?status=`), and can be opened in another tab; the open positions are the page's own.
const STATUSES: StatusFilter[] = ['open', 'closed', 'all']

function statusHref(status: StatusFilter): string {
  return status === 'open' ? '?' : `?status=${status}`
}

const TABS = [
  { id: 'open', label: 'Open', href: statusHref('open') },
  { id: 'closed', label: 'Closed', href: statusHref('closed') },
  { id: 'all', label: 'All', href: statusHref('all') },
]

const TABLE_TEXT: Record<StatusFilter, { title: string; emptyTitle: string; emptyDescription?: string }> = {
  open: {
    title: 'Open positions',
    emptyTitle: 'No open position',
    emptyDescription: 'The trader holds nothing right now.',
  },
  closed: { title: 'Closed positions', emptyTitle: 'No closed position in this range' },
  all: { title: 'Open and closed positions', emptyTitle: 'No position in this range' },
}

/** The current time, refreshed every few seconds: enough for "12 s ago" beside a live value. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

export function PositionsPage() {
  const { timezone, actions_enabled: actionsEnabled } = useSession()
  const now = useNow(5_000)
  const [searchParams, setSearchParams] = useSearchParams()
  // A link from an order: its position, open or closed, with its orders shown.
  const find = searchParams.get('find') ?? ''
  const status = STATUSES.find((candidate) => candidate === searchParams.get('status')) ?? (find ? 'all' : 'open')
  const [range, setRange] = useState<DateRange>(ALL_TIME)
  const params = rangeParams(range)

  const positionsQuery = useQuery({
    queryKey: ['positions', status, params.from, params.to],
    queryFn: () => api.positions({ ...params, status: status === 'all' ? undefined : status }),
    // Open positions carry a live value that the monitor publishes every few seconds.
    refetchInterval: status === 'open' ? 5_000 : 15_000,
    placeholderData: keepPreviousData,
  })

  const columns = useMemo(
    () => positionColumns(timezone, status, { now, canClose: actionsEnabled }),
    [timezone, status, now, actionsEnabled],
  )
  const page = positionsQuery.data

  return (
    <>
      <PageHeader
        title="Positions"
        description={`What the trader and the position monitor recorded. Times are in ${timezone}.`}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <AnimatedTabs
          tabs={TABS}
          activeTab={status}
          // The position that was looked for has been seen: the other tabs start unsearched.
          onChange={(tab) => setSearchParams(tab === 'open' ? {} : { status: tab }, { replace: true })}
          linkComponent={RouterLink}
          renderContent={false}
        />
        <div className="flex flex-wrap items-center gap-2">
          {status !== 'open' && <DateRangeFilter value={range} onChange={setRange} />}
          <ExportButton name={`wata-positions-${status}`} rows={page?.items ?? []} columns={POSITION_CSV} />
        </div>
      </div>

      <QueryState isPending={positionsQuery.isPending} error={positionsQuery.error} hasData={!!page}>
        {page && (
          // While another filter loads, the previous rows stay in place, dimmed.
          <div className={cn('space-y-2 transition-opacity', positionsQuery.isPlaceholderData && 'opacity-60')}>
            <AdvancedDataTable<Position>
              // Sorting and paging start over when the filter changes the columns.
              key={`${status}:${find}`}
              title={TABLE_TEXT[status].title}
              emptyTitle={TABLE_TEXT[status].emptyTitle}
              emptyDescription={TABLE_TEXT[status].emptyDescription}
              searchPlaceholder="Search instrument, id…"
              data={page.items}
              columns={columns}
              getRowId={(row) => row.position_id}
              defaultPageSize={20}
              selectable={false}
              initialSearch={find}
              initialExpandedRowIds={find ? [find] : undefined}
              renderSubComponent={(row) =>
                row.position_status === 'Open' ? (
                  <div className="space-y-4">
                    <LivePositionChart position={row} />
                    <PositionOrders positionId={row.position_id} />
                  </div>
                ) : (
                  <div className="space-y-4">
                    <PositionChart position={row} />
                    <PositionOrders positionId={row.position_id} />
                  </div>
                )
              }
            />
            {page.total > page.items.length && (
              <p className="text-xs text-muted-foreground">
                Showing the {page.items.length} most recent of {page.total}. Narrow the date range to see older ones.
              </p>
            )}
          </div>
        )}
      </QueryState>
    </>
  )
}
