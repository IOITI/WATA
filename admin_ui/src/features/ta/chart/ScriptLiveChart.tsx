// One of the operator's scripts on a chart of WATA's bars as it is being written (its page's Chart tab): built once
// for an asset and its bars, then each draft the page compiles is given to it in place (Vela's updateCode), the
// drawing of the last draft that ran kept while a newer one fails. Not live, nothing saved, the script's own inputs.
//
// With TaChart.tsx, RunDayChart.tsx and IndicatorPreviewChart.tsx, the only files that build Vela. Its Pine worker
// is ended with the chart: Vela's destroy() leaves an engine's worker running.

import { BarStore, MultiProviderFeed, Vela } from '@luxalgo/vela'
import type { IndicatorHandle } from '@luxalgo/vela'
import { PineWorkerEngine } from '@luxalgo/vela-pinets'
import { cn, useTheme } from 'ioiti-ui'
import { useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import velaWorkerUrl from 'virtual:vela-pine-worker'

import type { ChartSnapshot } from '@/features/prices/chartSnapshot'

import { WataBars } from '../data/wataBars'
import { PROVIDERS } from '../engine/tickers'
import { sharedAssets, wataProvider } from './wataProvider'

export interface ScriptLiveChartProps {
  /** The draft with its libraries inlined, as the page's check prepared it once it compiled; null before one did. */
  source: string | null
  name: string
  /** Vela's symbol (`capital:CAPITAL_US100.R60`) and Pine's timeframe (`1`). */
  symbol: string
  timeframe: string
  timeZone: string
  className?: string
  /** Given what the chart shows while it is on screen: "Take as picture" takes it. */
  snapshotRef?: RefObject<ChartSnapshot | null>
}

/** What became of the newest source given: drawn, being drawn, or why it was not (the one before still drawn). */
type Run = { state: 'running' } | { state: 'drawn' } | { state: 'failed'; error: string; kept: boolean }

export function ScriptLiveChart({ source, name, symbol, timeframe, timeZone, className, snapshotRef }: ScriptLiveChartProps) {
  const host = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Vela | null>(null)
  const handleRef = useRef<IndicatorHandle | null>(null)
  // Whether a draft was given in place since the chart was built: its outcome is updateCode's, not the chart's event.
  const replaced = useRef(false)
  // The chart can take the script: its symbol's info read, the provider's and not a fallback of Vela's.
  const readyRef = useRef(false)
  const { theme } = useTheme()
  const [run, setRun] = useState<Run | null>(null)
  // What the chart is built with, read when it is: a later change is given to it below.
  const latest = useRef({ source, name, theme })
  useEffect(() => {
    latest.current = { source, name, theme }
  })

  /** The script given to the chart: added the first time, replaced in place since (only the newest call counts). */
  const give = useRef((chart: Vela, given: string) => {
    const handle = handleRef.current
    if (!handle) {
      handleRef.current = chart.addIndicator(given, { id: 'script', title: latest.current.name })
      // A failure of this first run comes as the chart's indicator:error.
      setRun({ state: 'drawn' })
      return
    }
    replaced.current = true
    setRun({ state: 'running' })
    void handle.updateCode(given).then((result) => {
      if (chartRef.current !== chart || latest.current.source !== given) return
      setRun(result.ok ? { state: 'drawn' } : { state: 'failed', error: result.error?.message ?? 'it did not run', kept: true })
    })
  })

  // Built again for another asset or length of bars.
  useEffect(() => {
    if (!host.current) return
    setRun(null)
    const pine = new PineWorkerEngine({ workerUrl: velaWorkerUrl })
    const feed = new MultiProviderFeed(new BarStore())
    const deps = { bars: new WataBars(), assets: sharedAssets(), timeZone }
    for (const provider of PROVIDERS) void feed.registerProvider(provider, wataProvider(provider, deps))
    const chart = new Vela(
      host.current,
      { symbol, timeframe, live: false, drawings: false, volume: false, currentPriceLine: false, theme: latest.current.theme },
      { dataFeed: feed, engines: [pine] },
    )
    chart.renderer.set('timezone', timeZone)
    chartRef.current = chart
    // The first run's failure: a draft given in place since says its own (updateCode).
    const off = chart.on('indicator:error', ({ error }) => {
      if (!replaced.current) setRun({ state: 'failed', error: error.message, kept: false })
    })
    let cancelled = false
    void (async () => {
      try {
        await feed.symbolInfoFor(symbol)
      } catch {
        // Vela asks again when it loads the bars: the script runs on what it gets.
      }
      if (cancelled) return
      readyRef.current = true
      if (latest.current.source !== null) give.current(chart, latest.current.source)
    })()
    return () => {
      cancelled = true
      readyRef.current = false
      off()
      chart.destroy()
      pine.terminate()
      chartRef.current = null
      handleRef.current = null
      replaced.current = false
    }
  }, [symbol, timeframe, timeZone])

  // A new draft compiled: given to the chart, once it can take it (until then, the build gives the newest).
  useEffect(() => {
    const chart = chartRef.current
    if (chart && readyRef.current && source !== null) give.current(chart, source)
  }, [source])

  // Its picture as Vela composes it: its background filled, the script's legend in it.
  useEffect(() => {
    if (!snapshotRef) return
    snapshotRef.current = () => chartRef.current?.renderer.screenshotCanvas() ?? null
    return () => {
      snapshotRef.current = null
    }
  }, [snapshotRef])

  // Another theme: the chart's colours; the script is not run again.
  useEffect(() => {
    chartRef.current?.setTheme(theme)
  }, [theme])

  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        ref={host}
        role="group"
        aria-label={`${name} on a chart of WATA's bars`}
        aria-busy={run?.state === 'running'}
        className="h-80 overflow-hidden rounded-xl border border-border sm:h-[28rem]"
        data-testid="script-live-chart"
      />
      {run?.state === 'failed' && (
        <p role="alert" className="text-sm text-negative">
          {run.kept
            ? `The newest draft did not run: ${run.error}. The chart keeps the last one that did.`
            : `It did not run on these bars: ${run.error}`}
        </p>
      )}
    </div>
  )
}
