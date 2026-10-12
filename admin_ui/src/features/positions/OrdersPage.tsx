import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { DateRangeFilter } from '@/components/DateRangeFilter'
import { ExportButton } from '@/components/ExportButton'
import { PageHeader } from '@/components/Panel'
import { QueryState } from '@/components/status'
import { AdvancedDataTable } from '@/components/ui/advanced-data-table'
import type { DateRange } from '@/components/ui/calendar'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatNumber } from '@/lib/format'
import { lastDays, rangeParams } from '@/lib/dateRange'
import type { Order } from '@/lib/types'
import { cn } from '@/lib/utils'

import { ORDER_CSV, orderColumns } from './columns'

export function OrdersPage() {
  const { timezone } = useSession()
  const [range, setRange] = useState<DateRange>(() => lastDays(30))
  const params = rangeParams(range)

  const ordersQuery = useQuery({
    queryKey: ['orders', params.from, params.to],
    queryFn: () => api.orders(params),
    refetchInterval: 30_000,
    placeholderData: keepPreviousData,
  })

  const columns = useMemo(() => orderColumns(timezone), [timezone])
  const page = ordersQuery.data

  return (
    <>
      <PageHeader title="Orders" description={`Orders placed at Saxo by the trader. Times are in ${timezone}.`} />

      <div className="flex flex-wrap items-center justify-end gap-2">
        <DateRangeFilter value={range} onChange={setRange} />
        <ExportButton name="wata-orders" rows={page?.items ?? []} columns={ORDER_CSV} />
      </div>

      <QueryState isPending={ordersQuery.isPending} error={ordersQuery.error} hasData={!!page}>
        {page && (
          <div className={cn('space-y-2 transition-opacity', ordersQuery.isPlaceholderData && 'opacity-60')}>
            <AdvancedDataTable<Order>
              title={`${formatNumber(page.total)} order${page.total === 1 ? '' : 's'}`}
              emptyTitle="No order in this range"
              searchPlaceholder="Search instrument, id…"
              data={page.items}
              columns={columns}
              getRowId={(row) => row.order_id}
              defaultPageSize={20}
              selectable={false}
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
