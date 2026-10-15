import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatMoney } from '@/lib/format'
import { cn } from '@/lib/utils'

import { OrderTimeline } from './OrderTimeline'
import { closureWithoutOrder, orderLink, purposeOf } from './orderLife'
import { sellRefusalSentence, sellRefusalsOf } from './orderRequests'
import { RequestList } from './RequestList'

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
  const requests = detailQuery.data.requests ?? []
  const opening = orders.filter((order) => purposeOf(order) === 'open')
  const closing = orders.filter((order) => purposeOf(order) === 'close')
  const noSellOrder = closureWithoutOrder(position, orders)
  const stillOpen = position.position_status === 'Open'
  const refusals = sellRefusalsOf(requests)

  return (
    <div className="space-y-4 text-xs">
      {refusals && (
        // Said first, in red while it lasts: WATA wants this position sold and Saxo does not take the sell.
        <p className={cn('max-w-[60rem]', stillOpen ? 'font-medium text-negative' : 'text-foreground/80')}>
          {sellRefusalSentence(refusals, timezone, stillOpen)}{' '}
          <Link to={orderLink(position.position_id)} className="underline decoration-current/40 underline-offset-2">
            See them among the orders
          </Link>
        </p>
      )}
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
        <OrderTimeline
          key={order.order_id}
          order={order}
          timeZone={timezone}
          heading="Opened by order"
          showPosition={false}
          showRequests={false}
        />
      ))}
      {closing.map((order) => (
        <OrderTimeline
          key={order.order_id}
          order={order}
          timeZone={timezone}
          heading="Closed by order"
          showPosition={false}
          showRequests={false}
        />
      ))}
      {noSellOrder && <p className="max-w-[60rem] text-muted-foreground">{noSellOrder}</p>}

      {requests.length > 0 && (
        <div className="space-y-1">
          <p className="font-medium text-foreground">Requests to Saxo about this position</p>
          <RequestList
            requests={requests}
            timeZone={timezone}
            label={`The requests about position ${position.position_id}`}
            hidden={(detailQuery.data.requests_total ?? requests.length) - requests.length}
          />
        </div>
      )}
    </div>
  )
}
