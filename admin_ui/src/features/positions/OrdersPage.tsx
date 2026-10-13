import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router'

import { DateRangeFilter } from '@/components/DateRangeFilter'
import { ExportButton } from '@/components/ExportButton'
import { KpiTile } from '@/components/KpiTile'
import { PageHeader } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { DateRange } from '@/components/ui/calendar'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatNumber, formatWait } from '@/lib/format'
import { ALL_TIME, lastDays, rangeParams } from '@/lib/dateRange'
import type { Order } from '@/lib/types'
import { cn } from '@/lib/utils'

import { ORDER_CSV, orderColumns } from './columns'
import { OrderTimeline } from './OrderTimeline'
import { entryFigures } from './orderLife'
import type { EntryFigures } from './orderLife'

/** How the entries of the period went: is Saxo slow to turn an order into a position, and did any give none? */
function EntryTiles({ figures }: { figures: EntryFigures }) {
  if (figures.tracked === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No order of this period has its moments: they are kept from the first order placed after the version
        that shows this page was installed.
      </p>
    )
  }
  const entries = `${formatNumber(figures.tracked)} entr${figures.tracked === 1 ? 'y' : 'ies'} in this period`
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-3">
      <KpiTile
        label="Order to position"
        value={figures.typicalMs == null ? '—' : formatWait(figures.typicalMs)}
        hint={`Typical: half were seen sooner. ${entries}`}
      />
      <KpiTile
        label="Slowest entry"
        value={figures.slowestMs == null ? '—' : formatWait(figures.slowestMs)}
        hint={figures.slowestOrderId ? `Order ${figures.slowestOrderId}` : 'No entry gave a position'}
      />
      <KpiTile
        label="Without a position"
        value={
          <span className={cn(figures.withoutPosition > 0 && 'text-negative')}>
            {formatNumber(figures.withoutPosition)}
          </span>
        }
        hint="Accepted by Saxo, then given up or stopped"
        className="col-span-2 lg:col-span-1"
      />
    </div>
  )
}

export function OrdersPage() {
  const { timezone } = useSession()
  const [searchParams] = useSearchParams()
  // A link from a position: its orders, whenever they were placed.
  const find = searchParams.get('find') ?? ''
  const [range, setRange] = useState<DateRange>(() => (find ? ALL_TIME : lastDays(30)))
  const params = rangeParams(range)

  const ordersQuery = useQuery({
    queryKey: ['orders', params.from, params.to],
    queryFn: () => api.orders(params),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  })

  const columns = useMemo(() => orderColumns(timezone), [timezone])
  const page = ordersQuery.data
  const figures = useMemo(() => entryFigures(page?.items ?? []), [page])

  return (
    <>
      <PageHeader
        title="Orders"
        description={`Orders sent to Saxo by the trader and the position monitor, and what became of each. Times are in ${timezone}.`}
      />

      <div className="flex flex-wrap items-center justify-end gap-2">
        <DateRangeFilter value={range} onChange={setRange} />
        <ExportButton name="wata-orders" rows={page?.items ?? []} columns={ORDER_CSV} />
      </div>

      <QueryState isPending={ordersQuery.isPending} error={ordersQuery.error} hasData={!!page}>
        {page && (
          <div className={cn('space-y-4 transition-opacity', ordersQuery.isPlaceholderData && 'opacity-60')}>
            <EntryTiles figures={figures} />
            <div className="space-y-2">
              <AdvancedDataTable<Order>
                // The search starts over when a link brings another order.
                key={find}
                title={`${formatNumber(page.total)} order${page.total === 1 ? '' : 's'}`}
                emptyTitle="No order in this range"
                searchPlaceholder="Search instrument, id…"
                data={page.items}
                columns={columns}
                getRowId={(row) => row.order_id}
                defaultPageSize={20}
                selectable={false}
                initialSearch={find}
                initialExpandedRowIds={find ? [find] : undefined}
                renderSubComponent={(row) => <OrderTimeline order={row} timeZone={timezone} />}
              />
              <p className="text-xs text-muted-foreground">
                “Took” runs from the order leaving to WATA seeing its position open (or closed), on the server’s clock:
                it holds Saxo’s time and WATA’s own waits between two looks. Open a row for each moment, Saxo’s time
                for the fill among them.
              </p>
              {page.total > page.items.length && (
                <p className="text-xs text-muted-foreground">
                  Showing the {page.items.length} most recent of {page.total}. Narrow the date range to see older ones.
                </p>
              )}
            </div>
          </div>
        )}
      </QueryState>
    </>
  )
}
