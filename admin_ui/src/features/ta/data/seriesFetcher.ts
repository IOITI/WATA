// WATA's bars as a Pine script reads them: PineTS candles, and the other series a
// script asks for through request.security, read from WATA by their ticker.

import type { SeriesFetcher } from '../engine/pineWorkerClient'
import { parseTicker } from '../engine/tickers'
import { timeframeOf } from '../engine/timeframes'
import type { Candle } from '../engine/workerProtocol'
import type { OhlcvBar, WataBars } from './wataBars'

export function toCandle(bar: OhlcvBar): Candle {
  return {
    openTime: bar.time,
    closeTime: bar.closeTime,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  }
}

export function wataSeriesFetcher(bars: WataBars): SeriesFetcher {
  return async (symbol, timeframe, window) => {
    const ticker = parseTicker(symbol)
    if (!ticker) {
      throw new Error(
        `${symbol} is not one of WATA's assets: request.security takes CAPITAL_US100, DUKASCOPY_US100 or SAXO_<id>`,
      )
    }
    const length = timeframeOf(timeframe)
    if (!length && !ticker.rangeTicks) {
      throw new Error(`WATA has no ${timeframe} bars: from 10 seconds (10S) to a day (1440)`)
    }
    const found = await bars.bars(ticker, length?.seconds ?? 60, window)
    return found.map(toCandle)
  }
}
