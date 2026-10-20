import { encodeState } from '@luxalgo/vela/workspace'
import { describe, expect, it, vi } from 'vitest'

import type { TaScript, TaScriptSummary } from '@/lib/types'

import { layoutFor, layoutStorage, scriptEntries } from './workspace'

const summary = (id: string, name: string, kind: TaScriptSummary['kind'], source: string) =>
  ({ id, name, kind, source }) as unknown as TaScript

const LIBRARY = '//@version=6\nlibrary("Helpers")\nexport double(float value) =>\n    value * 2\n'
const SCRIPTS = [
  summary('aaaaaaaaaaaa', 'Doubled', 'indicator', '//@version=6\nindicator("D")\nimport me/Helpers/1 as h\nplot(h.double(close))\n'),
  summary('bbbbbbbbbbbb', 'Helpers', 'library', LIBRARY),
  summary('cccccccccccc', 'Cross', 'strategy', '//@version=6\nstrategy("C")\nplot(close)\n'),
  summary('dddddddddddd', 'Orphan', 'indicator', '//@version=6\nindicator("O")\nimport me/Missing/1 as m\nplot(close)\n'),
]

describe('the indicator list of the charts', () => {
  it('holds the indicators and strategies, prepared, by the names the scripts have; libraries are inlined, not listed', async () => {
    const { entries, skipped } = await scriptEntries(
      async (name) => (name === 'Helpers' ? { id: 'b', name, version: 1, source: LIBRARY } : null),
      {
        list: async () => ({ items: SCRIPTS }),
        read: async (id) => SCRIPTS.find((script) => script.id === id) as TaScript,
      },
    )

    expect(entries.map(({ id, entry }) => [id, entry.name, entry.category, entry.enabled])).toEqual([
      ['aaaaaaaaaaaa', 'Doubled', 'WATA indicators', false],
      ['cccccccccccc', 'Cross', 'WATA strategies', false],
    ])
    expect(entries[0].entry.script).toContain('plot(Helpers__double(close))')
    expect(skipped).toEqual([{ name: 'Orphan', reason: expect.stringContaining('No library "Missing"') }])
  })
})

describe('the saved layout', () => {
  const saved = encodeState({
    version: 1,
    layout: '1',
    charts: [{ id: 'c1', symbol: 'capital:CAPITAL_US100', timeframe: '5', indicators: { manifest: ['Cross'], natives: [] } }],
  })

  it('keeps what it holds, with the symbol and timeframe of the address over its chart', () => {
    const state = JSON.parse(layoutFor(saved, { symbol: 'dukascopy:DUKASCOPY_US100.R60', timeframe: '1' }) as string)

    expect(state.charts[0]).toMatchObject({ symbol: 'dukascopy:DUKASCOPY_US100.R60', timeframe: '1' })
    expect(state.charts[0].indicators.manifest).toEqual(['Cross'])
    expect(JSON.parse(layoutFor(saved, {}) as string).charts[0]).toMatchObject({ symbol: 'capital:CAPITAL_US100', timeframe: '5' })
    expect(layoutFor(null, { symbol: 'x' })).toBeNull()
    expect(layoutFor('not json', {})).toBeNull()
  })

  it('is answered at once to Vela, and each change is sent to the admin', async () => {
    const save = vi.fn(async () => undefined)
    const storage = layoutStorage(saved, save)

    expect(storage.get('charts')).toBe(saved)
    expect(storage.get('other')).toBeNull()
    await storage.set('charts', '{"version":1}')
    expect(save).toHaveBeenCalledWith('charts', '{"version":1}')
  })
})
