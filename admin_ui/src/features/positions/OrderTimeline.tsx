import { Badge, formatDateTime, formatMoney, formatNumber, InteractiveTimeline, SignedValue } from 'ioiti-ui'
import type { TimelineItem } from 'ioiti-ui'
import { Link } from 'react-router'

import type { Order, OrderPosition, Refusal } from '@/lib/types'

import { orderLife, orderLink, RESULTS, stepToLookAt, timelineItems } from './orderLife'
import type { OrderLife } from './orderLife'
import { PositionPeekLink } from './PositionPeek'
import { RequestList } from './RequestList'

const LINK = 'font-mono text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground'

function LinkedPosition({ position, currency, timeZone }: { position: OrderPosition; currency: string | null; timeZone: string }) {
  const closed = position.position_status === 'Closed'
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
      <span>Position</span>
      <PositionPeekLink id={position.position_id} className={LINK}>
        {position.position_id}
      </PositionPeekLink>
      <Badge tone={closed ? 'neutral' : 'success'} dot={!closed}>
        {position.position_status ?? 'Unknown'}
      </Badge>
      <span className="tabular-nums">
        {formatNumber(position.position_amount)} at {formatMoney(position.position_open_price, currency)}
        {closed && ` → ${formatMoney(position.position_close_price, currency)}`}
      </span>
      {closed && (
        <>
          <SignedValue value={position.position_profit_loss} kind="money" currency={currency} />
          <span>
            (<SignedValue value={position.position_total_performance_percent} kind="percent" />)
          </span>
          <span>closed {formatDateTime(position.execution_time_close, timeZone)}</span>
        </>
      )}
      {closed && position.position_close_reason && (
        <span className="basis-full text-foreground/70">{position.position_close_reason}</span>
      )}
    </p>
  )
}

/**
 * What an order is, in a line and a sentence: what it did for the position ("Opened by"), its id (a link to the
 * Orders page) and when it left, then what became of it.
 */
export function OrderHeading({ order, life, timeZone, heading }: { order: Order; life: OrderLife; timeZone: string; heading?: string }) {
  const result = RESULTS[life.result]
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {heading && (
          <>
            <span className="font-medium text-foreground">{heading}</span>
            <Link to={orderLink(order.order_id)} className={LINK}>
              {order.order_id}
            </Link>
            <span className="text-muted-foreground">{formatDateTime(order.sent_at ?? order.order_time, timeZone)}</span>
          </>
        )}
        <Badge tone={result.tone}>{result.label}</Badge>
      </div>
      <p className="max-w-[60rem] text-foreground/80">{life.summary}</p>
    </div>
  )
}

/**
 * EasyUI's timeline of moments, each with its note open, the first that went wrong (else the last) picked out.
 * Made again when its steps change (an order in flight gains one), so that a new step opens too.
 */
export function MomentsTimeline({ items, label }: { items: TimelineItem[]; label: string }) {
  if (items.length === 0) return null
  return (
    <InteractiveTimeline
      key={items.map((item) => item.id).join('|')}
      items={items}
      label={label}
      defaultExpandedIds={items.map((item) => item.id)}
      defaultSelectedId={stepToLookAt(items)}
      className="max-w-[60rem]"
    />
  )
}

interface OrderTimelineProps {
  order: Order
  timeZone: string
  /** Above the steps: "Opened by", "Closed by". The order's id then follows, as a link to the Orders page. */
  heading?: string
  /** The position the order gave or closed, with a link to it. Left out where the position is the row itself. */
  showPosition?: boolean
  /** The sells of the same position Saxo refused just before this order went through. */
  refusal?: Refusal | null
  /** Its requests to Saxo under the steps. Left out where every request about the position is listed below. */
  showRequests?: boolean
}

/** The moments of an order, on a timeline in the order they happened: what, when, on whose clock, and how long after what. */
export function OrderTimeline({
  order,
  timeZone,
  heading,
  showPosition = true,
  refusal = null,
  showRequests = true,
}: OrderTimelineProps) {
  const life = orderLife(order, refusal)

  return (
    <div className="space-y-2 text-xs">
      <OrderHeading order={order} life={life} timeZone={timeZone} heading={heading} />
      <MomentsTimeline items={timelineItems(life.steps, timeZone)} label={`The moments of order ${order.order_id}`} />

      {showRequests && order.requests && order.requests.length > 0 && (
        <div className="space-y-1">
          <p className="font-medium text-foreground">Requests to Saxo</p>
          <RequestList requests={order.requests} timeZone={timeZone} label={`The requests of order ${order.order_id}`} />
        </div>
      )}

      {showPosition &&
        (order.position ? (
          <LinkedPosition position={order.position} currency={order.instrument_currency} timeZone={timeZone} />
        ) : (
          // Its details could not be read with the order: the link still leads to it.
          order.position_id && (
            <p className="text-muted-foreground">
              Position{' '}
              <PositionPeekLink id={order.position_id} className={LINK}>
                {order.position_id}
              </PositionPeekLink>
            </p>
          )
        ))}
    </div>
  )
}
