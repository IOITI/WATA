// The Charts page's chart: Vela's workspace (one chart; its grids stay offered) on
// WATA's three providers, with WATA's scripts in its own indicator picker and the
// Pine engine in a worker of our own file (vite/velaPineWorker.ts).
//
// The only file that builds Vela, as PriceChart.tsx is the only one that builds
// Lightweight Charts.

import { PineWorkerEngine } from '@luxalgo/vela-pinets'
import { VelaWorkspace } from '@luxalgo/vela/workspace'
import { useEffect, useRef, useState } from 'react'
import velaWorkerUrl from 'virtual:vela-pine-worker'

import { useTheme } from '@/components/theme-context'
import { cn } from '@/lib/utils'

import { WataBars } from '../data/wataBars'
import type { LibraryResolver } from '../engine/prepare'
import { parseTicker, PROVIDERS, tickerText } from '../engine/tickers'
import type { ProviderId, WataTicker } from '../engine/tickers'
import { DEFAULT_TIMEFRAME, TIMEFRAMES } from '../engine/timeframes'
import { sharedAssets, wataProvider } from './wataProvider'
import { LAYOUT_KEY, layoutStorage, scriptEntries } from './workspace'

export interface TaChartProps {
  /** The saved layout, with the address's symbol and timeframe over it; null to start afresh. */
  layout: string | null
  /** The symbol and timeframe the address names (Vela's `provider:TICKER`, Pine's minutes); null: the layout's. */
  symbol: string | null
  timeframe: string | null
  /** What a chart without layout nor address opens on. */
  fallbackSymbol: string
  timeZone: string
  resolve: LibraryResolver
  /** A WATA script to put on the chart once (Open in Charts). */
  addScript: string | null
  onScriptAdded: () => void
  /** The chart moved to another symbol or timeframe (Vela's own pickers included). */
  onMarket: (symbol: string, timeframe: string) => void
  /** Scripts that could not be prepared for the chart, and why. */
  onSkipped: (skipped: { name: string; reason: string }[]) => void
  className?: string
}

/** The ticker of a Vela symbol, with its provider: `capital:CAPITAL_US100.R60`. */
function velaTicker(symbol: string): WataTicker | null {
  return parseTicker(symbol)
}

/** Two symbols of the same series, whatever prefix either is written with. */
function sameTicker(a: string, b: string): boolean {
  const first = parseTicker(a)
  const second = parseTicker(b)
  return first !== null && second !== null && tickerText(first) === tickerText(second) && first.provider === second.provider
}

function velaSymbolOf(ticker: WataTicker): string {
  return `${ticker.provider}:${tickerText(ticker)}`
}

export function TaChart(props: TaChartProps) {
  const { layout, symbol, timeframe, fallbackSymbol, timeZone, resolve, addScript, className } = props
  const host = useRef<HTMLDivElement>(null)
  const workspace = useRef<VelaWorkspace | null>(null)
  const callbacks = useRef(props)
  const { theme } = useTheme()
  const [market, setMarket] = useState({ symbol: symbol ?? fallbackSymbol, timeframe: timeframe ?? DEFAULT_TIMEFRAME.pine })
  // Where the chart last said it is: an address that only follows it asks nothing of it.
  const shown = useRef(market)

  useEffect(() => {
    callbacks.current = props
  })

  // Built once: what changes afterwards is passed to it below.
  useEffect(() => {
    if (!host.current) return
    const bars = new WataBars()
    const assets = sharedAssets()
    const deps = { bars, assets, timeZone }
    const entries = scriptEntries(resolve)
    entries.then((found) => callbacks.current.onSkipped(found.skipped), () => undefined)

    const providers = Object.fromEntries(
      PROVIDERS.map((provider: ProviderId) => [provider, () => wataProvider(provider, deps)]),
    )
    const built = new VelaWorkspace(host.current, {
      layout: false,
      symbol: symbol ?? fallbackSymbol,
      timeframe: timeframe ?? DEFAULT_TIMEFRAME.pine,
      theme,
      timezone: timeZone,
      timeframes: TIMEFRAMES.map((length) => length.pine),
      providers,
      engines: { pine: () => new PineWorkerEngine({ workerUrl: velaWorkerUrl }) },
      indicators: async () => (await entries).entries.map((script) => script.entry),
      persist: LAYOUT_KEY,
      storage: layoutStorage(layout),
    })
    workspace.current = built

    const chart = built.active.chart
    const unsubscribe = chart.on('market:changed', (change: { symbol: string; timeframe: string }) => {
      shown.current = { symbol: change.symbol, timeframe: change.timeframe }
      setMarket({ symbol: change.symbol, timeframe: change.timeframe })
      callbacks.current.onMarket(change.symbol, change.timeframe)
    })
    return () => {
      unsubscribe()
      built.destroy()
      workspace.current = null
    }
    // Built once with the page; the effects below follow what changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    workspace.current?.setTheme(theme)
  }, [theme])

  // An address that names another symbol or timeframe (a link followed while the page is open).
  useEffect(() => {
    const cell = workspace.current?.active
    if (!cell) return
    if (symbol && !sameTicker(symbol, shown.current.symbol)) {
      shown.current = { ...shown.current, symbol }
      cell.setSymbol(symbol)
    }
    if (timeframe && timeframe !== shown.current.timeframe) {
      shown.current = { ...shown.current, timeframe }
      cell.setTimeframe(timeframe)
    }
  }, [symbol, timeframe])

  // A script to put on the chart: once the indicator list and the first bars are there.
  useEffect(() => {
    const built = workspace.current
    if (!built || !addScript) return
    let cancelled = false
    void (async () => {
      const { entries } = await scriptEntries(resolve)
      const found = entries.find((script) => script.id === addScript)
      await built.active.chart.ready()
      if (cancelled || !found) return
      const shown = built.active.onChartRows().some((row) => row.name === found.entry.name)
      if (!shown) built.active.addManifestInstance(found.entry, { record: true })
      callbacks.current.onScriptAdded()
    })()
    return () => {
      cancelled = true
    }
  }, [addScript, resolve])

  const ticker = velaTicker(market.symbol)
  const switchTo = (change: Partial<Pick<WataTicker, 'side' | 'rangeTicks'>>) => {
    if (!ticker) return
    workspace.current?.active.setSymbol(velaSymbolOf({ ...ticker, ...change }))
  }

  return (
    <div className={cn('flex min-h-0 flex-col gap-2', className)}>
      {ticker && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs" aria-label="Prices of the chart">
          <span className="font-mono text-muted-foreground">{tickerText(ticker)}</span>
          <div role="group" aria-label="Price side" className="flex overflow-hidden rounded-lg border border-border">
            {(['bid', 'ask'] as const).map((side) => (
              <button
                key={side}
                type="button"
                aria-pressed={ticker.side === side}
                className={cn('px-2.5 py-1 capitalize', ticker.side === side ? 'bg-surface-raised font-medium' : 'hover:bg-surface-hover')}
                onClick={() => switchTo({ side })}
              >
                {side}
              </button>
            ))}
          </div>
          <div role="group" aria-label="Bars" className="flex items-center overflow-hidden rounded-lg border border-border">
            <button
              type="button"
              aria-pressed={!ticker.rangeTicks}
              className={cn('px-2.5 py-1', !ticker.rangeTicks ? 'bg-surface-raised font-medium' : 'hover:bg-surface-hover')}
              onClick={() => switchTo({ rangeTicks: null })}
            >
              Time
            </button>
            <button
              type="button"
              aria-pressed={!!ticker.rangeTicks}
              className={cn('px-2.5 py-1', ticker.rangeTicks ? 'bg-surface-raised font-medium' : 'hover:bg-surface-hover')}
              onClick={() => switchTo({ rangeTicks: ticker.rangeTicks ?? 60 })}
            >
              Range
            </button>
          </div>
          {ticker.rangeTicks && (
            <label className="flex items-center gap-1.5">
              <span className="text-muted-foreground">R</span>
              <input
                type="number"
                min={1}
                defaultValue={ticker.rangeTicks}
                key={ticker.rangeTicks}
                aria-label="Range, in steps of the price"
                className="w-16 rounded-md border border-border bg-surface px-1.5 py-0.5 font-mono"
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') return
                  const value = Number((event.target as HTMLInputElement).value)
                  if (Number.isInteger(value) && value >= 1) switchTo({ rangeTicks: value })
                }}
                onBlur={(event) => {
                  const value = Number(event.target.value)
                  if (Number.isInteger(value) && value >= 1 && value !== ticker.rangeTicks) switchTo({ rangeTicks: value })
                }}
              />
            </label>
          )}
          {ticker.rangeTicks && (
            <span className="text-muted-foreground">
              Built from ten-second bars. On this chart a range bar's time_close is when the next one began; the
              editor's test and the signal engine use the end of its last ten seconds.
            </span>
          )}
        </div>
      )}
      <div ref={host} className="min-h-[480px] flex-1 overflow-hidden rounded-2xl border border-border" data-testid="ta-chart" />
    </div>
  )
}
