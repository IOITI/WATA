// A day of a backtest on Vela: the bars its script ran on (from the engine's restart, with its days of
// history before it), the script drawn as it ran (its plots, fills, shapes, backgrounds and labels, as on
// the Charts page), and the run's signals and trades over it (runMarksEngine.ts), each a row of Vela's
// legend that can be hidden. Not live, nothing saved. A signal rule's page draws a day of the rule with it,
// its journal's signals the second layer (`layer`).
//
// With TaChart.tsx, the only files that build Vela. Its Pine worker is ended with the chart: Vela's
// destroy() leaves an engine's worker running.

import { BarStore, MultiProviderFeed, Vela } from '@luxalgo/vela'
import type { IndicatorHandle } from '@luxalgo/vela'
import { PineWorkerEngine } from '@luxalgo/vela-pinets'
import { Alert, AlertDescription, cn, formatDay, formatNumber, useTheme } from 'ioiti-ui'
import { useEffect, useMemo, useRef, useState } from 'react'
import velaWorkerUrl from 'virtual:vela-pine-worker'

import { barsLabel } from '@/features/ta/signals/ruleModel'
import type { BacktestDayIndicator, BacktestSignal, BacktestTrade } from '@/lib/types'

import { WataBars } from '../data/wataBars'
import { prepareScript } from '../engine/prepare'
import type { LibraryResolver } from '../engine/prepare'
import { PROVIDERS } from '../engine/tickers'
import { dayStartIn, inputOverrides, runLayer, runProvider, runSymbol, visibleRangeOf } from './runDayModel'
import type { LayerOf, RunColors } from './runDayModel'
import { RUN_LANGUAGE, runMarksEngine } from './runMarksEngine'
import { sharedAssets, wataProvider } from './wataProvider'

export interface RunDayChartProps {
  answer: BacktestDayIndicator
  /** The day, in the trading timezone. */
  day: string
  signals: readonly BacktestSignal[]
  trades: readonly BacktestTrade[]
  /** The trade brought into view; the day's bars otherwise. */
  selected: BacktestTrade | null
  timeZone: string
  label: string
  className?: string
  /** What the second layer draws, in place of the run's signals and trades (a rule's page: its journal). */
  layer?: LayerOf
}

/** The second layer: the one given, else the run's signals and trades. */
function layerOf(layer: LayerOf | undefined, signals: readonly BacktestSignal[], trades: readonly BacktestTrade[]): LayerOf {
  return layer ?? ((rows, colors, dayStart) => runLayer(signals, trades, rows, colors, dayStart))
}

/** A token of the theme, as a colour the canvas takes: it knows no CSS variable. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback
}

/** The run's marks in the tones of the day page's chart (PriceChart's palette). */
function runColors(): RunColors {
  return {
    entry: token('--info', '#38bdf8'),
    profit: token('--positive', '#34d399'),
    loss: token('--negative', '#f87171'),
    neutral: token('--text-secondary', '#a1a1a6'),
    warning: token('--warning', '#fbbf24'),
    warmUp: 'rgba(128, 128, 128, 0.12)',
  }
}

function tradeColors() {
  return { long: token('--positive', '#34d399'), short: token('--negative', '#f87171'), exit: token('--info', '#38bdf8') }
}

/** The script's libraries at the versions it ran with, by the names it imports them by. */
function librariesOf(answer: BacktestDayIndicator): LibraryResolver {
  return async (name) => answer.libraries.find((library) => library.name === name) ?? null
}

export function RunDayChart({ answer, day, signals, trades, selected, timeZone, label, className, layer }: RunDayChartProps) {
  const host = useRef<HTMLDivElement>(null)
  const chartRef = useRef<Vela | null>(null)
  const runRef = useRef<IndicatorHandle | null>(null)
  // Once the chart has its bars: a visible range set before them leaves a few bars in view.
  const ready = useRef(false)
  const { theme } = useTheme()
  const [error, setError] = useState<string | null>(null)
  const [others, setOthers] = useState<string[]>([])
  const dayStart = useMemo(() => dayStartIn(day, timeZone), [day, timeZone])
  const overrides = useMemo(
    () => inputOverrides(answer.inputs, answer.inputs_version, answer.script.version),
    [answer.inputs, answer.inputs_version, answer.script.version],
  )
  // What the chart is built with, read when it is: a later change is given to it below.
  const latest = useRef({ selected, theme, signals, trades, layer })
  useEffect(() => {
    latest.current = { selected, theme, signals, trades, layer }
  })

  // Built once for a day: what changes afterwards is given to it below.
  useEffect(() => {
    if (!host.current) return
    const symbol = runSymbol(answer)
    const pine = new PineWorkerEngine({ workerUrl: velaWorkerUrl })
    const feed = new MultiProviderFeed(new BarStore())
    // Another asset a script reads, through the Charts page's providers; its days UTC's, as the engine's.
    const deps = { bars: new WataBars(), assets: sharedAssets(), timeZone: 'UTC' }
    const provider = runProvider(
      answer,
      Object.fromEntries(PROVIDERS.map((name) => [name, wataProvider(name, deps)])),
      (ticker) => setOthers((before) => (before.includes(ticker) ? before : [...before, ticker])),
    )
    for (const name of PROVIDERS) void feed.registerProvider(name, provider)
    const { selected: chosen, theme: shown } = latest.current
    const chart = new Vela(
      host.current,
      {
        symbol,
        timeframe: answer.timeframe,
        visibleRange: visibleRangeOf(answer.bars, dayStart, chosen) ?? undefined,
        live: false,
        drawings: false,
        volume: false,
        currentPriceLine: false,
        theme: shown,
      },
      // A bar cache of its own: the page-wide one could serve the Charts page's bars of the same symbol.
      { dataFeed: feed, engines: [pine, runMarksEngine()] },
    )
    chart.renderer.set('timezone', timeZone)
    chart.renderer.set('tradeMarkers', { qty: false, colors: tradeColors() })
    chartRef.current = chart
    const off = chart.on('indicator:error', ({ error: failed }) => setError(failed.message))
    let cancelled = false
    void chart.ready().then(() => {
      if (cancelled) return
      ready.current = true
      // What is to be shown now: the day, or a trade chosen meanwhile.
      const range = visibleRangeOf(answer.bars, dayStart, latest.current.selected)
      if (range) chart.setVisibleRange(range)
    })
    void (async () => {
      try {
        // The script's symbol info before its one run: the engine's, not a fallback of Vela's.
        await feed.symbolInfoFor(symbol)
        const prepared = await prepareScript(answer.script.source, librariesOf(answer))
        if (cancelled) return
        chart.addIndicator(prepared.source, { id: 'script', inputs: overrides.values })
        const { signals: raised, trades: made, layer: given } = latest.current
        const drawn = layerOf(given, raised, made)(answer.bars, runColors(), dayStart)
        runRef.current = chart.addIndicator(JSON.stringify(drawn), { id: 'run', language: RUN_LANGUAGE, title: drawn.title })
      } catch (failure) {
        if (!cancelled) setError(failure instanceof Error ? failure.message : String(failure))
      }
    })()
    return () => {
      cancelled = true
      ready.current = false
      off()
      chart.destroy()
      pine.terminate()
      chartRef.current = null
      runRef.current = null
    }
  }, [answer, dayStart, timeZone, overrides])

  // Another theme: the chart's colours, and the run's marks in the theme's tones; the script is not run again.
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setTheme(theme)
    chart.renderer.set('tradeMarkers', { colors: tradeColors() })
    void runRef.current?.updateCode(JSON.stringify(layerOf(layer, signals, trades)(answer.bars, runColors(), dayStart)))
  }, [theme, signals, trades, layer, answer.bars, dayStart])

  // A trade chosen below is brought into view, and the day again once it is closed.
  useEffect(() => {
    if (!ready.current) return
    const range = visibleRangeOf(answer.bars, dayStart, selected)
    if (range) chartRef.current?.setVisibleRange(range)
  }, [selected, answer.bars, dayStart])

  const applied = answer.inputs.filter((input) => !overrides.left.includes(input.title ?? input.id))
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      <div
        ref={host}
        role="group"
        aria-label={label}
        className="h-[28rem] overflow-hidden rounded-xl border border-border"
        data-testid="run-day-chart"
      />
      {error && (
        <p role="alert" className="text-sm text-red-700 dark:text-[#FF7A7A]">
          The script did not run on these bars: {error}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {answer.script.name ?? 'The script'}, version {answer.script.version}
        {applied.length > 0
          ? `, with ${applied.map((input) => `${input.title ?? input.var ?? input.id} ${String(input.value)}`).join(', ')}`
          : ', with its own inputs'}
        . On the {formatNumber(answer.bars.length)} bars of {barsLabel(answer.chart.bars)} it ran on: from{' '}
        {formatDay(answer.window.anchor.slice(0, 10))} {answer.restart_time} UTC
        {answer.history_days > 0
          ? `, ${answer.history_days === 1 ? 'a day' : `${answer.history_days} days`} before the engine's restart of the day,`
          : ", the engine's restart of the day,"}{' '}
        to its next restart; the shaded part comes before the day.
      </p>
      {(answer.notes.length > 0 || overrides.left.length > 0 || others.length > 0) && (
        <Alert tone="warning">
          <AlertDescription>
            <ul className="list-disc pl-4">
              {answer.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
              {overrides.left.length > 0 && (
                <li>Not in this version of the script: {overrides.left.join(', ')}; drawn with its own values.</li>
              )}
              {others.length > 0 && (
                <li>
                  The script reads {others.join(', ')} too: read from the day&apos;s start, not from the engine&apos;s
                  restart as the run read it.
                </li>
              )}
            </ul>
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
