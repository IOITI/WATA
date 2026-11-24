import { formatWait } from 'ioiti-ui'
import type { BadgeTone, TimelineItem } from 'ioiti-ui'

import { between, inTimeOrder, orderLife, signalSteps, timelineItem } from '@/features/positions/orderLife'
import type { LifeStep } from '@/features/positions/orderLife'
import { answerText } from '@/features/positions/orderRequests'
import type { Order, OrderRequest, OrderSignal, SignalTrace } from '@/lib/types'

/**
 * One signal's way to WATA and what it gave (GET /api/signals/{ref}, src/admin/signal_trace.py), as its page says it:
 * every moment recorded, from the bar's close to the position seen, in the order they happened. Each moment is read on
 * the server's clock (the engine's and the trader's alike) but Saxo's fills and TradingView's alert, which say so.
 */

/** What the trader did with a signal it received (the journal's outcomes). */
export const TRADER_OUTCOMES: Record<string, { label: string; tone: BadgeTone }> = {
  executed: { label: 'Executed', tone: 'success' },
  closed: { label: 'Positions closed', tone: 'success' },
  refused: { label: 'Refused', tone: 'warning' },
  failed: { label: 'Failed', tone: 'danger' },
  ignored: { label: 'Nothing to do', tone: 'neutral' },
}

/** What became of a signal that never reached the trader's journal (live_fate of src/admin/live_check.py). */
const NOT_TRADED: Record<string, { label: string; tone: BadgeTone }> = {
  shadow: { label: 'In shadow: never sent', tone: 'neutral' },
  not_sent: { label: 'Not sent by the engine', tone: 'warning' },
  not_journaled: { label: 'Never reached the trader', tone: 'warning' },
  unknown: { label: 'Unknown', tone: 'neutral' },
}

export function fateBadge(key: string): { label: string; tone: BadgeTone } {
  return TRADER_OUTCOMES[key] ?? NOT_TRADED[key] ?? { label: key, tone: 'neutral' }
}

/** A step of the signal's way, with the timeline's tag of what it belongs to and the prefix that keeps its id unique. */
export interface TraceEntry {
  step: LifeStep
  tag: string
  prefix: string
}

/** The order's own steps, its signal's left out: the trace says those once, for the signal. */
const SIGNAL_KEYS = new Set(['bar', 'raised', 'alert', 'signal'])

function ofOrder(order: Order, tag: string): TraceEntry[] {
  return orderLife(order)
    .steps.filter((step) => !SIGNAL_KEYS.has(step.key))
    .map((step) => ({ step, tag, prefix: `${order.order_id}-` }))
}

/** The attempts Saxo did not take, each a step of its own: what it answered. */
function refusedAttempts(requests: OrderRequest[], tag: string, label: string): TraceEntry[] {
  return requests
    .filter((request) => request.outcome !== 'accepted')
    .map((request) => ({
      step: {
        key: `request-${request.request_id}`,
        label,
        at: request.requested_at,
        clock: 'wata' as const,
        note: answerText(request),
        tone: 'danger' as const,
      },
      tag,
      prefix: '',
    }))
}

/** The trace's signal as an order's: its moments before an order left, as the order timelines say them. */
export function signalOf(trace: SignalTrace): OrderSignal {
  const engine = trace.engine
  return {
    signal_uuid: trace.signal_uuid,
    signal_timestamp: trace.journal?.signal_timestamp ?? null,
    received_timestamp: trace.journal?.received_timestamp ?? null,
    decided_at: trace.journal?.decided_at ?? null,
    alert_timestamp: trace.journal?.alert_timestamp ?? null,
    engine: engine
      ? {
          id: engine.id,
          rule_id: engine.rule_id,
          rule_name: engine.rule_name,
          mode: engine.mode,
          bar_time: engine.bar_time,
          bar_closed_at: engine.bar_closed_at,
          raised_at: engine.raised_at,
          sent_at: engine.sent_at,
          detail: engine.detail,
        }
      : null,
  }
}

/** The engine's own steps after its tick: its rule's run, and the web server's answer coming back. */
function engineSteps(trace: SignalTrace): LifeStep[] {
  const engine = trace.engine
  if (!engine) return []
  const steps: LifeStep[] = []
  const run = trace.run
  if (run?.started_at) {
    steps.push({ key: 'run-start', label: 'Its rule’s run began', at: run.started_at, clock: 'wata', note: '' })
  }
  if (run?.ended_at) {
    const took = between(run.started_at, run.ended_at)
    steps.push({
      key: 'run-end',
      label: 'Run done: the alert raised',
      at: run.ended_at,
      clock: 'wata',
      note: took != null ? `The script took ${formatWait(took)}` : '',
    })
  }
  if (engine.sent_at && engine.outcome !== 'shadow') {
    const attempts = engine.attempts > 1 ? `Taken at attempt ${engine.attempts}: no answer to the ones before. ` : ''
    steps.push({
      key: 'answered',
      label: engine.outcome === 'sent' ? 'The web server’s answer reached the engine' : 'The engine gave up sending it',
      at: engine.sent_at,
      clock: 'wata',
      note: `${attempts}The web server had stored it before answering`.trim(),
      tone: engine.outcome === 'sent' ? undefined : 'danger',
    })
  }
  return steps
}

/** The trader's journal row: its outcome, written once the trade was done for a signal it executed. */
function journalStep(trace: SignalTrace): LifeStep | null {
  const journal = trace.journal
  if (!journal?.decided_at) return null
  const outcome = fateBadge(journal.outcome)
  const executed = journal.outcome === 'executed' || journal.outcome === 'closed'
  return {
    key: 'decided',
    label: executed ? `Journaled by the trader: ${outcome.label.toLowerCase()}` : `The trader’s outcome: ${outcome.label.toLowerCase()}`,
    at: journal.decided_at,
    clock: 'wata',
    note: executed ? 'Written once its orders were done' : (journal.reason ?? ''),
    tone: journal.outcome === 'failed' ? 'danger' : journal.outcome === 'refused' ? 'warning' : undefined,
  }
}

/**
 * Every recorded moment of a signal, in the order they happened: the engine's (bar, tick, run, answer) or TradingView's
 * alert, the web server's reception, the purchase's attempts and order, the sales the trader sent while it handled the
 * signal, and the trader's journal row.
 */
export function traceEntries(trace: SignalTrace): TraceEntry[] {
  const signal = signalOf(trace)
  const source = trace.engine ? 'Engine' : 'Signal'
  const entries: TraceEntry[] = [
    ...signalSteps(signal).map((step) => ({ step, tag: step.key === 'signal' ? 'Web server' : source, prefix: '' })),
    ...engineSteps(trace).map((step) => ({ step, tag: 'Engine', prefix: '' })),
  ]
  const ordered = new Set(trace.orders.flatMap((order) => (order.requests ?? []).map((request) => request.request_id)))
  entries.push(
    ...refusedAttempts(
      trace.requests.filter((request) => !ordered.has(request.request_id)),
      'Entry',
      'Purchase refused by Saxo',
    ),
  )
  for (const order of trace.orders) entries.push(...ofOrder(order, 'Entry'))
  for (const sale of trace.sold_for_it) {
    const tag = sale.position_id ? `Sale of ${sale.position_id}` : 'Sale'
    entries.push(...refusedAttempts(sale.requests, tag, 'Sale refused by Saxo'))
    if (sale.order) entries.push(...ofOrder(sale.order, tag))
  }
  // Last of what shares its moment: the trader writes it once its orders are done.
  const journal = journalStep(trace)
  if (journal) entries.push({ step: journal, tag: 'Trader', prefix: '' })
  return inTimeOrder(entries, (entry) => entry.step)
}

/** The moment the trace is counted from: the bar's close of an engine signal, else its reception by the web server. */
export function traceOrigin(trace: SignalTrace): { at: string | null; short: string } {
  const bar = trace.engine?.bar_closed_at ?? null
  if (bar) return { at: bar, short: 'from the bar' }
  return { at: trace.journal?.received_timestamp ?? null, short: 'from its reception' }
}

function signedWait(ms: number): string {
  return ms >= 0 ? `+${formatWait(ms)}` : `−${formatWait(-ms)}`
}

/**
 * The timeline of a signal: each step at its moment and its note. A moment of the server's clock also says, on its
 * line, how long after the trace's origin and after the step before on that clock it came: "11:10:47.305 · +1.2 s
 * from the bar · +1.2 s after the step before".
 */
export function traceTimeline(trace: SignalTrace, timeZone: string): TimelineItem[] {
  const origin = traceOrigin(trace)
  let before: string | null = null
  return traceEntries(trace).map(({ step, tag, prefix }) => {
    const item = timelineItem(step, timeZone, prefix, tag)
    if (step.clock !== 'wata' || !step.at) return item
    const said = [item.timestamp]
    const fromOrigin = between(origin.at, step.at)
    if (fromOrigin != null && before != null) said.push(`${signedWait(fromOrigin)} ${origin.short}`)
    const fromBefore = between(before, step.at)
    if (fromBefore != null) said.push(`${signedWait(fromBefore)} after the step before`)
    before = step.at
    return { ...item, timestamp: said.join(' · ') }
  })
}

export interface TraceFigure {
  key: string
  label: string
  ms: number | null
  hint: string
}

/** The figures at the top of its page: how long each stretch of its way took, on the server's clock. */
export function traceFigures(trace: SignalTrace): TraceFigure[] {
  const origin = traceOrigin(trace)
  const received = trace.journal?.received_timestamp ?? null
  const buy = trace.orders[0] ?? null
  const fromBar = Boolean(trace.engine?.bar_closed_at)
  const figures: TraceFigure[] = []
  if (fromBar) {
    figures.push({
      key: 'to_received',
      label: 'Bar close → received',
      ms: between(origin.at, received),
      hint: 'The engine’s tick, its run and the web server’s reception',
    })
  }
  figures.push(
    {
      key: 'to_order',
      label: 'Received → order left',
      ms: between(received, buy?.sent_at),
      hint: 'The trader: its checks, the other side sold, the turbo chosen and sized (not recorded apart)',
    },
    {
      key: 'to_accepted',
      label: 'Order left → Saxo accepted',
      ms: between(buy?.sent_at, buy?.accepted_at),
      hint: 'Saxo’s answer to the order',
    },
    {
      key: 'to_position',
      label: fromBar ? 'Bar close → position seen' : 'Received → position seen',
      ms: between(origin.at, buy?.confirmed_at),
      hint: 'The whole way, until WATA saw the position at Saxo',
    },
  )
  return figures
}

/** "Rule “Kernel”", or what the journal cannot tell apart: TradingView's alerts, the scheduler's and the admin's signals. */
export function sourceSaid(trace: SignalTrace): string {
  if (trace.source.kind === 'engine') {
    const mode = trace.source.mode === 'shadow' ? ' (in shadow)' : ''
    return `Rule “${trace.source.rule_name ?? trace.source.rule_id}”${mode}`
  }
  return 'TradingView, the scheduler or by hand'
}

/** The page's name in the trail and the tab: its action and moment. */
export function traceName(trace: SignalTrace, formatMoment: (value: string | null) => string): string {
  const at = trace.engine?.bar_closed_at ?? trace.journal?.received_timestamp ?? null
  const action = trace.action ?? 'Signal'
  return `${action.charAt(0).toUpperCase()}${action.slice(1)} at ${formatMoment(at)}`
}

/** How long after it was raised a signal's page reads it again while it is on its way. */
const UNDER_WAY_MS = 10 * 60_000

/**
 * True while a recent signal is still on its way: sent and not journaled yet (the trader journals it once its orders
 * are done), or an order of it without its outcome.
 */
export function stillUnderWay(trace: SignalTrace, now: number): boolean {
  const at = Date.parse(trace.engine?.raised_at ?? trace.journal?.received_timestamp ?? '')
  if (!Number.isFinite(at) || now - at > UNDER_WAY_MS) return false
  if (!trace.journal) return trace.engine?.outcome === 'sending' || trace.engine?.outcome === 'sent'
  return trace.orders.some((order) => !order.outcome)
}
