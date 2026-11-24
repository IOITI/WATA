import { formatDateTime } from 'ioiti-ui'
import { Link } from 'react-router'

import { RulePeekName } from '@/features/ta/signals/RulePeek'

import { signalLink, signalRef } from './orderLife'

const LINK = 'tabular-nums text-foreground underline decoration-border underline-offset-2 hover:decoration-foreground'

/**
 * The signal an order or a position came from, in a line: what it was to it ("From the signal of", "Sold for the
 * signal of"), its moment as a link to its own page, and the rule of the TA engine that raised it. Nothing without a
 * signal to lead to.
 */
export function SignalLine({
  heading,
  uuid,
  taSignalId,
  ruleId,
  ruleName,
  at,
  timeZone,
}: {
  heading: string
  uuid: string | null | undefined
  taSignalId?: number | null
  ruleId?: string | null
  ruleName?: string | null
  /** The moment it is named by: its bar's close (a rule's), else its reception. */
  at: string | null | undefined
  timeZone: string
}) {
  const ref = signalRef(uuid, taSignalId)
  if (!ref) return null
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
      <span>{heading}</span>
      <Link to={signalLink(ref)} className={LINK}>
        {at ? formatDateTime(at, timeZone) : 'its page'}
      </Link>
      {ruleId ? (
        <span>
          raised by rule <RulePeekName id={ruleId} name={ruleName ?? ruleId} />
        </span>
      ) : (
        <span>from TradingView, the scheduler or by hand</span>
      )}
    </p>
  )
}
