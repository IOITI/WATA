import { describe, expect, it } from 'vitest'

import type { Order, SignalTrace } from '@/lib/types'
import { BUY, TRACE } from '@/test/signalTrace'

import { fateBadge, sourceSaid, stillUnderWay, traceEntries, traceFigures, traceName, traceTimeline } from './signalTrace'

describe('a signal of the engine that bought', () => {
  it('runs from its bar to its position in the order things happened, each step marked with what it is of', () => {
    expect(traceEntries(TRACE).map(({ step, tag }) => [tag, step.label])).toEqual([
      ['Engine', 'Bar closed'],
      ['Engine', 'Raised by the engine'],
      ['Engine', 'Its rule’s run began'],
      ['Engine', 'Run done: the alert raised'],
      ['Web server', 'Signal received'],
      ['Engine', 'The web server’s answer reached the engine'],
      ['Sale of 4001', 'Sell order sent'],
      ['Sale of 4001', 'Closed at Saxo'],
      ['Sale of 4001', 'Accepted by Saxo'],
      ['Entry', 'Purchase refused by Saxo'],
      ['Sale of 4001', 'Seen closed by WATA'],
      ['Entry', 'Order sent'],
      ['Entry', 'Filled at Saxo'],
      ['Entry', 'Accepted by Saxo'],
      ['Entry', 'Seen by WATA'],
      ['Trader', 'Journaled by the trader: executed'],
    ])
  })

  it('says the signal once: the order of its purchase does not repeat its moments', () => {
    const labels = traceEntries(TRACE).map(({ step }) => step.label)
    expect(labels.filter((label) => label === 'Signal received')).toHaveLength(1)
    expect(labels.filter((label) => label === 'Bar closed')).toHaveLength(1)
  })

  it('says why Saxo refused an attempt, and the attempt the web server took', () => {
    const steps = traceEntries(TRACE).map(({ step }) => step)
    expect(steps.find((step) => step.label === 'Purchase refused by Saxo')?.note).toBe('“Not enough cash” (InsufficientFunds, HTTP 400)')
    expect(steps.find((step) => step.key === 'answered')?.note).toBe(
      'Taken at attempt 2: no answer to the ones before. The web server had stored it before answering',
    )
    expect(steps.find((step) => step.key === 'run-end')?.note).toBe('The script took 190 ms')
  })

  it('counts each moment of the server from the bar’s close and from the step before, never Saxo’s', () => {
    const items = traceTimeline(TRACE, 'UTC')
    expect(items[0].timestamp).toBe('14:31:00.000')
    expect(items[1].timestamp).toBe('14:31:00.420 · +420 ms from the bar · +420 ms after the step before')
    expect(items.find((item) => item.title === 'Filled at Saxo')?.timestamp).toBe('14:31:03.270')
    expect(items.find((item) => item.title === 'Seen by WATA')?.timestamp).toBe(
      '14:31:04.920 · +4.9 s from the bar · +1.6 s after the step before',
    )
    expect(new Set(items.map((item) => item.id)).size).toBe(items.length)
  })

  it('gives each stretch of its way', () => {
    expect(traceFigures(TRACE).map(({ label, ms }) => [label, ms])).toEqual([
      ['Bar close → received', 1070],
      ['Received → order left', 1950],
      ['Order left → Saxo accepted', 260],
      ['Bar close → position seen', 4920],
    ])
  })

  it('is named by its rule, its action and its bar', () => {
    expect(sourceSaid(TRACE)).toBe('Rule “Kernel”')
    expect(sourceSaid({ ...TRACE, source: { ...TRACE.source, mode: 'shadow' } })).toBe('Rule “Kernel” (in shadow)')
    expect(traceName(TRACE, (value) => value ?? '?')).toBe('Long at 2026-11-23T14:31:00Z')
  })
})

describe('a signal of TradingView, or sent by hand', () => {
  const received: SignalTrace = {
    ...TRACE,
    source: { kind: 'received', label: 'TradingView or by hand', rule_id: null, rule_name: null, mode: null },
    engine: null,
    run: null,
    journal: { ...TRACE.journal!, alert_timestamp: '2026-11-23T14:30:59Z' },
    sold_for_it: [],
    requests: [],
  }

  it('starts at its alert, on TradingView’s clock to the second, then its reception', () => {
    const entries = traceEntries(received)
    expect(entries.slice(0, 2).map(({ step }) => [step.label, step.clock, step.toTheSecond ?? false])).toEqual([
      ['Alert fired', 'tradingview', true],
      ['Signal received', 'wata', false],
    ])
    expect(sourceSaid(received)).toBe('TradingView, the scheduler or by hand')
  })

  it('counts from its reception', () => {
    const items = traceTimeline(received, 'UTC')
    expect(items[0].timestamp).toBe('14:30:59')
    expect(items[1].timestamp).toBe('14:31:01.070')
    expect(items[2].timestamp).toBe('14:31:03.020 · +1.9 s from its reception · +1.9 s after the step before')
    expect(traceFigures(received).map(({ label }) => label)).toEqual([
      'Received → order left',
      'Order left → Saxo accepted',
      'Received → position seen',
    ])
  })
})

describe('a signal that never reached the trader', () => {
  it('has the engine’s steps alone, and says what became of it', () => {
    const lost: SignalTrace = {
      ...TRACE,
      ref: 'engine-9',
      signal_uuid: null,
      fate: { key: 'not_sent', reason: 'No answer' },
      engine: { ...TRACE.engine!, outcome: 'failed', attempts: 1 },
      journal: null,
      orders: [],
      requests: [],
      sold_for_it: [],
    }
    const steps = traceEntries(lost).map(({ step }) => step)
    expect(steps.map((step) => step.label)).toEqual([
      'Bar closed',
      'Raised by the engine',
      'Its rule’s run began',
      'Run done: the alert raised',
      'The engine gave up sending it',
    ])
    expect(steps[4].tone).toBe('danger')
    expect(fateBadge('not_sent')).toEqual({ label: 'Not sent by the engine', tone: 'warning' })
    expect(fateBadge('executed').tone).toBe('success')
    expect(fateBadge('something new')).toEqual({ label: 'something new', tone: 'neutral' })
  })
})

describe('a signal on its way', () => {
  const raised = Date.parse('2026-11-23T14:31:00.420Z')

  it('is read again while the trader has not journaled it, or an order of it is not settled', () => {
    expect(stillUnderWay({ ...TRACE, journal: null }, raised + 5_000)).toBe(true)
    expect(stillUnderWay({ ...TRACE, orders: [{ ...BUY, outcome: null } as unknown as Order] }, raised + 5_000)).toBe(true)
  })

  it('is not once settled, or never sent, or older than ten minutes', () => {
    expect(stillUnderWay(TRACE, raised + 5_000)).toBe(false)
    expect(stillUnderWay({ ...TRACE, journal: null, engine: { ...TRACE.engine!, outcome: 'refused' } }, raised + 5_000)).toBe(false)
    expect(stillUnderWay({ ...TRACE, journal: null }, raised + 11 * 60_000)).toBe(false)
  })
})
