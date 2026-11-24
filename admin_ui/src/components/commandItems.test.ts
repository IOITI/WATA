import { describe, expect, it } from 'vitest'

import type { DataAsset, TaRule } from '@/lib/types'
import { run } from '@/test/backtests'

import { assetItems, idItems, listItems, pageItems, paletteItems, ruleItems, runItems } from './commandItems'
import { MENU } from './navigation'

describe('what the palette goes to', () => {
  it('has every page of the menu, and the pages that make something new: places only', () => {
    const items = pageItems()

    expect(items.filter((item) => item.id.startsWith('page-'))).toHaveLength(MENU.length)
    expect(items.find((item) => item.title === 'Backtests')).toMatchObject({ category: 'Analysis', href: '/backtests' })
    expect(items.find((item) => item.title === 'New backtest')?.href).toBe('/backtests/new')
    // Nothing that trades or changes anything: each entry is a place, none an action.
    expect(items.every((item) => item.href !== undefined && item.action === undefined)).toBe(true)
  })

  it('leads a run to its page and says what it ran', () => {
    const [item] = runItems([run()])

    expect(item.href).toBe(`/backtests/${run().id}`)
    expect(item.description).toBe('EMA cross v1 · Capital.com US100 · 1 min · 14 Oct 2026 to 16 Oct 2026 · Done')
  })

  it('leads a signal rule to its own page', () => {
    const rule = {
      id: 'aaaaaaaaaaaa', name: 'Cross on US100', mode: 'shadow', asset: { provider: 'capital', key: 'US100' },
      bars: { type: 'time', timeframe: '1' }, indice: 'us100', script_name: 'Cross',
    } as TaRule
    const [item] = ruleItems([rule])

    expect(item.href).toBe('/ta/signals/aaaaaaaaaaaa')
    expect(item.description).toBe('Shadow · US100 · 1 min')
  })

  it('finds a turbo by its knock-out level as typed, and leaves out the turbos no longer quoted', () => {
    const turbo = (key: string, status: DataAsset['status']) =>
      ({ provider: 'saxo', key, ref: `saxo/${key}`, role: 'turbo', status, barrier: 24150, direction: 'long', issuer: 'SG', symbol: null, indice: 'us100' }) as unknown as DataAsset
    const items = assetItems([turbo('2', 'recording'), turbo('3', 'ended')])

    expect(items).toHaveLength(1)
    expect(items[0].title).toBe('Long 24,150 · SG')
    expect(items[0].keywords).toContain('24150')
    expect(items[0].href).toBe('/data/assets/saxo/2')
  })

  it('offers a number typed as a position and as an order', () => {
    expect(idItems('5098').map((item) => [item.title, item.href])).toEqual([
      ['Position 5098', '/positions?find=5098'],
      ['Order 5098', '/orders?find=5098'],
    ])
    expect(idItems('ab')).toEqual([])
    // A signal's uuid, pasted from a log or a request, opens its own page.
    expect(idItems(' 0B8F6A52-6C1E-4D55-9A8E-3F1C2D4E5A6B ').map((item) => [item.title, item.href])).toEqual([
      ['Signal 0b8f6a52…', '/signals/0b8f6a52-6c1e-4d55-9a8e-3f1c2d4e5a6b'],
    ])
  })

  it('opens each kind as a list of its own, its entries always given, the ones of a feature off left out', () => {
    const runs = runItems([run()])
    const lists = listItems({ configurations: [], runs, rules: undefined, scripts: [], assets: [], conversations: null, memory: null })

    expect(lists.map((item) => [item.title, item.description])).toEqual([
      ['Backtest configurations', '0 configurations'],
      ['Backtest runs', '1 run'],
      ['Signal rules', 'Being read…'],
      ['Scripts', '0 scripts'],
      ['Assets', '0 assets'],
    ])
    expect(lists.every((item) => item.category === 'Lists' && Array.isArray(item.items) && item.href === undefined)).toBe(true)
    // The same entries as at the top: one chosen in a list is found again among the ones chosen last.
    expect(lists[1].items).toEqual(runs)
  })

  it('offers the lists and the pages with nothing typed, and everything the lists hold once something is', () => {
    const pages = pageItems()
    const runs = runItems([run()])

    const empty = paletteItems({ query: '', pages, lists: { runs } })
    expect(empty.slice(0, 7).map((item) => item.id)).toEqual(['list-configurations', 'list-runs', 'list-rules', 'list-scripts',
      'list-assets', 'list-conversations', 'list-memory'])
    expect(empty.map((item) => item.id)).not.toContain(runs[0].id)

    const typed = paletteItems({ query: '5098', pages, lists: { runs } })
    expect(typed.slice(0, 2).map((item) => item.id)).toEqual(['position-5098', 'order-5098'])
    expect(typed.map((item) => item.id)).toContain(runs[0].id)
    expect(typed.map((item) => item.id)).toContain('list-runs')
  })
})
