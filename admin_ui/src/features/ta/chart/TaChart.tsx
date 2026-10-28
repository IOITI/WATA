// The Charts page's chart: Vela's workspace (one chart; its grids stay offered) on
// WATA's three providers, with WATA's scripts in its own indicator picker and the
// Pine engine in a worker of our own file (vite/velaPineWorker.ts).
//
// Live: Vela draws history alone unless told, and only a live chart asks the
// providers for what moves its last bar. Its scripts run on the bars that are
// over, again when one more is (pineEngine.ts).
//
// Bars of time are of any length WATA makes, from ten seconds to a day: Vela's menu
// offers the usual ones, the length field above the chart takes any other.
//
// Full screen: the chart and its controls over the whole window, and the window over
// the whole screen where the browser allows it. Vela keeps its menus inside its own
// element, and the admin's dialogs and notices are above it.
//
// The only file that builds Vela, as PriceChart.tsx is the only one that builds
// Lightweight Charts.

import { PineWorkerEngine } from '@luxalgo/vela-pinets'
import { VelaWorkspace } from '@luxalgo/vela/workspace'
import { cn, useTheme } from 'ioiti-ui'
import { Maximize2, Minimize2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import velaWorkerUrl from 'virtual:vela-pine-worker'

import { WataBars } from '../data/wataBars'
import type { LibraryResolver } from '../engine/prepare'
import { parseTicker, PROVIDERS, tickerText } from '../engine/tickers'
import type { ProviderId, WataTicker } from '../engine/tickers'
import { DEFAULT_TIMEFRAME, readTimeframe, shortName, TIMEFRAME_HINT, TIMEFRAMES, timeframeOf, unevenNote } from '../engine/timeframes'
import { fullRunEngine } from './pineEngine'
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

/** The length of the chart's bars as typed (45s, 2m, 1h), applied on Enter or when the field is left. */
function LengthField({ timeframe, onApply }: { timeframe: string; onApply: (timeframe: string) => void }) {
  const current = timeframeOf(timeframe)
  const [error, setError] = useState<string | null>(null)
  const apply = (text: string) => {
    const read = readTimeframe(text)
    if ('error' in read) {
      setError(read.error)
      return
    }
    setError(null)
    if (read.timeframe.pine !== current?.pine) onApply(read.timeframe.pine)
  }
  return (
    <label className="flex items-center gap-1.5" title={TIMEFRAME_HINT}>
      <span className="text-muted-foreground">Length</span>
      <input
        type="text"
        defaultValue={current ? shortName(current.seconds) : timeframe}
        aria-label="Length of the bars, as 45s, 2m or 1h"
        aria-invalid={error !== null}
        autoComplete="off"
        spellCheck={false}
        enterKeyHint="done"
        // Its focus as the form's Input shows it: a field that takes text has no ring (ioiti-ui 0.5.3).
        className={cn(
          'w-16 rounded-md border bg-surface px-1.5 py-0.5 font-mono',
          'focus:border-neutral-500 dark:focus:border-[#4A4A4A] accent:focus:border-brand',
          error ? 'border-rose-500/50 focus:border-rose-500' : 'border-border',
        )}
        onKeyDown={(event) => {
          if (event.key === 'Enter') apply(event.currentTarget.value)
        }}
        onBlur={(event) => apply(event.target.value)}
      />
      {error && (
        <span role="alert" className="text-red-700 dark:text-[#FF7A7A]">
          {error}
        </span>
      )}
    </label>
  )
}

/**
 * Whether the chart is shown alone. The page itself is made the browser's full screen, not the chart's
 * element, so that what opens over it (a dialog, a notice) stays in sight; where the browser has no full
 * screen for a page (an iPhone) or refuses it, the chart fills the window. Leaving the browser's full
 * screen (Escape) leaves the chart's too.
 */
function useFullScreen(): [boolean, (full: boolean) => void] {
  const [full, setFull] = useState(false)
  useEffect(() => {
    if (!full) return
    let left = false
    let entered = false
    const root = document.documentElement
    if (root.requestFullscreen && !document.fullscreenElement) {
      root.requestFullscreen().then(
        () => {
          entered = true
          // Left before the browser had answered.
          if (left) void document.exitFullscreen().catch(() => undefined)
        },
        () => undefined,
      )
    }
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false)
    }
    // Only when the browser is not in full screen: there, Escape is its own, and ends in onChange.
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented && !document.fullscreenElement) setFull(false)
    }
    document.addEventListener('fullscreenchange', onChange)
    document.addEventListener('keydown', onKey)
    return () => {
      left = true
      document.removeEventListener('fullscreenchange', onChange)
      document.removeEventListener('keydown', onKey)
      if (entered && document.fullscreenElement === root) void document.exitFullscreen().catch(() => undefined)
    }
  }, [full])
  return [full, setFull]
}

export function TaChart(props: TaChartProps) {
  const { layout, symbol, timeframe, fallbackSymbol, timeZone, resolve, addScript, className } = props
  const host = useRef<HTMLDivElement>(null)
  const workspace = useRef<VelaWorkspace | null>(null)
  const callbacks = useRef(props)
  const { theme } = useTheme()
  const [full, setFull] = useFullScreen()
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
      live: true,
      theme,
      timezone: timeZone,
      timeframes: TIMEFRAMES.map((length) => length.pine),
      providers,
      engines: {
        pine: () =>
          fullRunEngine(new PineWorkerEngine({ workerUrl: velaWorkerUrl }), undefined, (ticker) => bars.growingBar(ticker)),
      },
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
  const length = timeframeOf(market.timeframe)
  const uneven = length ? unevenNote(length.seconds) : null
  const switchTo = (change: Partial<Pick<WataTicker, 'side' | 'rangeTicks'>>) => {
    if (!ticker) return
    workspace.current?.active.setSymbol(velaSymbolOf({ ...ticker, ...change }))
  }
  // Once: Enter, then leaving the field, or the field leaving the page, asks the same length again.
  const lengthTo = (next: string) => {
    const cell = workspace.current?.active
    if (!cell || next === shown.current.timeframe) return
    shown.current = { ...shown.current, timeframe: next }
    cell.setTimeframe(next)
  }

  return (
    <div
      className={cn('flex min-h-0 flex-col gap-2', className, full && 'fixed inset-0 z-40 h-auto bg-background p-3 sm:p-4')}
      data-full-screen={full || undefined}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs" aria-label="Prices of the chart">
        {ticker && (
          <>
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
            {!ticker.rangeTicks && (
              <LengthField
                key={market.timeframe}
                timeframe={market.timeframe}
                onApply={lengthTo}
              />
            )}
            {!ticker.rangeTicks && uneven && (
              <span className="text-muted-foreground">
                {uneven} On this chart a bar's time_close is its open time and its length; the editor's test and the
                backtests use the end of its last ten seconds.
              </span>
            )}
            {ticker.rangeTicks && (
              <label className="flex items-center gap-1.5">
                <span className="text-muted-foreground">R</span>
                <input
                  type="number"
                  min={1}
                  defaultValue={ticker.rangeTicks}
                  key={ticker.rangeTicks}
                  aria-label="Range, in steps of the price"
                  className="w-16 rounded-md border border-border bg-surface px-1.5 py-0.5 font-mono focus:border-neutral-500 dark:focus:border-[#4A4A4A] accent:focus:border-brand"
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
          </>
        )}
        <button
          type="button"
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 hover:bg-surface-hover"
          title={full ? 'Back to the page (Escape)' : 'The chart alone, over the whole screen'}
          onClick={() => setFull(!full)}
        >
          {full ? <Minimize2 className="size-3.5" aria-hidden /> : <Maximize2 className="size-3.5" aria-hidden />}
          {full ? 'Exit full screen' : 'Full screen'}
        </button>
      </div>
      <div ref={host} className="min-h-[480px] flex-1 overflow-hidden rounded-2xl border border-border" data-testid="ta-chart" />
    </div>
  )
}
