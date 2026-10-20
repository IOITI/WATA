// PineTS's market data for a run, in the browser's worker and in the server's
// runner alike: the chart's bars as they came, any other series asked of whoever
// has WATA's data (the page, or the TA engine).

import { BaseProvider } from 'pinets'
import type { ISymbolInfo, Kline } from 'pinets'

import { TIMEFRAMES } from './timeframes'
import type { Candle } from './workerProtocol'

/** Asks for the bars of another symbol or timeframe (request.security). */
export type SeriesAsk = (
  symbol: string,
  timeframe: string,
  window: { from?: number; to?: number; limit?: number },
) => Promise<Candle[]>

/** The chart a script runs on. */
export interface ChartSeries {
  symbol: string
  timeframe: string
  bars: Candle[]
}

export function bareTicker(symbol: string): string {
  const colon = symbol.lastIndexOf(':')
  return colon === -1 ? symbol : symbol.slice(colon + 1)
}

/**
 * PineTS hands over tickers without their exchange prefix, and builds the
 * timeframes WATA has not (4 hours, a day) from the ones it has. No time zone is
 * given for a symbol: PineTS's days are then UTC days, in the pages and on the
 * server.
 */
export class SeriesProvider extends BaseProvider {
  private readonly chart: ChartSeries
  private readonly ask: SeriesAsk

  constructor(chart: ChartSeries, ask: SeriesAsk) {
    super({ requiresApiKey: false, providerName: 'WATA' })
    this.chart = chart
    this.ask = ask
  }

  protected getSupportedTimeframes(): Set<string> {
    return new Set(TIMEFRAMES.map((timeframe) => timeframe.pine))
  }

  protected async _getMarketDataNative(
    tickerId: string,
    timeframe: string,
    limit?: number,
    sDate?: number,
    eDate?: number,
  ): Promise<Kline[]> {
    const own = bareTicker(tickerId) === bareTicker(this.chart.symbol) && timeframe === this.chart.timeframe
    const candles = own ? this.chart.bars : await this.ask(bareTicker(tickerId), timeframe, { from: sDate, to: eDate, limit })
    return candles as unknown as Kline[]
  }

  async getSymbolInfo(tickerId: string): Promise<ISymbolInfo> {
    const ticker = bareTicker(tickerId)
    return { ticker, tickerid: ticker, description: ticker } as unknown as ISymbolInfo
  }
}
