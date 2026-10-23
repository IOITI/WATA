import type { TimelineItem, TimelineStatus } from '@/components/ui/interactive-timeline'
import { formatClock, formatNumber, formatWait } from '@/lib/format'
import type { Order, Refusal, SellRefusals } from '@/lib/types'

import { answerText, sellRefusalLabel } from './orderRequests'

/**
 * What became of an order, read from what the service that sent it kept: the
 * moments it left, was accepted, and gave (or closed) its position. Two clocks
 * are involved: the server's for what WATA did and saw, Saxo's for the fill.
 */

export type Purpose = 'open' | 'close'

export type OrderResult =
  | 'opened'
  | 'closed'
  | 'no_position'
  | 'not_seen_closed'
  | 'error'
  | 'not_recorded'

export type ResultTone = 'neutral' | 'success' | 'warning' | 'danger'

export const RESULTS: Record<OrderResult, { label: string; tone: ResultTone }> = {
  opened: { label: 'Opened a position', tone: 'success' },
  closed: { label: 'Closed a position', tone: 'neutral' },
  no_position: { label: 'No position', tone: 'danger' },
  not_seen_closed: { label: 'Not seen closed', tone: 'warning' },
  error: { label: 'Stopped by an error', tone: 'danger' },
  not_recorded: { label: 'Outcome not recorded', tone: 'warning' },
}

export const RESULT_FILTER = (Object.keys(RESULTS) as OrderResult[]).map((value) => ({
  value,
  label: RESULTS[value].label,
}))

export interface LifeStep {
  key: string
  label: string
  /** The moment, or null for a step that has none of its own. */
  at: string | null
  /** Whose clock the moment was read on. */
  clock: 'wata' | 'saxo'
  note: string
  tone?: 'danger' | 'warning'
}

export interface OrderLife {
  purpose: Purpose
  /** False for an order placed before its moments were kept. */
  tracked: boolean
  result: OrderResult
  steps: LifeStep[]
  /** What happened, in a sentence. */
  summary: string
  /** From the order leaving to its position being seen, or to the giving up: the server's clock alone. */
  tookMs: number | null
}

/** Where a row of the other page is found: its search, set on the id. */
export function positionLink(positionId: string): string {
  return `/positions?find=${encodeURIComponent(positionId)}`
}

export function orderLink(orderId: string): string {
  return `/orders?find=${encodeURIComponent(orderId)}`
}

/** What the order was for: as recorded, or read from its side for the orders placed before. */
export function purposeOf(order: Order): Purpose {
  return order.purpose ?? (order.buy_sell === 'Sell' ? 'close' : 'open')
}

function isTracked(order: Order): boolean {
  return !!order.sent_at
}

/** Milliseconds from one moment to the next, null when either is missing or unreadable. */
function between(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null
  const span = Date.parse(to) - Date.parse(from)
  return Number.isFinite(span) ? span : null
}

function ordinal(count: number): string {
  const lastTwo = count % 100
  if (lastTwo >= 11 && lastTwo <= 13) return `${count}th`
  return `${count}${['th', 'st', 'nd', 'rd'][count % 10] ?? 'th'}`
}

function looks(count: number | null | undefined): string {
  return count === 1 ? '1 look' : `${count ?? 0} looks`
}

export function orderResult(order: Order): OrderResult {
  const purpose = purposeOf(order)
  if (!isTracked(order)) {
    // All that was written then was a buy order whose position had been confirmed.
    if (purpose === 'open') return order.position_id ? 'opened' : 'not_recorded'
    return 'closed'
  }
  if (order.outcome === 'confirmed') return purpose === 'open' ? 'opened' : 'closed'
  if (order.outcome === 'gave_up') return purpose === 'open' ? 'no_position' : 'not_seen_closed'
  if (order.outcome === 'error') return 'error'
  return 'not_recorded'
}

/** How long the order took, on the server's clock alone: to its position being seen, or to the giving up. */
export function tookMs(order: Order): number | null {
  return between(order.sent_at, order.confirmed_at ?? order.gave_up_at)
}

/** Saxo's own time for the fill (or the closure), set against the moment the order left. */
function saxoStep(order: Order, purpose: Purpose): LifeStep | null {
  const at = purpose === 'open' ? order.position?.execution_time_open : order.position?.execution_time_close
  if (!at) return null
  const afterLeaving = between(order.sent_at, at)
  let note = "Saxo's clock"
  if (afterLeaving != null) {
    note =
      afterLeaving >= 0
        ? `Saxo's clock: ${formatWait(afterLeaving)} after the order left`
        : `Saxo's clock: ${formatWait(-afterLeaving)} before the order left, so the two clocks differ by at least that`
  }
  return { key: 'saxo', label: purpose === 'open' ? 'Filled at Saxo' : 'Closed at Saxo', at, clock: 'saxo', note }
}

function sentAndAccepted(order: Order, purpose: Purpose): LifeStep[] {
  const steps: LifeStep[] = []
  const received = order.signal?.received_timestamp
  if (purpose === 'open' && received) {
    steps.push({ key: 'signal', label: 'Signal received', at: received, clock: 'wata', note: '' })
  }
  const afterSignal = purpose === 'open' ? between(received, order.sent_at) : null
  steps.push({
    key: 'sent',
    label: purpose === 'open' ? 'Order sent' : 'Sell order sent',
    at: order.sent_at ?? null,
    clock: 'wata',
    note: afterSignal != null && afterSignal >= 0 ? `${formatWait(afterSignal)} after the signal` : '',
  })
  const answered = between(order.sent_at, order.accepted_at)
  steps.push({
    key: 'accepted',
    label: 'Accepted by Saxo',
    at: order.accepted_at ?? null,
    clock: 'wata',
    note: answered != null ? `${formatWait(answered)} after it left` : '',
  })
  return steps
}

function seenNote(order: Order): string {
  const afterAccepted = between(order.accepted_at, order.confirmed_at)
  const wait = afterAccepted != null ? `${formatWait(afterAccepted)} after Saxo accepted the order` : ''
  const look = order.lookups ? `at the ${ordinal(order.lookups)} look` : ''
  return [wait, look].filter(Boolean).join(', ')
}

function givenUpAfter(order: Order): string {
  const waited = between(order.accepted_at, order.gave_up_at)
  return [waited != null ? `after ${formatWait(waited)}` : '', `${looks(order.lookups)}`].filter(Boolean).join(' and ')
}

function cancellation(order: Order): LifeStep | null {
  if (order.cancel_accepted == null) return null
  if (order.cancel_accepted) {
    return { key: 'cancel', label: 'Cancellation', at: null, clock: 'wata', note: 'Accepted by Saxo' }
  }
  return {
    key: 'cancel',
    label: 'Cancellation',
    at: null,
    clock: 'wata',
    note: order.detail ? `Refused by Saxo: ${order.detail}` : 'Refused by Saxo',
    tone: 'danger',
  }
}

/** Attempts Saxo did not take, counted over the time they took: "37 attempts over 4 min". */
function attemptsOver(count: number, firstAt: string | null, lastAt: string | null): string {
  const span = between(firstAt, lastAt)
  const attempts = count === 1 ? '1 attempt' : `${formatNumber(count)} attempts`
  return `${attempts}${span != null && span > 0 ? ` over ${formatWait(span)}` : ''}`
}

/** The sells of the same position Saxo refused before taking this one. */
function refusedBefore(refusal: Refusal): LifeStep {
  const answer =
    refusal.answers.length === 1 ? answerText(refusal.answers[0]) : `${refusal.answers.length} different answers`
  return {
    key: 'refused',
    label: refusal.count === 1 ? 'Refused by Saxo' : `Refused by Saxo ×${formatNumber(refusal.count)}`,
    at: refusal.first_at,
    clock: 'wata',
    note: `${attemptsOver(refusal.count, refusal.first_at, refusal.last_at)}: ${answer}`,
    tone: 'danger',
  }
}

/** A position's sells Saxo did not take, from its requests: their last answer is all they keep of what Saxo said. */
function sellsRefused(refusals: SellRefusals): LifeStep {
  const answer = answerText(refusals)
  return {
    key: 'sells-refused',
    label: sellRefusalLabel(refusals),
    at: refusals.first_at,
    clock: 'wata',
    note: refusals.count === 1 ? answer : `${attemptsOver(refusals.count, refusals.first_at, refusals.last_at)}, the last answered: ${answer}`,
    tone: 'danger',
  }
}

/** Orders resting on the position (its protective stop) cancelled on the way to this sell. */
function restingOrdersCancelled(order: Order): LifeStep[] {
  return (order.requests ?? [])
    .filter((request) => request.operation === 'cancel' && request.purpose === 'close')
    .map((request) => ({
      key: `cancel-${request.request_id}`,
      label: request.outcome === 'accepted' ? 'Resting orders cancelled' : 'Resting orders not cancelled',
      at: request.requested_at,
      clock: 'wata' as const,
      note:
        request.outcome === 'accepted'
          ? `Order ${request.order_id ?? ''}`.trim()
          : `Order ${request.order_id ?? ''}: ${answerText(request)}`.trim(),
      tone: request.outcome === 'accepted' ? undefined : ('warning' as const),
    }))
}

const REFUSED_CANCEL =
  'Saxo refused the cancellation, so the order may have been filled after the trader stopped looking: check the account at Saxo for a position WATA does not know.'

function trackedLife(
  order: Order,
  purpose: Purpose,
  result: OrderResult,
  refusal: Refusal | null,
): Pick<OrderLife, 'steps' | 'summary'> {
  const life = trackedSteps(order, purpose, result)
  // What came before the order left: the sells Saxo refused, the resting orders cancelled.
  const before = [...(refusal ? [refusedBefore(refusal)] : []), ...restingOrdersCancelled(order)]
  if (before.length > 0) {
    const sent = life.steps.findIndex((step) => step.key === 'sent')
    life.steps.splice(sent < 0 ? 0 : sent, 0, ...before)
  }
  if (refusal) {
    const sells = refusal.count === 1 ? 'a sell order' : `${formatNumber(refusal.count)} sell orders`
    life.summary = `${life.summary} Saxo had refused ${sells} of this position before taking this one.`
  }
  return life
}

function trackedSteps(order: Order, purpose: Purpose, result: OrderResult): Pick<OrderLife, 'steps' | 'summary'> {
  const steps = sentAndAccepted(order, purpose)
  const saxo = saxoStep(order, purpose)
  const took = tookMs(order)
  const tookText = took != null ? formatWait(took) : 'an unknown time'

  if (result === 'opened' || result === 'closed') {
    if (saxo) steps.push(saxo)
    steps.push({
      key: 'seen',
      label: purpose === 'open' ? 'Seen by WATA' : 'Seen closed by WATA',
      at: order.confirmed_at ?? null,
      clock: 'wata',
      note: seenNote(order),
    })
    const summary =
      purpose === 'open'
        ? `WATA saw the position ${tookText} after the order left.`
        : `WATA saw the position closed ${tookText} after the sell order left.`
    return { steps, summary }
  }

  if (result === 'no_position') {
    steps.push({
      key: 'gave_up',
      label: 'No position',
      at: order.gave_up_at ?? null,
      clock: 'wata',
      note: givenUpAfter(order),
      tone: 'danger',
    })
    const cancel = cancellation(order)
    if (cancel) steps.push(cancel)
    const waited = between(order.accepted_at, order.gave_up_at)
    const shown = `Saxo accepted the order and showed no position${waited != null ? ` for ${formatWait(waited)}` : ''}.`
    if (order.cancel_accepted === false) return { steps, summary: `${shown} ${REFUSED_CANCEL}` }
    return {
      steps,
      summary: order.cancel_accepted ? `${shown} The trader cancelled it, which Saxo accepted.` : shown,
    }
  }

  if (result === 'not_seen_closed') {
    if (saxo) steps.push(saxo)
    steps.push({
      key: 'gave_up',
      label: 'Not seen closed',
      at: order.gave_up_at ?? null,
      clock: 'wata',
      note: [givenUpAfter(order), order.detail].filter(Boolean).join(': '),
      tone: 'warning',
    })
    const later =
      order.position?.position_status === 'Closed'
        ? ' The closure was recorded later, when the database was set against Saxo.'
        : ' The position is still recorded as open.'
    return {
      steps,
      summary: `Saxo accepted the sell order but did not list the position among its closed ones in time.${later}`,
    }
  }

  if (result === 'error') {
    steps.push({
      key: 'error',
      label: 'Stopped by an error',
      at: order.gave_up_at ?? null,
      clock: 'wata',
      note: order.detail ?? '',
      tone: 'danger',
    })
    if (purpose === 'open') {
      const cancelled =
        order.cancel_accepted == null
          ? ''
          : order.cancel_accepted
            ? ' The order was cancelled, which Saxo accepted.'
            : ` ${REFUSED_CANCEL}`
      return { steps, summary: `An error stopped the trader once Saxo had the order.${cancelled}` }
    }
    return { steps, summary: 'An error stopped the look for the closed position once Saxo had the sell order.' }
  }

  steps.push({
    key: 'unknown',
    label: 'Outcome not recorded',
    at: null,
    clock: 'wata',
    note: 'The service stopped before it knew, or has not written it yet',
    tone: 'warning',
  })
  return { steps, summary: 'Saxo accepted the order; what became of it was not recorded.' }
}

/** An order placed before its moments were kept: the fill at Saxo, and the second WATA wrote it down. */
function untrackedLife(order: Order, purpose: Purpose): Pick<OrderLife, 'steps' | 'summary'> {
  const steps: LifeStep[] = []
  const saxo = saxoStep(order, purpose)
  if (saxo) steps.push({ ...saxo, note: "Saxo's clock" })
  steps.push({
    key: 'recorded',
    label: 'Recorded by WATA',
    at: order.order_time,
    clock: 'wata',
    note: 'To the second, once the position was confirmed',
  })
  return {
    steps,
    summary:
      'Placed before WATA kept the moments of an order: when it left and when Saxo accepted it were not written down.',
  }
}

/** ``refusal``: the sells of the same position Saxo refused just before this order went through. */
export function orderLife(order: Order, refusal: Refusal | null = null): OrderLife {
  const purpose = purposeOf(order)
  const tracked = isTracked(order)
  const result = orderResult(order)
  const life = tracked ? trackedLife(order, purpose, result, refusal) : untrackedLife(order, purpose)
  return { purpose, tracked, result, tookMs: tookMs(order), ...life }
}

function statusOf(step: LifeStep): TimelineStatus {
  if (step.tone === 'danger') return 'failed'
  if (step.tone === 'warning') return 'warning'
  return 'completed'
}

/**
 * Steps as the timeline draws them: the moment on the clock it was read on (a
 * dash for a step without one), the note under it. Ids are prefixed by the order
 * when a timeline holds several; the tag says which order of a position a step is of.
 */
export function timelineItems(steps: LifeStep[], timeZone: string, { prefix = '', tag }: { prefix?: string; tag?: string } = {}): TimelineItem[] {
  return steps.map((step) => ({
    id: `${prefix}${step.key}`,
    title: step.label,
    timestamp: formatClock(step.at, timeZone),
    status: statusOf(step),
    ...(step.note ? { description: step.note } : {}),
    ...(tag ? { tag } : {}),
  }))
}

/** The step a timeline is opened on: the first that went wrong, else the last. */
export function stepToLookAt(items: TimelineItem[]): string | undefined {
  return (items.find((item) => item.status === 'failed' || item.status === 'warning') ?? items[items.length - 1])?.id
}

/**
 * What happened to a position, on one line: the moments of the orders that
 * opened it, the sells Saxo did not take, then the moments of the orders that
 * closed it, each marked Entry or Exit.
 */
export function positionTimeline(opening: Order[], closing: Order[], refusals: SellRefusals | null, timeZone: string): TimelineItem[] {
  const ofOrder = (order: Order, tag: string) => timelineItems(orderLife(order).steps, timeZone, { prefix: `${order.order_id}-`, tag })
  return [
    ...opening.flatMap((order) => ofOrder(order, 'Entry')),
    ...(refusals ? timelineItems([sellsRefused(refusals)], timeZone, { tag: 'Exit' }) : []),
    ...closing.flatMap((order) => ofOrder(order, 'Exit')),
  ]
}

/**
 * Why a closed position shows no sell order, when it shows none. Null while it
 * is open, or when a sell order is there to speak for itself.
 */
export function closureWithoutOrder(
  position: { position_status: string | null; position_close_reason: string | null },
  orders: Order[],
): string | null {
  if (position.position_status !== 'Closed') return null
  if (orders.some((order) => purposeOf(order) === 'close')) return null
  // The reason the monitor writes when it learns of a closure from Saxo and not from a sale of its own.
  if (position.position_close_reason === 'SaxoAPI') {
    return 'Closed at Saxo, not by a sell order WATA recorded: a knock-out, a stop resting at Saxo, or a sale made there by hand.'
  }
  if (!orders.some(isTracked)) {
    return 'Its sell order was not recorded: WATA did not keep them when this position was closed.'
  }
  return 'No sell order was recorded for this closure.'
}

export interface EntryFigures {
  /** Entry orders whose moments were kept, in the rows given. */
  tracked: number
  /** Half of the entries that gave a position were seen sooner than this. */
  typicalMs: number | null
  slowestMs: number | null
  slowestOrderId: string | null
  /** Accepted by Saxo, and no position came of them. */
  withoutPosition: number
}

/** How the entries of a period went: what the three figures above the orders say. */
export function entryFigures(orders: Order[]): EntryFigures {
  const entries = orders.filter((order) => isTracked(order) && purposeOf(order) === 'open')
  const seen = entries
    .filter((order) => orderResult(order) === 'opened')
    .map((order) => ({ id: order.order_id, took: tookMs(order) }))
    .filter((entry): entry is { id: string; took: number } => entry.took != null)
    .sort((a, b) => a.took - b.took)
  const middle = Math.floor(seen.length / 2)
  const slowest = seen[seen.length - 1]
  return {
    tracked: entries.length,
    typicalMs: seen.length ? (seen.length % 2 ? seen[middle].took : (seen[middle - 1].took + seen[middle].took) / 2) : null,
    slowestMs: slowest?.took ?? null,
    slowestOrderId: slowest?.id ?? null,
    withoutPosition: entries.filter((order) => ['no_position', 'error'].includes(orderResult(order))).length,
  }
}
