// PineTS's market data for a run, in the browser's worker and in the server's
// runner alike: the chart's bars as they came, any other series asked of whoever
// has WATA's data (the page, or the TA engine).

import { BaseProvider, stripTickerModifier } from 'pinets'
import type { ISymbolInfo, Kline } from 'pinets'

import { TIMEFRAMES, timeframeOf } from './timeframes'
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
 * timeframes WATA does not name (4 hours, a day) from the ones it does: those of
 * the pickers, the chart's own whatever its length, and any length of seconds
 * WATA makes ("90S"). No time zone is given for a symbol: PineTS's days are then
 * UTC days, in the pages and on the server.
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
    const names = new Set(TIMEFRAMES.map((timeframe) => timeframe.pine))
    // Another symbol read at the chart's own length gets bars of WATA's, on the chart's grid,
    // however PineTS writes it (two minutes are "2", or "120S").
    const chart = timeframeOf(this.chart.timeframe)
    if (chart) names.add(chart.pine).add(`${chart.seconds}S`)
    return names
  }

  /**
   * A length of seconds WATA makes is read as WATA makes it, on its own grid: PineTS
   * would build 90 seconds of two bars of 45 from wherever its first one fell.
   */
  async getMarketData(tickerId: string, timeframe: string, limit?: number, sDate?: number, eDate?: number): Promise<Kline[]> {
    if (/^\d+S$/i.test(timeframe.trim()) && timeframeOf(timeframe)) {
      return this._getMarketDataNative(stripTickerModifier(tickerId), timeframe.trim(), limit, sDate, eDate)
    }
    return super.getMarketData(tickerId, timeframe, limit, sDate, eDate)
  }

  protected async _getMarketDataNative(
    tickerId: string,
    timeframe: string,
    limit?: number,
    sDate?: number,
    eDate?: number,
  ): Promise<Kline[]> {
    // The chart's length however it is written: two minutes are "2" or "120S".
    const chartLength = timeframeOf(this.chart.timeframe)?.seconds
    const sameLength = timeframe === this.chart.timeframe || (chartLength !== undefined && timeframeOf(timeframe)?.seconds === chartLength)
    const own = bareTicker(tickerId) === bareTicker(this.chart.symbol) && sameLength
    const candles = own ? this.chart.bars : await this.ask(bareTicker(tickerId), timeframe, { from: sDate, to: eDate, limit })
    return candles as unknown as Kline[]
  }

  async getSymbolInfo(tickerId: string): Promise<ISymbolInfo> {
    const ticker = bareTicker(tickerId)
    return { ticker, tickerid: ticker, description: ticker } as unknown as ISymbolInfo
  }
}
