import { describe, expect, it } from 'vitest'

import type { DataAsset, TaRule } from '@/lib/types'

import { fallbackSymbol } from '../chartLinks'
import { PREVIEW_TIMEFRAME, PREVIEW_TIMEFRAMES, previewAssets, previewMarket, previewSymbol, scriptBars, scriptOpening } from './previewModel'

const asset = (fields: Partial<DataAsset>) => ({ role: 'underlying', status: 'live', ...fields }) as DataAsset
const CAPITAL = asset({ provider: 'capital', key: 'US100', ref: 'capital/US100', description: 'US Tech 100' })
const DUKASCOPY = asset({ provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', description: null, symbol: 'us100' })
const TURBO = asset({ provider: 'saxo', key: '12', ref: 'saxo/12', role: 'turbo', description: 'Turbo long' })

describe('an indicator of LuxAlgo on a chart', () => {
  it('is drawn on the indices and histories, never a turbo', () => {
    expect(previewAssets([TURBO, DUKASCOPY, CAPITAL])).toEqual([
      { symbol: 'dukascopy:DUKASCOPY_US100', label: 'us100 · Dukascopy' },
      { symbol: 'capital:CAPITAL_US100', label: 'US Tech 100 · Capital.com' },
    ])
  })

  it("opens on the asset chosen when it is offered, else on the Charts page's", () => {
    const assets = [TURBO, DUKASCOPY, CAPITAL]
    expect(previewSymbol(assets, null)).toBe('capital:CAPITAL_US100')
    expect(previewSymbol(assets, 'dukascopy:DUKASCOPY_US100')).toBe('dukascopy:DUKASCOPY_US100')
    expect(previewSymbol(assets, 'saxo:SAXO_12')).toBe('capital:CAPITAL_US100')
    expect(fallbackSymbol([DUKASCOPY])).toBe('dukascopy:DUKASCOPY_US100')
    expect(fallbackSymbol([])).toBe('capital:CAPITAL_US100')
  })

  it('offers bars of one minute to an hour, five by default', () => {
    expect(PREVIEW_TIMEFRAMES.map((entry) => entry.value)).toEqual(['1', '5', '15', '60'])
    expect(PREVIEW_TIMEFRAME).toBe('5')
  })
})

const rule = (name: string, bars: TaRule['bars'], key = 'US100') =>
  ({ name, bars, asset: { provider: 'capital', key }, side: 'ask' }) as TaRule

describe('a script on its Chart tab', () => {
  it('is offered the bars its rules run it on first, once each, then one minute to an hour', () => {
    const offered = scriptBars([rule('Kernel', { type: 'range', ticks: 60 }), rule('Kernel short', { type: 'range', ticks: 60 }),
      rule('Cross', { type: 'time', timeframe: '5' })])

    expect(offered.map((entry) => [entry.value, entry.label])).toEqual([
      ['R60', 'Range 60, as 2 rules run it'],
      ['5', '5 min, as “Cross” runs it'],
      ['1', '1 minute'],
      ['15', '15 minutes'],
      ['60', '1 hour'],
    ])
    expect(offered[0]).toMatchObject({ rangeTicks: 60, timeframe: '1' })
    expect(scriptBars([]).map((entry) => entry.value)).toEqual(['1', '5', '15', '60'])
  })

  it('opens on its first rule’s asset and bars, else on the Charts page’s asset in five minutes', () => {
    expect(scriptOpening([DUKASCOPY, CAPITAL], [rule('Kernel', { type: 'range', ticks: 60 })])).toEqual({ symbol: 'capital:CAPITAL_US100', bars: 'R60' })
    // Its asset not offered: the Charts page's in five minutes, never 60 of another asset's ticks.
    expect(scriptOpening([DUKASCOPY], [rule('Kernel', { type: 'range', ticks: 60 })])).toEqual({ symbol: 'dukascopy:DUKASCOPY_US100', bars: '5' })
    expect(scriptOpening([DUKASCOPY, CAPITAL], [])).toEqual({ symbol: 'capital:CAPITAL_US100', bars: '5' })
  })

  it('gives Vela the asset in range bars with a minute, or in bars of time with their own', () => {
    const [range, five] = scriptBars([rule('Kernel', { type: 'range', ticks: 60 }), rule('Cross', { type: 'time', timeframe: '5' })])

    expect(previewMarket('capital:CAPITAL_US100', range)).toEqual({ symbol: 'capital:CAPITAL_US100.R60', timeframe: '1' })
    expect(previewMarket('capital:CAPITAL_US100.R60', five)).toEqual({ symbol: 'capital:CAPITAL_US100', timeframe: '5' })
  })
})
