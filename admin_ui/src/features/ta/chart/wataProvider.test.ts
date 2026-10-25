import { describe, expect, it, vi } from 'vitest'

import type { DataAsset, PriceBarRow, PriceBars } from '@/lib/types'

import type { OhlcvBar, WataBars } from '../data/wataBars'
import type { WataTicker } from '../engine/tickers'
import { sharedAssets, wataProvider } from './wataProvider'
import type { ProviderDeps, StreamLike } from './wataProvider'

const PARIS = 'Europe/Paris'
const NOW = Date.UTC(2026, 9, 15, 10, 0, 5)
const MINUTE = Math.floor(NOW / 60_000) * 60

const asset = (changes: Partial<DataAsset>): DataAsset =>
  ({
    provider: 'capital', key: 'US100', ref: 'capital/US100', role: 'underlying', description: 'US Tech 100',
    symbol: 'US100', currency: 'USD', decimals: 1, status: 'recording', ...changes,
  }) as DataAsset

const ASSETS = [
  asset({}),
  asset({ provider: 'dukascopy', key: 'us100', ref: 'dukascopy/us100', description: 'US 100 (Dukascopy)', status: 'downloaded' }),
  asset({ provider: 'saxo', key: '2', ref: 'saxo/2', description: 'NASDAQ 100', role: 'underlying' }),
  asset({ provider: 'saxo', key: '9', ref: 'saxo/9', description: 'TURBO ended', role: 'turbo', status: 'ended' }),
]

const bar = (time: number, close: number): OhlcvBar => ({ time, open: close, high: close + 1, low: close - 1, close, volume: 3, closeTime: time + 60_000 })

class FakeStream implements StreamLike {
  listeners = new Map<string, ((event: MessageEvent<string>) => void)[]>()
  closed = false
  addEventListener(type: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }
  send(type: string, data: unknown) {
    for (const listener of this.listeners.get(type) ?? []) listener({ data: JSON.stringify(data) } as MessageEvent<string>)
  }
  close() {
    this.closed = true
  }
}

function deps(changes: Partial<ProviderDeps> = {}) {
  const asked: { ticker: WataTicker; seconds: number; window: object }[] = []
  const bars = {
    bars: vi.fn(async (ticker: WataTicker, seconds: number, window: object) => {
      asked.push({ ticker, seconds, window })
      return [bar(1000, 25000), bar(61_000, 25001)]
    }),
  } as unknown as WataBars
  const streams: FakeStream[] = []
  const timers: (() => void)[] = []
  const base: ProviderDeps = {
    bars,
    assets: async () => ASSETS.filter((entry) => !(entry.role === 'turbo' && entry.status === 'ended')),
    timeZone: PARIS,
    now: () => NOW,
    openStream: () => {
      const stream = new FakeStream()
      streams.push(stream)
      return stream
    },
    setTimer: (callback) => {
      timers.push(callback)
      return timers.length
    },
    clearTimer: () => undefined,
    ...changes,
  }
  return { deps: base, asked, streams, timers }
}

describe('a WATA provider for Vela', () => {
  it('lists its assets by the tickers PineTS can route, ended turbos left out', async () => {
    const { deps: fake } = deps()

    expect(await wataProvider('capital', fake).listSymbols?.()).toEqual([
      { ticker: 'CAPITAL_US100', description: 'US Tech 100', type: 'index' },
    ])
    expect(await wataProvider('saxo', fake).listSymbols?.()).toEqual([{ ticker: 'SAXO_2', description: 'NASDAQ 100', type: 'index' }])
    expect(await wataProvider('dukascopy', fake).listSymbols?.()).toEqual([
      { ticker: 'DUKASCOPY_US100', description: 'US 100 (Dukascopy)', type: 'index' },
    ])
  })

  it('says what Pine reads of a symbol: its step of price and the trading time zone', async () => {
    const { deps: fake } = deps()

    expect(await wataProvider('capital', fake).getSymbolInfo?.('CAPITAL_US100.R60')).toMatchObject({
      ticker: 'CAPITAL_US100.R60',
      description: 'US Tech 100',
      exchange: 'Capital.com',
      timezone: PARIS,
      mintick: 0.1,
      pricescale: 10,
    })
  })

  it('serves bars of the length asked, or range bars, and refuses what is not its own', async () => {
    const { deps: fake, asked } = deps()
    const provider = wataProvider('capital', fake)

    const bars = await provider.getBars('CAPITAL_US100', '5', { limit: 100 })
    await provider.getBars('CAPITAL_US100.ASK.R60', '1', { from: 0 })
    // What a live chart missed: Vela counts one bar a minute since then, range bars come faster.
    await provider.getBars('CAPITAL_US100.R60', '1', { from: 60_000, limit: 3 })
    await provider.getBars('CAPITAL_US100', '1', { from: 60_000, limit: 3 })
    // Bars of seconds, of the menu or typed, and four hours.
    await provider.getBars('CAPITAL_US100', '10S', { limit: 100 })
    await provider.getBars('CAPITAL_US100', '45S', { limit: 100 })
    await provider.getBars('CAPITAL_US100', '90S', { limit: 100 })
    await provider.getBars('CAPITAL_US100', '240', { limit: 100 })

    expect(bars).toEqual([
      { time: 1000, open: 25000, high: 25001, low: 24999, close: 25000, volume: 3 },
      { time: 61_000, open: 25001, high: 25002, low: 25000, close: 25001, volume: 3 },
    ])
    expect(asked.map(({ ticker, seconds }) => [ticker.side, ticker.rangeTicks, seconds])).toEqual([
      ['bid', null, 300],
      ['ask', 60, 60],
      ['bid', 60, 60],
      ['bid', null, 60],
      ['bid', null, 10],
      ['bid', null, 45],
      ['bid', null, 90],
      ['bid', null, 14_400],
    ])
    expect(asked.map(({ window }) => window).slice(2, 4)).toEqual([
      { from: 60_000, to: undefined, limit: undefined },
      { from: 60_000, to: undefined, limit: 3 },
    ])
    await expect(provider.getBars('DUKASCOPY_US100', '1', {})).rejects.toThrow("not one of WATA's Capital.com assets")
    // Under the ten seconds WATA records, over a day, or of the calendar.
    await expect(provider.getBars('CAPITAL_US100', '5S', {})).rejects.toThrow('WATA has no 5S bars: from 10 seconds to a day')
    await expect(provider.getBars('CAPITAL_US100', '2880', {})).rejects.toThrow('WATA has no 2880 bars')
    await expect(provider.getBars('CAPITAL_US100', 'W', {})).rejects.toThrow('WATA has no W bars')
  })

  it('moves the forming bar of a Capital.com chart with each quote, from the bars of the day read when the stream opens', async () => {
    const read: string[] = []
    const day: PriceBars = {
      bars: [[MINUTE, 25000, 25004, 24999, 25002, 25001, 25005, 25000, 25003, 7] as PriceBarRow],
      through: MINUTE,
      interval: 60,
    } as unknown as PriceBars
    const { deps: fake, streams } = deps({
      readBars: async (ref, period, interval) => {
        read.push(`${ref} ${period.from}..${period.to} ${interval}`)
        return day
      },
    })
    const seen: { time: number; close: number; high: number }[] = []
    const stop = wataProvider('capital', fake).subscribe?.('CAPITAL_US100', '1', (bar) => seen.push(bar))

    streams[0].send('snapshot', { server_ms: NOW, live: true, detail: null, quote: null, state: null })
    await vi.waitFor(() => expect(seen).toHaveLength(1))
    // Ten seconds later in the same minute: a quote above the high.
    streams[0].send('quote', { type: 'quote', bar: [MINUTE + 10, 25002, 25010, 25002, 25009, 25003, 25011, 25003, 25010, 2] })

    expect(read).toEqual(['capital/US100 2026-10-15..2026-10-15 60'])
    expect(seen.at(-1)).toMatchObject({ time: MINUTE * 1000, high: 25010, close: 25009 })
    stop?.()
    expect(streams[0].closed).toBe(true)
  })

  it('folds the stream into bars of 45 s: each ten seconds goes to the bar it begins in', async () => {
    // A bar of 45 s on the minute and a half: it holds the ten seconds that begin at 0, 10, 20, 30 and 40.
    const start = Math.floor(NOW / 90_000) * 90
    const day: PriceBars = {
      bars: [[start, 25000, 25004, 24999, 25002, 25001, 25005, 25000, 25003, 7] as PriceBarRow],
      through: start + 30,
      interval: 45,
    } as unknown as PriceBars
    const read: number[] = []
    const { deps: fake, streams } = deps({
      readBars: async (_ref, _period, interval) => {
        read.push(interval)
        return day
      },
    })
    const seen: { time: number; high: number; close: number }[] = []
    wataProvider('capital', fake).subscribe?.('CAPITAL_US100', '45S', (bar) => seen.push(bar))

    streams[0].send('snapshot', { server_ms: NOW, live: true, detail: null, quote: null, state: null })
    await vi.waitFor(() => expect(seen).toHaveLength(1))
    streams[0].send('bar', { type: 'bar', bar: [start + 40, 25002, 25012, 25001, 25011, 25003, 25013, 25002, 25012, 2] })
    streams[0].send('bar', { type: 'bar', bar: [start + 50, 25011, 25011, 25006, 25007, 25012, 25012, 25007, 25008, 1] })

    expect(read).toEqual([45])
    // The ten seconds from 40 to 50 are the first bar's last; those from 50 begin the bar of 45.
    expect(seen.at(-2)).toMatchObject({ time: start * 1000, high: 25012, close: 25011 })
    expect(seen.at(-1)).toMatchObject({ time: (start + 45) * 1000, high: 25011, close: 25007 })
  })

  it('reads a Saxo chart again every ten seconds, and leaves a Dukascopy one as it is', async () => {
    const { deps: fake, timers, asked } = deps()
    const seen: number[] = []

    wataProvider('saxo', fake).subscribe?.('SAXO_2', '1', (bar) => seen.push(bar.time))
    expect(timers).toHaveLength(1)
    timers[0]()
    await vi.waitFor(() => expect(seen).toEqual([1000, 61_000]))
    expect(asked[0].window).toEqual({ limit: 2 })

    timers[1]?.()
    await vi.waitFor(() => expect(asked).toHaveLength(2))
    // From the last bar handed over: only it and what came after.
    expect(asked[1].window).toEqual({ from: 61_000 })

    const stopDukascopy = wataProvider('dukascopy', fake).subscribe?.('DUKASCOPY_US100', '1', () => undefined)
    expect(timers).toHaveLength(3)
    stopDukascopy?.()
  })

  it('starts a live range chart from the newest bar it was given, rather than reading its series again', async () => {
    const asked: object[] = []
    const series = [bar(1000, 25000), bar(61_000, 25001)]
    const { deps: fake, streams, timers } = deps({
      bars: {
        bars: vi.fn(async (_ticker: WataTicker, _seconds: number, window: { from?: number; to?: number }) => {
          asked.push(window)
          return series.filter((one) => one.time >= (window.from ?? 0) && one.time <= (window.to ?? Infinity))
        }),
      } as unknown as WataBars,
    })
    const provider = wataProvider('capital', fake)
    const seen: number[] = []

    // Nothing handed over yet for that ticker: the newest bars, as before.
    provider.subscribe?.('CAPITAL_US100.ASK.R60', '1', () => undefined)
    streams[0].send('bar', {})
    await vi.waitFor(() => expect(asked).toEqual([{ limit: 2 }]))

    // Vela loads a chart's bars, then subscribes. Older bars read meanwhile do not move where it starts.
    await provider.getBars('CAPITAL_US100.R60', '1', { limit: 500 })
    series.push(bar(121_000, 25002))
    await provider.getBars('CAPITAL_US100.R60', '1', { to: 1000, limit: 500 })
    provider.subscribe?.('CAPITAL_US100.R60', '1', (one) => seen.push(one.time))
    streams[1].send('bar', {})
    await vi.waitFor(() => expect(seen).toEqual([61_000, 121_000]))
    expect(asked.slice(1)).toEqual([{ limit: 500 }, { to: 1000, limit: 500 }, { from: 61_000 }])

    // A Saxo range chart, read again every ten seconds, starts the same way.
    const saxo = wataProvider('saxo', fake)
    await saxo.getBars('SAXO_2.R60', '1', { limit: 500 })
    saxo.subscribe?.('SAXO_2.R60', '1', () => undefined)
    timers.at(-1)?.()
    await vi.waitFor(() => expect(asked.at(-1)).toEqual({ from: 121_000 }))
  })

  it('reads the asset list once for the three providers', async () => {
    const read = vi.fn(async () => ({ items: ASSETS }))
    const assets = sharedAssets(read)

    await Promise.all([assets(), assets(), assets()])

    expect(read).toHaveBeenCalledTimes(1)
    expect((await assets()).map((entry) => entry.ref)).toEqual(['capital/US100', 'dukascopy/us100', 'saxo/2'])
  })
})
