import { describe, expect, it } from 'vitest'

import type { DataAsset } from '@/lib/types'

import {
  assetHref,
  assetRows,
  assetsLink,
  assetType,
  capitalFacts,
  dayOf,
  historyFacts,
  legacyPricesTarget,
  openingDay,
  parseSeen,
  tableFilters,
} from './assets'

const TURBO: DataAsset = {
  provider: 'saxo', key: '2', ref: 'saxo/2', id: 2, uic: 40001234, asset_type: 'WarrantOpenEndKnockOut',
  symbol: 'SG-NDX-L1', description: 'NASDAQ 100 Turbo Long 24150 SG', currency: 'EUR', role: 'turbo', indice: 'us100',
  underlying_uic: 1909050, direction: 'long', barrier: 24150, issuer: 'SG',
  first_t: 1791784800, last_t: 1791842390, last_bid: 8.45, last_ask: 8.5, status: 'recording',
}
const HISTORY: DataAsset = {
  provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', id: null, uic: null, asset_type: 'Dukascopy',
  symbol: 'USATECHIDXUSD', description: 'US Tech 100 (Dukascopy)', currency: 'USD', role: 'underlying', indice: 'us100',
  underlying_uic: null, direction: null, barrier: null, issuer: null,
  first_t: 1789430400, last_t: 1790121590, last_bid: 25000, last_ask: 25001.5, status: 'downloaded', days: 3,
  first_day: '2026-09-15', last_day: '2026-09-22',
}

describe('an asset as a row of the table', () => {
  it('carries its name and its type among its own fields, where the search finds them', () => {
    const [turbo, history] = assetRows([TURBO, HISTORY])

    expect([turbo.name, turbo.type]).toEqual(['Long 24,150 · SG', 'long'])
    expect([history.name, history.type]).toEqual(['US Tech 100 (Dukascopy)', 'index'])
  })

  it('is of the type its role and direction say', () => {
    expect(assetType({ role: 'underlying', direction: null })).toBe('index')
    expect(assetType({ role: 'turbo', direction: 'short' })).toBe('short')
    expect(assetType({ role: 'turbo', direction: 'long' })).toBe('long')
  })

  it('leads to its own page by its provider and key', () => {
    expect(assetHref(TURBO)).toBe('/data/assets/saxo/2')
    expect(assetHref(HISTORY)).toBe('/data/assets/dukascopy/us100')
  })

  it('has its days in the trading timezone', () => {
    // 23:59:50 in Paris on Monday 12 October, 21:59:50 UTC.
    expect(dayOf(1791842390, 'Europe/Paris')).toBe('2026-10-12')
    // Two hours later it is Tuesday in Paris, still Monday in UTC.
    expect(dayOf(1791842390 + 7200, 'Europe/Paris')).toBe('2026-10-13')
  })
})

describe('the filters an address opens the table on', () => {
  it('reads the columns it can filter, several values each', () => {
    expect(tableFilters(new URLSearchParams('provider=dukascopy&type=long,short&status=ended'))).toEqual({
      provider: ['dukascopy'], type: ['long', 'short'], status: ['ended'],
    })
  })

  it('leaves out what a column does not take', () => {
    expect(tableFilters(new URLSearchParams('provider=elsewhere&type=,index&colour=red'))).toEqual({ type: ['index'] })
    expect(tableFilters(new URLSearchParams('provider=capital'))).toEqual({ provider: ['capital'] })
  })

  it('is what a link from the Providers page writes', () => {
    expect(assetsLink({ provider: 'saxo' })).toBe('/data/assets?provider=saxo')
    expect(assetsLink()).toBe('/data/assets')
    expect(tableFilters(new URLSearchParams(assetsLink({ provider: 'dukascopy' }).split('?')[1]))).toEqual({
      provider: ['dukascopy'],
    })
  })

  it('takes how far back the list goes from the address, all time by default', () => {
    expect(parseSeen('7d')).toBe('7d')
    expect(parseSeen('forever')).toBe('all')
    expect(parseSeen(null)).toBe('all')
  })
})

describe('an address of the former Prices page', () => {
  it('leads to the asset it showed, with its period and bars', () => {
    expect(legacyPricesTarget('?instrument=5&from=2026-10-06&to=2026-10-12&interval=300&side=ask')).toBe(
      '/data/assets/saxo/5?from=2026-10-06&to=2026-10-12&interval=300&side=ask',
    )
    expect(legacyPricesTarget('?instrument=2&range=auto')).toBe('/data/assets/saxo/2?range=auto')
  })

  it('leads to the Assets View when it named no instrument, or not one that can be', () => {
    expect(legacyPricesTarget('')).toBe('/data/assets')
    expect(legacyPricesTarget('?from=2026-10-06&to=2026-10-12')).toBe('/data/assets')
    expect(legacyPricesTarget('?instrument=../2')).toBe('/data/assets')
    expect(legacyPricesTarget('?instrument=0')).toBe('/data/assets')
  })

  it('drops a period that makes no sense', () => {
    expect(legacyPricesTarget('?instrument=5&from=yesterday&to=today')).toBe('/data/assets/saxo/5')
  })
})

describe('the day an asset opens on', () => {
  it('is that of its last bar, today without one', () => {
    expect(openingDay(TURBO, 'Europe/Paris', '2026-10-14')).toBe('2026-10-12')
    expect(openingDay({ ...TURBO, last_t: null }, 'Europe/Paris', '2026-10-14')).toBe('2026-10-14')
  })

  it('is that of its last file for a Dukascopy history, whose last bar is two hours into the next day in Paris', () => {
    expect(dayOf(HISTORY.last_t as number, 'Europe/Paris')).toBe('2026-09-23')
    expect(openingDay(HISTORY, 'Europe/Paris', '2026-10-14')).toBe('2026-09-22')
  })
})

describe('what an opened row says of a Dukascopy history', () => {
  it('gives its last quote, the days fetched and its symbol', () => {
    expect(historyFacts(HISTORY, 'Europe/Paris')).toEqual([
      { label: 'Last price', value: 'Bid 25,000.00 · Ask 25,001.50 · spread 1.50 at 23 Sept, 01:59:50' },
      { label: 'Fetched', value: '3 days, from 15 Sept 2026 to 22 Sept 2026' },
      { label: 'Instrument', value: 'USATECHIDXUSD · USD · Dukascopy' },
    ])
  })
})

describe('what an opened row says of an instrument of the Capital.com feed', () => {
  const US100: DataAsset = {
    provider: 'capital', key: 'US100', ref: 'capital/US100', id: 3, uic: null, asset_type: 'Capital.com', symbol: 'US100',
    description: 'US Tech 100', currency: 'USD', role: 'underlying', indice: 'us100', underlying_uic: null, direction: null,
    barrier: null, issuer: null, first_t: 1791417600, last_t: 1791842390, last_bid: 25000.5, last_ask: 25001.5,
    status: 'recording', epic: 'US100', instrument_type: 'INDICES', decimals: 1,
  }

  it('its last bar recorded, since when it is recorded, and what it is at Capital.com and for WATA', () => {
    expect(capitalFacts(US100, 'Europe/Paris')).toEqual([
      { label: 'Last bar recorded', value: 'Bid 25,000.50 · Ask 25,001.50 · spread 1.00 at 12 Oct, 23:59:50' },
      { label: 'Recorded', value: 'Every ten seconds, from 08 Oct 2026 (recording)' },
      { label: 'Instrument', value: 'US100 · INDICES · USD · Capital.com' },
      { label: 'For WATA', value: 'The index of us100' },
    ])
  })

  it('a provider of its own on the table', () => {
    expect(assetRows([US100])[0].type).toBe('index')
    expect(assetHref(US100)).toBe('/data/assets/capital/US100')
  })
})

