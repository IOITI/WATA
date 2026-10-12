import { useQuery } from '@tanstack/react-query'

import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatMoney, formatNumber } from '@/lib/format'

/** The orders behind a position, shown when its row is expanded. */
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
  return (
    <div className="space-y-3 text-xs">
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

      {orders.length === 0 ? (
        <p className="text-muted-foreground">No order recorded for this position.</p>
      ) : (
        <table className="w-full text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1.5 pr-4 font-medium">Time</th>
              <th className="py-1.5 pr-4 font-medium">Side</th>
              <th className="py-1.5 pr-4 font-medium">Type</th>
              <th className="py-1.5 pr-4 text-right font-medium">Quantity</th>
              <th className="py-1.5 pr-4 text-right font-medium">Price</th>
              <th className="py-1.5 font-medium">Order</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {orders.map((order) => (
              <tr key={order.order_id}>
                <td className="py-1.5 pr-4">{formatDateTime(order.order_time, timezone)}</td>
                <td className="py-1.5 pr-4">{order.buy_sell ?? '—'}</td>
                <td className="py-1.5 pr-4">{[order.order_type, order.order_kind].filter(Boolean).join(' · ') || '—'}</td>
                <td className="py-1.5 pr-4 text-right tabular-nums">{formatNumber(order.order_amount)}</td>
                <td className="py-1.5 pr-4 text-right tabular-nums">
                  {formatMoney(order.instrument_price, order.instrument_currency)}
                </td>
                <td className="py-1.5 font-mono text-muted-foreground">{order.order_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
