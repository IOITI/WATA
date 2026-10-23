/**
 * What the card that follows an operator's request says while it waits: the close of a position asked of the
 * position monitor, or a signal sent through the web server as TradingView sends one. It reads what the pages
 * already read, a position's orders and requests to Saxo and the signal journal; the live events refresh both.
 * Pure: the provider asks again, this tells.
 *
 * The ask is timed by the browser's clock and a request by the server's: a request is taken as this one's from a
 * little before the ask on (`CLOCK_SLACK_MS`).
 */

import { positionLink, purposeOf, tookMs } from '@/features/positions/orderLife'
import { answerText } from '@/features/positions/orderRequests'
import { formatMoney, formatNumber, formatPercent, formatTime, formatWait } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { JournalEntry, Order, OrderRequest, Position, PositionDetail } from '@/lib/types'

export type FollowStatus = 'processing' | 'verifying' | 'success' | 'failed' | 'unknown'

export interface FollowFigure {
  label: string
  value: string
}

export interface FollowState {
  status: FollowStatus
  title: string
  description: string
  /** What it is about, in a few words. */
  subject: string
  figures: FollowFigure[]
  reference?: { label: string; value: string }
  link?: { href: string; label: string }
  /** Nothing more will come: the card stops asking. */
  final: boolean
}

/** The server's and the browser's clocks differ a little: a request this long before the ask is still its own. */
export const CLOCK_SLACK_MS = 10_000
/**
 * The position monitor drops a close it has not taken within a minute of its asking (CLOSE_REQUEST_MAX_AGE_SECONDS
 * in src/position_monitor): past this, no sell is coming for it.
 */
export const CLOSE_GIVE_UP_MS = 75_000
/** A signal the trader has not decided on by then was most likely replaced by a newer one before it read it. */
export const SIGNAL_GIVE_UP_MS = 60_000
/** How long a card asks at most, whatever it says. */
export const FOLLOW_FOR_MS = 120_000
export const POLL_MS = 2_000

/** A close asked of the position monitor, from the dashboard, the Positions page or the top bar. */
export interface CloseAsked {
  positionId: string
  instrument: string | null
  amount: number | null
  askedAt: number
}

/** A signal sent from the Signals page. */
export interface SignalSent {
  signalUuid: string
  action: string
  indice: string | null
  sentAt: number
}

function after(value: string | null | undefined, moment: number): boolean {
  const at = value ? Date.parse(value) : Number.NaN
  return Number.isFinite(at) && at >= moment - CLOCK_SLACK_MS
}

function closeSubject(asked: CloseAsked): string {
  const what = asked.instrument ?? `Position ${asked.positionId}`
  return asked.amount === null ? what : `${formatNumber(asked.amount)} × ${what}`
}

/** The sells sent for this close, oldest first, and the close order Saxo took. */
function sellsOf(detail: PositionDetail | undefined, askedAt: number): { requests: OrderRequest[]; order: Order | undefined } {
  const requests = (detail?.requests ?? []).filter(
    (request) => request.operation === 'place' && request.purpose === 'close' && after(request.requested_at, askedAt),
  )
  const orders = (detail?.orders ?? []).filter((order) => purposeOf(order) === 'close' && after(order.sent_at ?? order.order_time, askedAt))
  return { requests, order: orders[orders.length - 1] }
}

/** Where the close of a position stands, from its details (`api.position`), `now` on the browser's clock. */
export function closeProgress(asked: CloseAsked, detail: PositionDetail | undefined, now: number, timeZone: string): FollowState {
  const position = detail?.position
  const base = {
    subject: closeSubject(asked),
    reference: { label: 'Position', value: asked.positionId },
    link: { href: positionLink(asked.positionId), label: 'Open the position' },
  }
  const { requests, order } = sellsOf(detail, asked.askedAt)

  if (position && position.position_status !== 'Open') {
    const currency = position.instrument_currency
    const took = order ? tookMs(order) : null
    const reason = position.position_close_reason
    const figures: FollowFigure[] = [
      { label: 'Sold at', value: formatMoney(position.position_close_price, currency) },
      {
        label: 'Result',
        value: `${formatMoney(position.position_profit_loss, currency, true)} (${formatPercent(position.position_total_performance_percent, true)})`,
      },
      { label: 'Closed at', value: formatTime(position.execution_time_close, timeZone) },
    ]
    if (took !== null) figures.push({ label: 'Order to closed', value: formatWait(took) })
    // An operator's close is recorded as "Explicit Close (position <id>)" (src/trade/async_services.py).
    const ours = !reason || reason.startsWith('Explicit Close (position')
    return {
      ...base,
      status: 'success',
      title: 'Sold',
      description: ours ? 'The position monitor sold it at market.' : `It was closed by something else first: ${reason}.`,
      figures,
      final: true,
    }
  }

  const last = requests[requests.length - 1]
  if (order || last?.outcome === 'accepted') {
    const figures: FollowFigure[] = []
    if (last?.requested_at) figures.push({ label: 'Sell sent at', value: formatTime(last.requested_at, timeZone) })
    if (last?.price_seen != null) figures.push({ label: 'Bid it was sent on', value: formatMoney(last.price_seen, last.instrument_currency) })
    const orderId = order?.order_id ?? last?.order_id
    return {
      ...base,
      status: 'verifying',
      title: 'Saxo took the sell',
      description: 'Waiting for the position to show closed at Saxo.',
      figures,
      reference: orderId ? { label: 'Order', value: orderId } : base.reference,
      final: false,
    }
  }

  if (last) {
    const figures: FollowFigure[] = [{ label: 'Sell sent at', value: formatTime(last.requested_at, timeZone) }]
    if (requests.length > 1) figures.push({ label: 'Attempts', value: formatNumber(requests.length) })
    const refused = last.outcome === 'refused'
    return {
      ...base,
      status: 'failed',
      title: refused ? 'Saxo refused the sell' : last.outcome === 'no_answer' ? 'Saxo did not answer the sell' : 'The sell failed',
      description:
        last.outcome === 'no_answer'
          ? 'Saxo may have taken it all the same: check the account at Saxo. The position is still open here.'
          : `${answerText(last)}. The position is still open.`,
      figures,
      // The monitor may try again: a sell it gets through still shows here.
      final: false,
    }
  }

  if (now - asked.askedAt < CLOSE_GIVE_UP_MS) {
    return {
      ...base,
      status: 'processing',
      title: 'Close asked',
      description: 'The position monitor sells it at market as soon as it takes the request.',
      figures: [],
      final: false,
    }
  }
  return {
    ...base,
    status: 'unknown',
    title: 'No sell seen',
    description:
      'No sell was sent within a minute of the request, which the monitor then drops. It says why on Telegram when it cannot take one.',
    figures: [],
    final: true,
  }
}

function signalSubject(sent: SignalSent): string {
  return [`${actionLabel(sent.action)} signal`, sent.indice].filter(Boolean).join(' · ')
}

/**
 * Where a signal sent by hand stands, from its entry in the journal and the position it bought, `now` on the
 * browser's clock.
 */
export function signalProgress(
  sent: SignalSent,
  entry: JournalEntry | undefined,
  position: Position | undefined,
  now: number,
  timeZone: string,
): FollowState {
  const base = { subject: signalSubject(sent), reference: { label: 'Signal', value: sent.signalUuid } }

  if (!entry) {
    if (now - sent.sentAt < SIGNAL_GIVE_UP_MS) {
      return {
        ...base,
        status: 'processing',
        title: 'Signal sent',
        description: 'The web server holds it: the trader takes it at its next poll.',
        figures: [],
        final: false,
      }
    }
    return {
      ...base,
      status: 'unknown',
      title: 'No decision seen',
      description: `The trader journaled nothing for it within a minute. A newer signal${sent.indice ? ` for ${sent.indice}` : ''} may have replaced it before the trader read it, and such a signal never reaches the trader.`,
      figures: [],
      final: true,
    }
  }

  const figures: FollowFigure[] = [{ label: 'Decided at', value: formatTime(entry.decided_at, timeZone) }]
  const received = entry.received_timestamp ? Date.parse(entry.received_timestamp) : Number.NaN
  const decided = entry.decided_at ? Date.parse(entry.decided_at) : Number.NaN
  if (Number.isFinite(received) && Number.isFinite(decided)) figures.push({ label: 'Received to decided', value: formatWait(decided - received) })
  const reason = entry.reason ?? ''

  switch (entry.outcome) {
    case 'executed': {
      if (!entry.position_id) {
        return { ...base, status: 'success', title: 'Bought', description: reason || 'The trader bought.', figures, final: true }
      }
      const link = { href: positionLink(entry.position_id), label: 'Open the position' }
      if (!position) {
        return { ...base, status: 'verifying', title: 'Bought', description: 'Reading the position it opened.', figures, link, final: false }
      }
      const currency = position.instrument_currency
      return {
        ...base,
        status: 'success',
        title: 'Bought',
        description: position.instrument_name ?? `Position ${position.position_id}`,
        figures: [
          ...figures,
          { label: 'Quantity', value: formatNumber(position.position_amount) },
          { label: 'Bought at', value: formatMoney(position.position_open_price, currency) },
        ],
        link,
        final: true,
      }
    }
    case 'closed':
      return { ...base, status: 'success', title: 'Positions closed', description: reason || 'The trader closed them.', figures, final: true }
    case 'refused':
      return { ...base, status: 'failed', title: 'Refused by the trader', description: reason || 'One of its rules refused it.', figures, final: true }
    case 'failed':
      return { ...base, status: 'failed', title: 'The trader failed', description: reason || 'It could not carry it out.', figures, final: true }
    case 'ignored':
      return { ...base, status: 'unknown', title: 'Nothing to do', description: reason || 'There was nothing for it to act on.', figures, final: true }
    default:
      return { ...base, status: 'unknown', title: `Decided: ${entry.outcome}`, description: reason, figures, final: true }
  }
}
