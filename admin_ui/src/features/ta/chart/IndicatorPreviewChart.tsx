// One of LuxAlgo's indicators on a chart of WATA's own bars, run from its source in Vela's Pine worker: nothing taken
// or saved, and the worker opens no connection (its policy, src/admin/app.py). Not live: the bars the Charts page's
// providers give of the asset, the indicator with its own inputs.
//
// With TaChart.tsx and RunDayChart.tsx, the only files that build Vela. Its Pine worker is ended with the chart:
// Vela's destroy() leaves an engine's worker running.

import { BarStore, MultiProviderFeed, Vela } from '@luxalgo/vela'
import { PineWorkerEngine } from '@luxalgo/vela-pinets'
import { cn, useTheme } from 'ioiti-ui'
import { useEffect, useRef, useState } from 'react'
import velaWorkerUrl from 'virtual:vela-pine-worker'

import { WataBars } from '../data/wataBars'
import { prepareScript } from '../engine/prepare'
import { PROVIDERS } from '../engine/tickers'
import { sharedAssets, wataProvider } from './wataProvider'

export interface IndicatorPreviewChartProps {
  /** The indicator's whole source, as LuxAlgo gives it. */
  source: string
  name: string
  /** Vela's symbol (`capital:CAPITAL_US100`) and Pine's timeframe (`5`). */
  symbol: string
  timeframe: string
  timeZone: string
  className?: string
}

export function IndicatorPreviewChart({ source, name, symbol, timeframe, timeZone, className }: IndicatorPreviewChartProps) {
  const host = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Vela | null>(null)
  const { theme } = useTheme()
  const [error, setError] = useState<string | null>(null)
  // The theme the chart is built with, read when it is: a later change is given to it below.
  const shown = useRef(theme)
  useEffect(() => {
    shown.current = theme
  })

  // Built again for another source, asset or length of bars.
  useEffect(() => {
    if (!host.current) return
    setError(null)
    const pine = new PineWorkerEngine({ workerUrl: velaWorkerUrl })
    const feed = new MultiProviderFeed(new BarStore())
    const deps = { bars: new WataBars(), assets: sharedAssets(), timeZone }
    for (const provider of PROVIDERS) void feed.registerProvider(provider, wataProvider(provider, deps))
    const chart = new Vela(
      host.current,
      { symbol, timeframe, live: false, drawings: false, volume: false, currentPriceLine: false, theme: shown.current },
      { dataFeed: feed, engines: [pine] },
    )
    chart.renderer.set('timezone', timeZone)
    chartRef.current = chart
    const off = chart.on('indicator:error', ({ error: failed }) => setError(failed.message))
    let cancelled = false
    void (async () => {
      try {
        // The symbol's info before the indicator's one run: the provider's, not a fallback of Vela's.
        await feed.symbolInfoFor(symbol)
        // LuxAlgo's indicators come alone: a library one imports is none WATA holds, and is said so.
        const prepared = await prepareScript(source, async () => null)
        if (cancelled) return
        chart.addIndicator(prepared.source, { id: 'luxalgo', title: name })
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure))
      }
    })()
    return () => {
      cancelled = true
      off()
      chart.destroy()
      pine.terminate()
      chartRef.current = null
    }
  }, [source, name, symbol, timeframe, timeZone])

  // Another theme: the chart's colours; the indicator is not run again.
  useEffect(() => {
    chartRef.current?.setTheme(theme)
  }, [theme])

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        ref={host}
        role="group"
        aria-label={`${name} on a chart of WATA's bars`}
        className="h-72 overflow-hidden rounded-xl border border-border sm:h-[28rem]"
        data-testid="indicator-preview-chart"
      />
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-[#FF7A7A]">
          It did not run on these bars: {error}
        </p>
      )}
    </div>
  )
}
