import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { PeekLink } from '@/components/PeekLink'
import { ActionBadge, PnlValue } from '@/components/status'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import { formatDateTime, formatMoney, formatNumber } from '@/lib/format'

import { positionLink } from './orderLife'

/** What a position's link shows of it: what it was, what it made, when. */
export function PositionPeek({ id }: { id: string }) {
  const { timezone } = useSession()
  const detailQuery = useQuery({ queryKey: ['position', id], queryFn: () => api.position(id) })
  if (detailQuery.isPending) return <Skeleton className="h-20 w-full" />
  const position = detailQuery.data?.position
  if (!position) return <p>Position {id} could not be read.</p>
  const open = position.position_status === 'Open'
  const currency = position.instrument_currency
  const figures: [string, ReactNode][] = [
    ['Quantity', formatNumber(position.position_amount)],
    ['Bought at', formatMoney(position.position_open_price, currency)],
    ['Opened', formatDateTime(position.execution_time_open, timezone)],
  ]
  if (open) {
    figures.push(['Now', <PnlValue key="now" value={position.live_performance_percent} kind="percent" />])
  } else {
    figures.push(
      ['Sold at', formatMoney(position.position_close_price, currency)],
      ['Result', <PnlValue key="result" value={position.position_profit_loss} kind="money" currency={currency} />],
      ['Closed', formatDateTime(position.execution_time_close, timezone)],
    )
  }
  return (
    <div className="space-y-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{position.instrument_name ?? `Position ${id}`}</p>
          <p className="truncate">
            Position {id} · {open ? 'open' : (position.position_close_reason ?? 'closed')}
          </p>
        </div>
        <ActionBadge action={position.action} />
      </div>
      <dl className="space-y-1">
        {figures.map(([label, value]) => (
          <div key={label} className="flex justify-between gap-3">
            <dt>{label}</dt>
            <dd className="tabular-nums text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** A link to a position, what it was and made shown on hover, keyboard focus or a first tap. */
export function PositionPeekLink({
  id,
  className,
  children,
  'aria-label': label,
}: {
  id: string
  className?: string
  children: ReactNode
  'aria-label'?: string
}) {
  return (
    <PeekLink to={positionLink(id)} content={() => <PositionPeek id={id} />} className={className} aria-label={label}>
      {children}
    </PeekLink>
  )
}
