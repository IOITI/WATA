import { describe, expect, it } from 'vitest'

import { SeriesProvider } from './provider'
import type { SeriesAsk } from './provider'
import type { Candle } from './workerProtocol'

const candle = (openTime: number, seconds = 60): Candle => ({
  openTime, closeTime: openTime + seconds * 1000, open: 1, high: 2, low: 0.5, close: 1.5, volume: 3,
})

function provider(timeframe: string) {
  const asked: string[] = []
  const ask: SeriesAsk = async (symbol, length) => {
    asked.push(`${symbol} ${length}`)
    return [candle(0), candle(60_000)]
  }
  const chart = { symbol: 'CAPITAL_US100', timeframe, bars: [candle(0, 90), candle(90_000, 90)] }
  return { pine: new SeriesProvider(chart, ask), asked }
}

describe('the market data of a run', () => {
  it('gives the chart its own bars, whatever their length, and asks WATA for another series of a length it makes', async () => {
    const { pine, asked } = provider('90S')

    expect(await pine.getMarketData('CAPITAL_US100', '90S')).toHaveLength(2)
    await pine.getMarketData('DUKASCOPY_US100', '90S')
    await pine.getMarketData('DUKASCOPY_US100', '10S')
    await pine.getMarketData('DUKASCOPY_US100', '45S')
    // Any length of seconds WATA makes, on WATA's grid, rather than built by PineTS of shorter bars.
    await pine.getMarketData('DUKASCOPY_US100;heikinashi', '180S')
    // Four hours are none of WATA's names: PineTS builds them from its hours, as before.
    await pine.getMarketData('DUKASCOPY_US100', '240')

    expect(asked).toEqual([
      'DUKASCOPY_US100 90S', 'DUKASCOPY_US100 10S', 'DUKASCOPY_US100 45S', 'DUKASCOPY_US100 180S', 'DUKASCOPY_US100 60',
    ])
  })

  it('reads the chart\'s length however PineTS writes it', async () => {
    const { pine, asked } = provider('2')

    await pine.getMarketData('DUKASCOPY_US100', '2')
    await pine.getMarketData('DUKASCOPY_US100', '120S')
    expect(await pine.getMarketData('CAPITAL_US100', '120S')).toHaveLength(2)

    expect(asked).toEqual(['DUKASCOPY_US100 2', 'DUKASCOPY_US100 120S'])
  })
})
