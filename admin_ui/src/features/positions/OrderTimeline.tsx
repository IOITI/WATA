import { Link } from 'react-router'

import { PnlValue } from '@/components/status'
import { Badge } from '@/components/ui/badge'
import { formatClock, formatDateTime, formatMoney, formatNumber } from '@/lib/format'
import type { Order, OrderPosition } from '@/lib/types'
import { cn } from '@/lib/utils'

import { orderLife, orderLink, positionLink, RESULTS } from './orderLife'
import type { LifeStep } from './orderLife'

const STEP_TONE: Record<NonNullable<LifeStep['tone']>, string> = {
  danger: 'text-negative',
  warning: 'text-warning',
}

// Step and time side by side, the note under them on a phone and beside them from a tablet up.
const STEP_GRID = 'grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5 sm:grid-cols-[10.5rem_7.5rem_minmax(0,1fr)]'

const LINK = 'font-mono text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground'

function LinkedPosition({ position, currency, timeZone }: { position: OrderPosition; currency: string | null; timeZone: string }) {
  const closed = position.position_status === 'Closed'
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
      <span>Position</span>
      <Link to={positionLink(position.position_id)} className={LINK}>
        {position.position_id}
      </Link>
      <Badge tone={closed ? 'neutral' : 'success'} dot={!closed}>
        {position.position_status ?? 'Unknown'}
      </Badge>
      <span className="tabular-nums">
        {formatNumber(position.position_amount)} at {formatMoney(position.position_open_price, currency)}
        {closed && ` → ${formatMoney(position.position_close_price, currency)}`}
      </span>
      {closed && (
        <>
          <PnlValue value={position.position_profit_loss} kind="money" currency={currency} />
          <span>
            (<PnlValue value={position.position_total_performance_percent} kind="percent" />)
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

interface OrderTimelineProps {
  order: Order
  timeZone: string
  /** Above the steps: "Opened by", "Closed by". The order's id then follows, as a link to the Orders page. */
  heading?: string
  /** The position the order gave or closed, with a link to it. Left out where the position is the row itself. */
  showPosition?: boolean
}

/** The moments of an order, a line each: what, when, on whose clock, and how long after the step before. */
export function OrderTimeline({ order, timeZone, heading, showPosition = true }: OrderTimelineProps) {
  const life = orderLife(order)
  const result = RESULTS[life.result]

  return (
    <div className="space-y-2 text-xs">
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

      {/*
        A grid and not a table: on a phone the note goes under its step, where a
        third column would be a few characters wide. Fixed widths on a wider
        screen, so that two orders shown one under the other line up.
      */}
      <div className="max-w-[60rem]">
        <div
          aria-hidden="true"
          className={cn(STEP_GRID, 'hidden border-b border-border/40 py-1 font-medium text-muted-foreground sm:grid')}
        >
          <span>Step</span>
          <span>Time</span>
          <span>What it tells</span>
        </div>
        <ol aria-label={`The moments of order ${order.order_id}`} className="divide-y divide-border/40">
          {life.steps.map((step) => (
            <li key={step.key} className={cn(STEP_GRID, 'grid py-1.5')}>
              <span className={cn(step.tone && STEP_TONE[step.tone])}>{step.label}</span>
              <span className="whitespace-nowrap tabular-nums">{formatClock(step.at, timeZone)}</span>
              {step.note && (
                <span className="col-span-2 text-muted-foreground [overflow-wrap:anywhere] sm:col-span-1">
                  {step.note}
                </span>
              )}
            </li>
          ))}
        </ol>
      </div>

      {showPosition &&
        (order.position ? (
          <LinkedPosition position={order.position} currency={order.instrument_currency} timeZone={timeZone} />
        ) : (
          // Its details could not be read with the order: the link still leads to it.
          order.position_id && (
            <p className="text-muted-foreground">
              Position{' '}
              <Link to={positionLink(order.position_id)} className={LINK}>
                {order.position_id}
              </Link>
            </p>
          )
        ))}
    </div>
  )
}
