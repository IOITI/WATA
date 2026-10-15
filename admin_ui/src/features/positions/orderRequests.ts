import { formatNumber, formatTime, formatWait } from '@/lib/format'
import type { Order, OrderRequest, Refusal, RequestAnswer, RequestOutcome, SellRefusals } from '@/lib/types'

/**
 * What the pages say of the requests sent to Saxo's order endpoint, and of the
 * runs of attempts Saxo did not take (grouped by src/admin/order_requests.py).
 * Saxo's message is shown as Saxo wrote it, in the account's language.
 */

export type RequestTone = 'neutral' | 'success' | 'warning' | 'danger'

export const REQUEST_RESULTS: Record<RequestOutcome, { label: string; tone: RequestTone }> = {
  accepted: { label: 'Accepted', tone: 'success' },
  refused: { label: 'Refused by Saxo', tone: 'danger' },
  no_answer: { label: 'No answer from Saxo', tone: 'warning' },
  error: { label: 'Error', tone: 'danger' },
}

/** A row of the Orders page: an order Saxo took, or a run of attempts it did not. */
export type OrderRow = (Order & { kind: 'order' }) | (Refusal & { kind: 'refusal' })

const NO_ANSWER_ADVICE = 'Saxo may have taken the order all the same: check the account at Saxo for a position WATA does not know.'

function between(from: string | null | undefined, to: string | null | undefined): number | null {
  if (!from || !to) return null
  const span = Date.parse(to) - Date.parse(from)
  return Number.isFinite(span) ? span : null
}

function times(count: number): string {
  return count === 1 ? 'once' : count === 2 ? 'twice' : `${formatNumber(count)} times`
}

/** Saxo's code and the HTTP status, as they follow its message: "OtherError, HTTP 400". */
function tags(answer: Pick<RequestAnswer, 'http_status' | 'error_code'>): string {
  return [answer.error_code, answer.http_status != null ? `HTTP ${answer.http_status}` : null].filter(Boolean).join(', ')
}

/** One of Saxo's answers in a few words: its message in quotes, then its code and status. */
export function answerText(answer: Pick<RequestAnswer, 'outcome' | 'http_status' | 'error_code' | 'error_message'>): string {
  const labels = tags(answer)
  if (answer.outcome === 'refused') {
    if (answer.error_message) return `“${answer.error_message}”${labels ? ` (${labels})` : ''}`
    return labels ? `No message (${labels})` : 'No message'
  }
  return answer.error_message ?? REQUEST_RESULTS[answer.outcome ?? 'error'].label
}

/** What Saxo answered to one request, in a line. */
export function saxoAnswer(request: OrderRequest): string {
  if (request.outcome === 'accepted') {
    if (request.operation === 'cancel') return 'Cancellation accepted'
    return request.order_id ? `Accepted: order ${request.order_id}` : 'Accepted'
  }
  if (request.outcome === 'no_answer') return `${answerText(request)}. ${NO_ANSWER_ADVICE}`
  return answerText(request)
}

/** What a request asked of Saxo: "Sell 6 · Market", "Cancel order 5020", "Protective stop: Sell 98 · StopIfTraded". */
export function requestLabel(request: OrderRequest): string {
  if (request.operation === 'cancel') {
    const orders = (request.order_id ?? '')
      .split(',')
      .map((order) => order.trim())
      .filter(Boolean)
    if (orders.length === 0) return 'Cancel'
    return `Cancel order${orders.length === 1 ? '' : 's'} ${orders.join(', ')}`
  }
  const side = request.buy_sell ?? (request.purpose === 'open' ? 'Buy' : 'Sell')
  const order = [`${side} ${formatNumber(request.amount)}`, request.order_type].filter(Boolean).join(' · ')
  return request.purpose === 'stop' ? `Protective stop: ${order}` : order
}

/** From the request leaving to Saxo's answer (or the failure), on the server's clock. */
export function requestTookMs(request: OrderRequest): number | null {
  return between(request.requested_at, request.answered_at)
}

/** A run as the Result column says it: "Refused by Saxo ×37". */
export function runLabel(run: Pick<Refusal, 'outcome' | 'count'>): string {
  const label = REQUEST_RESULTS[run.outcome].label
  return run.count > 1 ? `${label} ×${formatNumber(run.count)}` : label
}

/** From the first attempt leaving to the last answer: how long Saxo did not take the order. */
export function runTookMs(run: Refusal): number | null {
  return between(run.first_at, run.answered_at ?? run.last_at)
}

function intentText(run: Refusal): string {
  const amount = formatNumber(run.order_amount)
  const instrument = run.instrument_name ?? (run.purpose === 'open' ? 'the turbo' : 'the position')
  if (run.purpose === 'stop') return 'place the protective stop of the position'
  return `${run.purpose === 'open' ? 'buy' : 'sell'} ${amount} of ${instrument}`
}

/** What happened to a run, in a few sentences. */
export function refusalSummary(run: Refusal, timeZone: string): string {
  const span = runTookMs(run)
  const often = run.count > 1 ? ` ${times(run.count)}${span != null && span > 0 ? ` in ${formatWait(span)}` : ''}` : ''
  const sentences: string[] = []
  const [only] = run.answers.length === 1 ? run.answers : []
  if (run.outcome === 'refused') {
    sentences.push(
      only
        ? `Saxo refused to ${intentText(run)}${often}, answering ${answerText(only)}.`
        : `Saxo refused to ${intentText(run)}${often}, with ${run.answers.length} different answers.`,
    )
  } else if (run.outcome === 'no_answer') {
    sentences.push(`WATA got no answer from Saxo when it tried to ${intentText(run)}${often}. ${NO_ANSWER_ADVICE}`)
  } else {
    sentences.push(`WATA could not ${intentText(run)}${often}${only ? `: ${answerText(only)}` : ''}.`)
  }

  if (run.purpose === 'open') {
    sentences.push('No position was opened.')
  } else if (run.followed_by?.order_id) {
    sentences.push(
      `${run.purpose === 'stop' ? 'Saxo took it' : 'The position was sold'} by order ${run.followed_by.order_id} at ${formatTime(run.followed_by.requested_at, timeZone)}.`,
    )
  } else if (run.position?.position_status === 'Closed') {
    sentences.push('The position was closed later, by Saxo or by a sale WATA did not record.')
  } else if (run.purpose === 'close') {
    sentences.push('The position is still open.')
  }
  return sentences.join(' ')
}

/** The orders and the runs of a period as the rows of one table, newest first. */
export function orderRows(orders: Order[], refusals: Refusal[] = []): OrderRow[] {
  const rows: OrderRow[] = [
    ...orders.map((order) => ({ ...order, kind: 'order' as const })),
    ...refusals.map((run) => ({ ...run, kind: 'refusal' as const })),
  ]
  const time = (row: OrderRow) => Date.parse(rowTime(row) ?? '') || 0
  return rows.sort((a, b) => time(b) - time(a))
}

/** When a row happened: an order when it left (or was written down, before that was kept), a run at its first attempt. */
export function rowTime(row: OrderRow): string | null {
  return row.kind === 'order' ? (row.sent_at ?? row.order_time) : row.first_at
}

export function rowId(row: OrderRow): string {
  return row.kind === 'order' ? row.order_id : `refused:${row.run_id}`
}

/** The run Saxo did not take just before this order went through: the refusals of its position. */
export function refusalBefore(order: Order, refusals: Refusal[] = []): Refusal | null {
  return refusals.find((run) => run.followed_by?.order_id === order.order_id) ?? null
}

export interface RefusalFigures {
  /** Attempts Saxo did not take, in the period. */
  attempts: number
  runs: number
  latest: Refusal | null
}

export function refusalFigures(refusals: Refusal[] = []): RefusalFigures {
  const latest = [...refusals].sort((a, b) => Date.parse(b.last_at) - Date.parse(a.last_at))[0] ?? null
  return {
    attempts: refusals.reduce((total, run) => total + run.count, 0),
    runs: refusals.length,
    latest,
  }
}

/** What the sells of a position Saxo did not take add up to, from its requests. Null when there were none. */
export function sellRefusalsOf(requests: OrderRequest[] = []): SellRefusals | null {
  const refused = requests.filter(
    (request) => request.operation === 'place' && request.purpose === 'close' && request.outcome !== 'accepted',
  )
  if (refused.length === 0) return null
  const last = refused[refused.length - 1]
  return {
    count: refused.length,
    first_at: refused[0].requested_at,
    last_at: last.requested_at,
    outcome: last.outcome,
    http_status: last.http_status,
    error_code: last.error_code,
    error_message: last.error_message,
  }
}

/** The line under an open position's name: "Sell refused ×37 · 14:04:30". */
export function sellRefusalMarker(refusals: SellRefusals, timeZone: string): string {
  const what = refusals.outcome === 'refused' ? 'Sell refused' : refusals.outcome === 'no_answer' ? 'Sell not answered' : 'Sell failed'
  const count = refusals.count > 1 ? ` ×${formatNumber(refusals.count)}` : ''
  return `${what}${count} · ${formatTime(refusals.last_at, timeZone)}`
}

/** What a position's details say first while Saxo does not take its sells. */
export function sellRefusalSentence(refusals: SellRefusals, timeZone: string, stillOpen: boolean): string {
  const since =
    refusals.count > 1
      ? ` ${times(refusals.count)}, from ${formatTime(refusals.first_at, timeZone)} to ${formatTime(refusals.last_at, timeZone)}`
      : ` at ${formatTime(refusals.last_at, timeZone)}`
  const answer = answerText({ ...refusals, outcome: refusals.outcome ?? 'error' })
  const did = refusals.outcome === 'refused' ? 'Saxo refused to sell this position' : 'WATA could not get Saxo to sell this position'
  const after = stillOpen ? ' It is still open.' : ''
  const advice = refusals.outcome === 'no_answer' ? ` ${NO_ANSWER_ADVICE}` : ''
  return `${did}${since}. Last answer: ${answer}.${after}${advice}`
}
