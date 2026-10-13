import { useQuery } from '@tanstack/react-query'

import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatMoney } from '@/lib/format'

import { OrderTimeline } from './OrderTimeline'
import { closureWithoutOrder, purposeOf } from './orderLife'

/** The orders behind a position, shown when its row is expanded: the one that opened it, the one that closed it. */
export function PositionOrders({ positionId }: { positionId: string }) {
  const { timezone } = useSession()
  const detailQuery = useQuery({
    queryKey: ['position', positionId],
    queryFn: () => api.position(positionId),
  })

  if (detailQuery.isPending) return <Skeleton className="h-16 w-full" />
  if (detailQuery.isError) {
    return <p className="text-xs text-negative">Could not load the orders: {detailQuery.error.message}</p>
  }

  const { position, orders } = detailQuery.data
  const opening = orders.filter((order) => purposeOf(order) === 'open')
  const closing = orders.filter((order) => purposeOf(order) === 'close')
  const noSellOrder = closureWithoutOrder(position, orders)

  return (
    <div className="space-y-4 text-xs">
      <dl className="flex flex-wrap gap-x-8 gap-y-2 text-muted-foreground">
        <div>
          <dt>Position</dt>
          <dd className="font-mono text-foreground/80">{position.position_id}</dd>
        </div>
        <div>
          <dt>Kind</dt>
          <dd className="text-foreground/80">{position.position_kind ?? '—'}</dd>
        </div>
        <div>
          <dt>Total at open</dt>
          <dd className="tabular-nums text-foreground/80">
            {formatMoney(position.position_total_open_price, position.instrument_currency)}
          </dd>
        </div>
        <div>
          <dt>Total at close</dt>
          <dd className="tabular-nums text-foreground/80">
            {formatMoney(position.position_total_close_price, position.instrument_currency)}
          </dd>
        </div>
      </dl>

      {opening.length === 0 && <p className="text-muted-foreground">No order recorded for this position.</p>}
      {opening.map((order) => (
        <OrderTimeline key={order.order_id} order={order} timeZone={timezone} heading="Opened by order" showPosition={false} />
      ))}
      {closing.map((order) => (
        <OrderTimeline key={order.order_id} order={order} timeZone={timezone} heading="Closed by order" showPosition={false} />
      ))}
      {noSellOrder && <p className="max-w-[60rem] text-muted-foreground">{noSellOrder}</p>}
    </div>
  )
}
