import { describe, expect, it, vi } from 'vitest'

import type { BacktestRunListed, DailyPerformance, Health, JournalEntry, Position, TaRule, TaScriptSummary } from '@/lib/types'

import { attachFooter, attachItems, CATEGORY, LUXALGO_LIST } from './attachItems'

const RUN = {
  id: '20261027-101010-abcdef', name: 'Cross R60', from: '2026-10-20', to: '2026-10-24', tags: ['cross'],
  status: { state: 'done' }, source: { kind: 'journal' }, chart: { asset: { provider: 'capital', key: 'US100' } },
} as unknown as BacktestRunListed
const SCRIPT = { id: '0d4fd1117e2f', name: 'Kernel VWAP', kind: 'indicator', version: 10 } as TaScriptSummary
const RULE = {
  id: '86fc6f91463e', name: 'Kernel on US100', mode: 'shadow', indice: 'us100', asset: { provider: 'capital', key: 'US100' },
  bars: { type: 'range', ticks: 60 },
} as unknown as TaRule
const POSITION = {
  position_id: '5001', action: 'long', instrument_name: 'Turbo long US100 21000', execution_time_open: '2026-10-28T13:31:00Z',
  position_status: 'Closed',
} as Position
const SIGNAL = { signal_uuid: 'c0ffee', action: 'short', decided_at: '2026-10-28T22:40:00Z', outcome: 'refused', reason: 'Outside trading hours', indice: 'us100' } as JournalEntry
const DAY = { day_date: '2026-10-28', trade_count: 3, performance_percent: -1.2 } as DailyPerformance
const HEALTH = {
  checked_at: '2026-10-30T14:01:07Z',
  services: [{ id: 'trader', name: 'Trader', status: 'down', detail: 'No beat for 3 min' }, { id: 'web_server', name: 'Web server', status: 'ok', detail: '' }],
} as unknown as Health

function items(query = '', attached: string[] = [], { pending = [] as string[], room = 5, luxalgo = false } = {}) {
  const onAttach = vi.fn()
  const onDetach = vi.fn()
  const onFile = vi.fn()
  const { items: made, picked } = attachItems({
    lists: { runs: [RUN], scripts: [SCRIPT], rules: [RULE], positions: [POSITION], journal: [SIGNAL], days: [DAY], health: HEALTH },
    attached: new Set([...attached, ...pending]), pending: new Set(pending), room, query, timeZone: 'Europe/Paris', luxalgo,
    onAttach, onDetach, onFile,
  })
  return { made, picked, onAttach, onDetach, onFile }
}

describe('what the attach picker offers', () => {
  it('a file of the computer first, then each kind of thing in its place', () => {
    const { made, onFile } = items()

    expect(made.map((item) => item.category)).toEqual([CATEGORY.file, CATEGORY.run, CATEGORY.script, CATEGORY.rule,
      CATEGORY.position, CATEGORY.signal, CATEGORY.day, CATEGORY.incident])
    made[0].action!()
    expect(onFile).toHaveBeenCalledOnce()
  })

  it('attaches each thing as its page asks the agent about it', () => {
    const { made, onAttach } = items()
    for (const item of made.slice(1)) item.action!()

    expect(onAttach.mock.calls.map(([subject]) => subject)).toEqual([
      { kind: 'run', id: RUN.id, label: 'Cross R60' },
      { kind: 'script', id: SCRIPT.id, version: 10, label: 'Kernel VWAP' },
      { kind: 'rule', id: RULE.id, label: 'Kernel on US100' },
      { kind: 'position', id: '5001', label: 'long Turbo long US100 21000' },
      // Decided at 23:40 in Paris: the journal's day of the trading timezone.
      { kind: 'signal', id: 'c0ffee', day: '2026-10-28', label: 'short signal of 2026-10-28' },
      { kind: 'day', id: '2026-10-28', label: 'the day of 2026-10-28' },
      // The health's entries not OK only, at the minute it was read (UTC).
      { kind: 'incident', id: 'trader', at: '2026-10-30 14:01', label: 'Trader at 2026-10-30 14:01 UTC' },
    ])
  })

  it('leaves out what the conversation holds already', () => {
    const { made, picked } = items('', [`run:${RUN.id}`, `script:${SCRIPT.id}@v10`, 'incident:trader@2026-10-30 14:01'])

    expect(made.map((item) => item.id)).not.toContain(`run-${RUN.id}`)
    expect(made.map((item) => item.id)).not.toContain(`script-${SCRIPT.id}`)
    expect(made.map((item) => item.id)).not.toContain('incident-trader')
    expect(picked).toEqual([])
  })

  it('ticks what this message attaches, and takes it back when it is chosen again', () => {
    const { made, picked, onAttach, onDetach } = items('', [], { pending: [`run:${RUN.id}`, 'day:2026-10-28'] })

    expect(picked).toEqual([`run-${RUN.id}`, 'day-2026-10-28'])
    made.find((item) => item.id === `run-${RUN.id}`)!.action!()
    expect(onDetach).toHaveBeenCalledWith(`run:${RUN.id}`)
    expect(onAttach).not.toHaveBeenCalled()
  })

  it('once full, offers only what this message attaches, to take it back', () => {
    const { made, picked } = items('', [], { pending: [`run:${RUN.id}`], room: 0 })

    expect(made.map((item) => item.id)).toEqual([`run-${RUN.id}`])
    expect(picked).toEqual([`run-${RUN.id}`])
  })

  it('says under the list what is attached, what more may be, and how to finish', () => {
    expect(attachFooter(0, 5)).toBe('5 more possible · Esc when done')
    expect(attachFooter(2, 3)).toBe('2 attached · 3 more possible · Esc when done')
    expect(attachFooter(5, 0)).toBe('5 things attached at most in a conversation · Esc when done')
  })

  it('takes a number typed for a position, and a date for a trading day, listed or not', () => {
    const { made, onAttach } = items('5999999')
    made.find((item) => item.id === 'typed-position-5999999')!.action!()
    const day = items('2026-10-27').made.find((item) => item.id === 'typed-day-2026-10-27')

    expect(onAttach).toHaveBeenCalledWith({ kind: 'position', id: '5999999', label: 'position 5999999' })
    expect(day?.title).toBe('The trading day 27 Oct 2026')
    // The picker keeps only the entries holding every word typed: the date as typed is among the day's.
    expect(day?.keywords).toContain('2026-10-27')
    // A position or a day listed is not offered twice.
    expect(items('5001').made.filter((item) => item.id.endsWith('5001'))).toHaveLength(1)
    expect(items('2026-10-28').made.filter((item) => item.id.endsWith('2026-10-28'))).toHaveLength(1)
  })

  it("offers LuxAlgo's Library as a list of its own, holding the indicators LuxAlgo found", () => {
    const onAttach = vi.fn()
    const { items: made } = attachItems({
      lists: { luxalgo: [{ kind: 'indicator', slug: 'ai-supertrend', name: 'AI Supertrend', family: 'trend', description: 'Clusters.' }] },
      attached: new Set(), pending: new Set(), room: 5, query: '', timeZone: 'Europe/Paris', luxalgo: true,
      onAttach, onDetach: vi.fn(), onFile: vi.fn(),
    })
    // Nothing of LuxAlgo at the top: searched only in its list, so a run's name typed costs no call to LuxAlgo.
    expect(made.map((item) => item.id)).not.toContain('luxalgo-ai-supertrend')
    const list = made.find((item) => item.id === LUXALGO_LIST)!
    expect(list).toMatchObject({ title: 'LuxAlgo’s Library', category: CATEGORY.luxalgo })
    const found = list.items!.find((item) => item.id === 'luxalgo-ai-supertrend')!
    expect(found).toMatchObject({ title: 'AI Supertrend', category: CATEGORY.luxalgo, description: 'Clusters.' })
    found.action!()
    expect(onAttach).toHaveBeenCalledWith({ kind: 'luxalgo', id: 'ai-supertrend', label: "LuxAlgo's AI Supertrend" })
  })

  it("has no LuxAlgo list while the agent has none of LuxAlgo's tools", () => {
    expect(items().made.map((item) => item.id)).not.toContain(LUXALGO_LIST)
    // An empty list while nothing is found: the picker finds the list it shows by its id while its entries are given.
    expect(items('', [], { luxalgo: true }).made.find((item) => item.id === LUXALGO_LIST)?.items).toEqual([])
  })
})
