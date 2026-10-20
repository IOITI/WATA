// WATA's assets as Vela data providers, one per provider of the Data section:
// `capital`, `dukascopy`, `saxo` (Vela's own guide: docs/contributing/adding-a-data-provider.md).
//
// Bars come from the admin's API through WataBars. What moves them:
// - Capital.com: the feed's live stream (/api/data/assets/capital/<epic>/live), folded
//   into the bar of the chart's length by the asset pages' own rule (liveTail.ts);
//   range bars are read again at each closed ten seconds;
// - Saxo: the recorder writes ten-second bars, read again every ten seconds;
// - Dukascopy: days downloaded, nothing live.

import type { DataProvider, OHLCV, ProviderInfo, SymbolDescriptor, SymbolInfo } from '@luxalgo/vela'

import { startTail, tailRows, withBar, withQuote } from '@/features/prices/liveTail'
import type { LiveTail } from '@/features/prices/liveTail'
import { api, assetLiveUrl } from '@/lib/api'
import type { DataAsset, LiveBarMessage, LiveQuoteMessage, PriceBars } from '@/lib/types'

import { assetTicker, testableAssets } from '../chartLinks'
import { dayIn, timeBar } from '../data/wataBars'
import type { OhlcvBar, WataBars } from '../data/wataBars'
import { baseTicker, parseTicker, refOf, tickerText } from '../engine/tickers'
import type { ProviderId, WataTicker } from '../engine/tickers'
import { TIMEFRAMES, timeframeOf } from '../engine/timeframes'

export const PROVIDER_LABEL: Record<ProviderId, string> = {
  capital: 'Capital.com',
  dukascopy: 'Dukascopy',
  saxo: 'Saxo',
}

/** How often a Saxo chart reads its last bars again: the recorder writes ten-second bars. */
export const SAXO_REFRESH_MS = 10_000

/** What an EventSource is to the provider: the browser's, or a stand-in in tests. */
export interface StreamLike {
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void): void
  close(): void
}

export interface ProviderDeps {
  bars: WataBars
  /** The assets of the Data section, read once for the three providers. */
  assets: () => Promise<DataAsset[]>
  /** A day of bars of an asset, with the last ten seconds they hold (`through`). */
  readBars?: (ref: string, period: { from: string; to: string }, interval: number) => Promise<PriceBars>
  openStream?: (url: string) => StreamLike
  timeZone: string
  now?: () => number
  setTimer?: (callback: () => void, ms: number) => unknown
  clearTimer?: (timer: unknown) => void
}

function ohlcv(bar: OhlcvBar): OHLCV {
  return { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }
}

function parsed<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T
  } catch {
    return null
  }
}

/** The assets a chart can show, read once a minute at most for the three providers. */
export function sharedAssets(read: () => Promise<{ items: DataAsset[] }> = () => api.dataAssets('all')): () => Promise<DataAsset[]> {
  let cached: { at: number; assets: Promise<DataAsset[]> } | null = null
  return () => {
    if (!cached || Date.now() - cached.at > 60_000) {
      const assets = read().then((answer) => testableAssets(answer.items))
      assets.catch(() => {
        cached = null
      })
      cached = { at: Date.now(), assets }
    }
    return cached.assets
  }
}

export function wataProvider(provider: ProviderId, deps: ProviderDeps): DataProvider {
  const now = deps.now ?? (() => Date.now())
  const readBars = deps.readBars ?? ((ref, period, interval) => api.assetBars(ref, period, interval))
  const openStream = deps.openStream ?? ((url: string) => new EventSource(url) as unknown as StreamLike)
  const setTimer = deps.setTimer ?? ((callback: () => void, ms: number) => setTimeout(callback, ms))
  const clearTimer = deps.clearTimer ?? ((timer: unknown) => clearTimeout(timer as ReturnType<typeof setTimeout>))

  const tickerOf = (text: string): WataTicker => {
    const ticker = parseTicker(text)
    if (!ticker || ticker.provider !== provider) {
      throw new Error(`${text} is not one of WATA's ${PROVIDER_LABEL[provider]} assets`)
    }
    return ticker
  }

  const secondsOf = (ticker: WataTicker, timeframe: string): number => {
    const length = timeframeOf(timeframe)
    if (length) return length.seconds
    // Range bars take no length; a chart of them still names one.
    if (ticker.rangeTicks) return 60
    throw new Error(`WATA has no ${timeframe} bars: 1, 5, 15 or 60 minutes`)
  }

  const assetOf = async (ticker: WataTicker): Promise<DataAsset | undefined> =>
    (await deps.assets()).find((asset) => asset.provider === provider && asset.key === ticker.key)

  /** Reads the newest bars again every `ms`, and hands over those at or after the last one handed. */
  const poll = (ticker: WataTicker, seconds: number, onBar: (bar: OHLCV) => void, ms: number) => {
    let stopped = false
    let timer: unknown = null
    let last = 0
    const read = async () => {
      try {
        const found = await deps.bars.bars(ticker, seconds, last ? { from: last } : { limit: 2 })
        for (const bar of found) {
          if (stopped || bar.time < last) continue
          last = bar.time
          onBar(ohlcv(bar))
        }
      } catch {
        // The next read tries again.
      }
      if (!stopped) timer = setTimer(() => void read(), ms)
    }
    timer = setTimer(() => void read(), ms)
    return () => {
      stopped = true
      if (timer !== null) clearTimer(timer)
    }
  }

  /** Capital.com's bars of time, moved by each quote of the feed's stream. */
  const stream = (ticker: WataTicker, seconds: number, onBar: (bar: OHLCV) => void) => {
    let tail: LiveTail | null = null
    let closed = false
    const recent: LiveBarMessage['bar'][] = []
    const emit = () => {
      if (!tail || closed) return
      for (const row of tailRows(tail)) {
        const bar = timeBar(row, ticker.side, seconds)
        if (bar) onBar(ohlcv(bar))
      }
    }
    // The bars of the day so far: what the stream adds to. Read again at each (re)connection.
    const start = async () => {
      const today = dayIn(now(), deps.timeZone)
      try {
        const answer = await readBars(refOf(ticker), { from: today, to: today }, seconds)
        let next = startTail(answer.bars, answer.through, seconds)
        for (const bar of recent) next = withBar(next, bar)
        tail = next
        emit()
      } catch {
        // Without them, the next closed ten seconds start a bar of their own.
      }
    }
    const source = openStream(assetLiveUrl(refOf(ticker)))
    source.addEventListener('snapshot', () => void start())
    source.addEventListener('resync', () => void start())
    source.addEventListener('quote', (event) => {
      const quote = parsed<LiveQuoteMessage>(event)
      if (!quote || !tail) return
      tail = withQuote(tail, quote.bar)
      emit()
    })
    source.addEventListener('bar', (event) => {
      const message = parsed<LiveBarMessage>(event)
      if (!message) return
      recent.push(message.bar)
      if (recent.length > 360) recent.shift()
      if (!tail) return
      tail = withBar(tail, message.bar)
      emit()
    })
    return () => {
      closed = true
      source.close()
    }
  }

  /** Capital.com's range bars: read again when ten seconds close, as the asset page does. */
  const rangeStream = (ticker: WataTicker, onBar: (bar: OHLCV) => void) => {
    let stopped = false
    let reading = false
    let last = 0
    const read = async () => {
      if (reading || stopped) return
      reading = true
      try {
        const found = await deps.bars.bars(ticker, 60, last ? { from: last } : { limit: 2 })
        for (const bar of found) {
          if (stopped || bar.time < last) continue
          last = bar.time
          onBar(ohlcv(bar))
        }
      } catch {
        // The next closed ten seconds try again.
      } finally {
        reading = false
      }
    }
    const source = openStream(assetLiveUrl(refOf(ticker)))
    source.addEventListener('bar', () => void read())
    source.addEventListener('resync', () => void read())
    return () => {
      stopped = true
      source.close()
    }
  }

  return {
    info(): ProviderInfo {
      return {
        name: provider,
        displayName: PROVIDER_LABEL[provider],
        supportedTimeframes: TIMEFRAMES.map((length) => length.pine),
        capabilities: { enumerate: true, stream: provider !== 'dukascopy', symbolInfo: true },
      }
    },

    async listSymbols(): Promise<SymbolDescriptor[]> {
      return (await deps.assets())
        .filter((asset) => asset.provider === provider)
        .flatMap((asset) => {
          const ticker = assetTicker(asset)
          if (!ticker) return []
          return [
            {
              ticker: baseTicker(ticker),
              description: asset.description ?? asset.symbol ?? asset.key,
              type: asset.role === 'turbo' ? 'turbo' : 'index',
            },
          ]
        })
    },

    async getSymbolInfo(text: string): Promise<SymbolInfo | undefined> {
      const ticker = tickerOf(text)
      const asset = await assetOf(ticker)
      const decimals = typeof asset?.decimals === 'number' ? asset.decimals : 2
      return {
        ticker: tickerText(ticker),
        description: asset?.description ?? asset?.symbol ?? ticker.key,
        type: asset?.role === 'turbo' ? 'turbo' : 'index',
        currency: asset?.currency ?? undefined,
        exchange: PROVIDER_LABEL[provider],
        timezone: deps.timeZone,
        mintick: 10 ** -decimals,
        pricescale: 10 ** decimals,
      }
    },

    async getBars(text, timeframe, range) {
      const ticker = tickerOf(text)
      const found = await deps.bars.bars(ticker, secondsOf(ticker, timeframe), {
        from: range.from,
        to: range.to,
        limit: range.limit,
      })
      return found.map(ohlcv)
    },

    subscribe(text, timeframe, onBar) {
      const ticker = tickerOf(text)
      if (provider === 'dukascopy') return () => undefined
      if (provider === 'saxo') return poll(ticker, secondsOf(ticker, timeframe), onBar, SAXO_REFRESH_MS)
      return ticker.rangeTicks ? rangeStream(ticker, onBar) : stream(ticker, secondsOf(ticker, timeframe), onBar)
    },
  }
}
