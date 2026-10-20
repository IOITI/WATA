import { describe, expect, it } from 'vitest'

import { baseTicker, parseTicker, refOf, tickerText, velaSymbol } from './tickers'
import { daysPerRequest, timeframeOf } from './timeframes'

describe('tickers', () => {
  it.each([
    ['CAPITAL_US100', { provider: 'capital', key: 'US100', side: 'bid', rangeTicks: null }],
    ['capital:CAPITAL_US100', { provider: 'capital', key: 'US100', side: 'bid', rangeTicks: null }],
    ['DUKASCOPY_US100', { provider: 'dukascopy', key: 'us100', side: 'bid', rangeTicks: null }],
    ['SAXO_12.ASK', { provider: 'saxo', key: '12', side: 'ask', rangeTicks: null }],
    ['CAPITAL_US100.R60', { provider: 'capital', key: 'US100', side: 'bid', rangeTicks: 60 }],
    ['CAPITAL_US100.R60.ASK', { provider: 'capital', key: 'US100', side: 'ask', rangeTicks: 60 }],
    ['CAPITAL_US100.ASK.R60', { provider: 'capital', key: 'US100', side: 'ask', rangeTicks: 60 }],
  ])('reads %s', (text, ticker) => {
    expect(parseTicker(text)).toEqual(ticker)
  })

  it.each(['US100', 'SKILLING:US100', 'SAXO_abc', 'SAXO_0', 'DUKASCOPY_', 'CAPITAL_US100.R0', 'OTHER_US100'])(
    'names no WATA asset: %s',
    (text) => {
      expect(parseTicker(text)).toBeNull()
    },
  )

  it('writes a ticker back as it reads it, and the address of its asset', () => {
    const ticker = { provider: 'dukascopy' as const, key: 'us100', side: 'ask' as const, rangeTicks: 60 }

    expect(tickerText(ticker)).toBe('DUKASCOPY_US100.ASK.R60')
    expect(parseTicker(tickerText(ticker))).toEqual(ticker)
    expect(baseTicker(ticker)).toBe('DUKASCOPY_US100')
    expect(refOf(ticker)).toBe('dukascopy/us100')
    expect(velaSymbol(ticker)).toBe('dukascopy:DUKASCOPY_US100.ASK.R60')
  })
})

describe('timeframes', () => {
  it.each([
    ['1', 60],
    ['5', 300],
    ['15', 900],
    ['60', 3600],
    ['1h', 3600],
    ['15m', 900],
  ])('serves %s as %i seconds', (name, seconds) => {
    expect(timeframeOf(name)?.seconds).toBe(seconds)
  })

  it.each(['10S', 'D', '240', '2'])('has no %s', (name) => {
    expect(timeframeOf(name)).toBeNull()
  })

  it('asks for as many whole days as stay under the cap', () => {
    expect(daysPerRequest(60)).toBe(8)
    expect(daysPerRequest(300)).toBe(41)
    expect(daysPerRequest(3600, 31)).toBe(31)
    expect(daysPerRequest(10)).toBe(1)
  })
})
