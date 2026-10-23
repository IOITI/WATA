import { describe, expect, it } from 'vitest'

import type { DataAsset } from '@/lib/types'
import { run } from '@/test/backtests'

import { assetItems, idItems, pageItems, runItems } from './commandItems'
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
  })
})
