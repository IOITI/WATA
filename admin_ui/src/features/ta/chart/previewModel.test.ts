import { describe, expect, it } from 'vitest'

import type { DataAsset } from '@/lib/types'

import { fallbackSymbol } from '../chartLinks'
import { PREVIEW_TIMEFRAME, PREVIEW_TIMEFRAMES, previewAssets, previewSymbol } from './previewModel'

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
