import type { Check } from 'ioiti-ui'
import { describe, expect, it } from 'vitest'

import type { TaRuleEvent, TaSignal } from '@/lib/types'

import { changeSaid, eventSaid, firstDay, historyItems, liveSignalsSaid, openingDay, readinessSaid, ruleLayer, signalFate, steps } from './rulePageModel'

const PARIS = 'Europe/Paris'
const ms = (iso: string) => Date.parse(iso)

function signal(changes: Partial<TaSignal> = {}): TaSignal {
  return {
    id: 1, rule_id: 'aaaaaaaaaaaa', rule_name: 'Cross', script_id: 'cccccccccccc', script_version: 1, asset: 'capital/US100', side: 'bid',
    bars: '1', bar_time: '2026-10-22T08:31:00Z', bar_closed_at: '2026-10-22T08:32:00Z', alert_key: 'alertcondition:Up', alert_title: 'Up',
    alert_message: null, action: 'long', indice: 'us100', confidence: null, mode: 'shadow', raised_at: '2026-10-22T08:32:01Z',
    outcome: 'shadow', sent_at: null, http_status: null, signal_uuid: null, detail: null, trader: null, ...changes,
  }
}

describe("a rule's days", () => {
  it('begin the day it was made, or the weekday after a weekend', () => {
    expect(firstDay({ created_at: '2026-10-19T10:00:00Z' }, PARIS)).toBe('2026-10-19')
    expect(firstDay({ created_at: '2026-10-24T10:00:00Z' }, PARIS)).toBe('2026-10-26')
    // 23:30 in UTC is past midnight in Paris: its day is Paris's.
    expect(firstDay({ created_at: '2026-10-22T23:30:00Z' }, PARIS)).toBe('2026-10-23')
  })

  it('open on today, or the weekday before a weekend, and on none before the first', () => {
    const made = { created_at: '2026-10-19T10:00:00Z' }
    expect(openingDay(made, ms('2026-10-29T12:00:00Z'), PARIS)).toBe('2026-10-29')
    expect(openingDay(made, ms('2026-10-31T12:00:00Z'), PARIS)).toBe('2026-10-30')
    // Made on a Saturday, seen that Saturday: its first day is Monday.
    expect(openingDay({ created_at: '2026-10-31T09:00:00Z' }, ms('2026-10-31T12:00:00Z'), PARIS)).toBeNull()
  })

  it('step over weekends, never before the first nor after the last', () => {
    expect(steps('2026-10-26', '2026-10-19', '2026-10-30')).toEqual({ before: '2026-10-23', after: '2026-10-27' })
    expect(steps('2026-10-19', '2026-10-19', '2026-10-30').before).toBeNull()
    expect(steps('2026-10-30', '2026-10-19', '2026-10-30').after).toBeNull()
  })
})

describe("a rule's signals over a day", () => {
  it('say what became of each, a signal the trader did not take marked', () => {
    expect(signalFate(signal())).toEqual({ said: 'journaled in shadow, never sent', tone: 'neutral', missed: false })
    expect(signalFate(signal({ outcome: 'refused', detail: 'The rule is not live' }))).toEqual(
      { said: 'not sent: The rule is not live', tone: 'warning', missed: true })
    const sent = { outcome: 'sent' as const, mode: 'live' as const }
    expect(signalFate(signal({ ...sent, trader: { decided_at: '', outcome: 'executed', reason: null, position_id: '5001' } })).tone).toBe('entry')
    expect(signalFate(signal({ ...sent, trader: { decided_at: '', outcome: 'refused', reason: 'Max trades per day reached', position_id: null } })))
      .toEqual({ said: 'sent, refused by the trader: Max trades per day reached', tone: 'warning', missed: true })
    expect(signalFate(signal({ ...sent, trader: null })).said).toBe('sent, the trader not decided yet')
  })

  it('are drawn at their bars as the second layer, the days of history before the day shaded', () => {
    // Minute bars from 08:00 UTC, the day starting at 08:30 (its warm-up before).
    const rows = Array.from({ length: 60 }, (_, index) => {
      const open = ms('2026-10-22T08:00:00Z') + index * 60_000
      return [open, 1, 2, 0.5, 1.5, 3, open + 60_000] as [number, number, number, number, number, number, number]
    })
    const colors = { entry: '#0a0', profit: '#0b0', loss: '#c00', neutral: '#888', warning: '#c80', warmUp: '#eee' }
    const short = signal({ id: 2, action: 'short', bar_time: '2026-10-22T08:20:00Z', bar_closed_at: '2026-10-22T08:21:00Z', outcome: 'sent',
      trader: { decided_at: '', outcome: 'refused', reason: 'Max trades per day reached', position_id: null } })
    const layer = ruleLayer([signal(), short])(rows, colors, ms('2026-10-22T08:30:00Z'))

    expect([layer.title, layer.short]).toEqual(['The rule: its signals', 'The rule'])
    expect(layer.trades).toEqual([])
    // In the order of their bars: the short first.
    expect(layer.labels.map((label) => [label.text, label.style, label.yloc, label.color])).toEqual([
      ['Short ✕', 'arrowdown', 'abovebar', '#c80'],
      ['Long', 'arrowup', 'belowbar', '#888'],
    ])
    expect(layer.labels[1].tooltip).toBe('#1 Long (Up), journaled in shadow, never sent')
    expect(layer.backgrounds).toHaveLength(1)
  })

  it("say a live rule's last days in a sentence", () => {
    const sent = { outcome: 'sent' as const, mode: 'live' as const }
    expect(liveSignalsSaid([], 7)).toBe('No signal in the last 7 days.')
    expect(liveSignalsSaid([
      signal({ ...sent, trader: { decided_at: '', outcome: 'executed', reason: null, position_id: '5001' } }),
      signal({ ...sent, trader: { decided_at: '', outcome: 'closed', reason: null, position_id: null } }),
      signal({ ...sent, trader: { decided_at: '', outcome: 'refused', reason: 'Paused', position_id: null } }),
      signal({ outcome: 'refused', detail: 'Too old' }),
    ], 7)).toBe('In the last 7 days: 3 signals sent, 1 bought by the trader, 1 closing positions, 2 not taken.')
  })
})

describe("a rule's readiness", () => {
  const check = (id: string, state: Check['state']): Check => ({ id, label: id, state, detail: '' })

  it('is said by what the server would hold it back for', () => {
    expect(readinessSaid([check('shadow', 'pass'), check('signals', 'pass'), check('script', 'open')]))
      .toEqual({ text: 'Ready to go live', ready: true })
    expect(readinessSaid([check('shadow', 'open'), check('signals', 'fail'), check('paper', 'open')]))
      .toEqual({ text: '2 to go before live', ready: false })
  })
})

describe("a rule's history", () => {
  const cross = (version: number) => ({ id: 'cccccccccccc', name: 'Cross', version })

  it('says each change of what it runs, its inputs by their names', () => {
    expect(changeSaid({ what: 'script', before: cross(1), after: cross(2) })).toBe('Its script: Cross v1 → Cross v2.')
    expect(changeSaid({ what: 'bars', before: { type: 'time', timeframe: '1' }, after: { type: 'range', ticks: 60 } }))
      .toBe('Its bars: 1 min → Range 60.')
    expect(changeSaid({ what: 'history_days', before: null, after: 0 })).toBe('Its days of history: the engine’s → none.')
    expect(changeSaid({ what: 'input', id: 'in_0', name: 'Fast', before: 12, after: 20 })).toBe('Fast: 12 → 20.')
    expect(changeSaid({ what: 'input', id: 'in_2', name: 'slow', before: 30, after: null })).toBe('slow: 30, no longer set.')
    expect(changeSaid({ what: 'libraries', before: [], after: [{ name: 'Maths', version: 2 }] })).toBe('Its libraries: none → Maths v2.')
  })

  it('says each event, a switch into live short of its readiness marked', () => {
    const events: TaRuleEvent[] = [
      { at: '2026-10-28T09:00:00Z', kind: 'changed_live', fields: ['name', 'alerts'] },
      { at: '2026-10-27T09:00:00Z', kind: 'left_live', to: 'off', proposed: null },
      { at: '2026-10-26T09:00:00Z', kind: 'live', unmet: ['no signal journaled yet'], licence: 'CC BY-NC-SA 4.0', proposed: 'Take it live' },
      { at: '2026-10-25T09:00:00Z', kind: 'changed', live: true, also: ['name'],
        changes: [{ what: 'side', before: 'bid', after: 'ask' }] },
      { at: '2026-10-19T10:00:00Z', kind: 'made', by: 'claude', runs: {
        script: cross(1), libraries: [], asset: { provider: 'capital', key: 'US100' }, side: 'bid',
        bars: { type: 'time', timeframe: '1' }, history_days: null, inputs: 1 } },
      { at: '2026-10-01T08:00:00Z', kind: 'made', by: null, runs: null },
    ]

    expect(events.map((event) => [eventSaid(event).title, eventSaid(event).details, eventSaid(event).status])).toEqual([
      ['Changed while live', ['Its name and alerts: nothing it runs.'], 'warning'],
      ['Out of live, switched off', [], 'completed'],
      ['Went live without its readiness', [
        'Still missing: no signal journaled yet.', 'The licence of its script (CC BY-NC-SA 4.0) checked.',
        'Proposed by the AI agent: “Take it live”.'], 'warning'],
      ['Changed while live', ['Its side: bid → ask.', 'Its name too.'], 'warning'],
      ['Made by Claude', ['Cross v1 on capital/US100 bid, 1 min, 1 input set.'], 'completed'],
      ['Made', ['What it ran then was not kept: it was made before WATA kept what each rule runs.'], 'completed'],
    ])
    const items = historyItems(events.slice(1, 2), PARIS)
    expect(items).toEqual([{ id: 'left_live-0', title: 'Out of live, switched off', timestamp: expect.stringContaining('27 Oct'), status: 'completed' }])
  })
})
