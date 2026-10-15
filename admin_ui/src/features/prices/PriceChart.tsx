import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
} from 'lightweight-charts'
import type {
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  MouseEventParams,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import { useCallback, useEffect, useRef } from 'react'

import { cn } from '@/lib/utils'

import { withAlpha } from './chartData'
import type { Candle, ChartMarker, ChartOverlay, EmptyPoint, LinePoint, MarkerTone } from './chartData'
import type { LiveChart, LiveUpdate } from './liveTail'

/**
 * The price chart. The only file that knows the charting library: what it is
 * given is already in chart time (see chartData.ts), and it only draws.
 *
 * The chart is made once and kept; new data goes into its series. Making it
 * again at each refresh would lose the zoom.
 */

/** A token of the theme, as a colour the canvas takes: it knows no CSS variable. */
function token(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return /^#[0-9a-f]{6}$/i.test(value) ? value : fallback
}

interface Palette {
  text: string
  grid: string
  axis: string
  up: string
  down: string
  line: string
  tones: Record<MarkerTone, string>
}

function readPalette(): Palette {
  return {
    text: token('--text-secondary', '#a1a1a6'),
    grid: token('--chart-grid', '#27272a'),
    axis: token('--chart-axis', '#3f3f46'),
    up: token('--chart-positive', '#059669'),
    down: token('--chart-negative', '#ef4444'),
    line: token('--chart-series', '#3b82f6'),
    // A marker is mostly its label: the steps made for text, not the ones for filled marks.
    tones: {
      entry: token('--info', '#38bdf8'),
      profit: token('--positive', '#34d399'),
      loss: token('--negative', '#f87171'),
      neutral: token('--text-secondary', '#a1a1a6'),
      warning: token('--warning', '#fbbf24'),
    },
  }
}

/** The library brands its times; ours are plain seconds, already shifted to the trading timezone. */
function timed<T extends { time: number }>(points: readonly T[]): (Omit<T, 'time'> & { time: UTCTimestamp })[] {
  return points as unknown as (Omit<T, 'time'> & { time: UTCTimestamp })[]
}

function seriesMarkers(markers: readonly ChartMarker[], tones: Record<MarkerTone, string>): SeriesMarker<Time>[] {
  return markers.map((marker) => ({ ...marker, time: marker.time as UTCTimestamp, color: tones[marker.tone] }))
}

function priceFormat(decimals: number) {
  return { type: 'price' as const, precision: decimals, minMove: 1 / 10 ** decimals }
}

/** How a line over the prices is drawn: its place among them is told by more than its colour. */
function overlayOptions(tone: ChartOverlay['tone'], colors: Palette) {
  const common = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }
  if (tone === 'trend') return { ...common, color: colors.text, lineWidth: 2 as const }
  if (tone === 'reference') return { ...common, color: colors.line, lineWidth: 2 as const }
  return { ...common, color: withAlpha(colors.line, 0.6), lineWidth: 1 as const, lineStyle: LineStyle.Dashed }
}

/** A trend takes the colour of its direction at each point; the other lines keep their own. */
function overlayData(overlay: ChartOverlay, colors: Palette) {
  if (overlay.tone !== 'trend') return overlay.points
  return overlay.points.map((point) =>
    'rising' in point && point.rising !== undefined
      ? { time: point.time, value: point.value, color: point.rising ? colors.up : colors.down }
      : point,
  )
}

// How much taller the prices are than the index under them.
const MAIN_PANE_SHARE = 3
const INDEX_PANE = 1
// The volume takes the bottom fifth of the prices' panel, on a scale of its own.
const VOLUME_SCALE = 'volume'
const VOLUME_TOP_MARGIN = 0.8

/** A point given to `update`: a time before the last one drawn is refused by the library, and left to the next read. */
function updated(series: ISeriesApi<'Candlestick'> | ISeriesApi<'Histogram'> | null, point: Candle | LinePoint) {
  if (!series) return
  try {
    series.update(point as never)
  } catch {
    // The bars read next put it right.
  }
}

export interface PriceChartProps {
  candles: readonly (Candle | EmptyPoint)[]
  markers: readonly ChartMarker[]
  /** Decimals the prices are shown with. */
  decimals: number
  /** The index of a turbo's indice: a line in a second panel, on the same time axis. */
  indexLine?: readonly (LinePoint | EmptyPoint)[]
  indexMarkers?: readonly ChartMarker[]
  indexDecimals?: number
  /** Lines over the prices, on their scale: what was measured on them. */
  overlays?: readonly ChartOverlay[]
  /** The quote changes of each bar, along the bottom of the prices: what stands for volume. */
  volume?: readonly (LinePoint | EmptyPoint)[]
  /** The last bars moving with the stream, drawn as they come without the page being drawn again. */
  live?: LiveChart | null
  /** Bars shorter than a minute: the time axis then shows the seconds. */
  showSeconds: boolean
  /** Changes when another view is shown: the whole period comes back into view. A refresh keeps the zoom. */
  viewKey: string
  /** Chart times to bring into view in place of the whole period: one trade of a day, say. */
  focus?: { from: number; to: number } | null
  /** The chart time under the pointer; null when it leaves the chart. */
  onHover: (time: number | null) => void
  label: string
  className?: string
}

export function PriceChart({
  candles,
  markers,
  decimals,
  indexLine,
  indexMarkers,
  indexDecimals = 2,
  overlays,
  volume,
  live,
  showSeconds,
  viewKey,
  focus,
  onHover,
  label,
  className,
}: PriceChartProps) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const palette = useRef<Palette | null>(null)
  const candleSeries = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const candleMarkers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const lineSeries = useRef<ISeriesApi<'Line'> | null>(null)
  const lineMarkers = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const overlaySeries = useRef(new Map<string, ISeriesApi<'Line'>>())
  const volumeSeries = useRef<ISeriesApi<'Histogram'> | null>(null)
  const liveChart = useRef(live)
  const hover = useRef(onHover)

  useEffect(() => {
    hover.current = onHover
  }, [onHover])

  useEffect(() => {
    const element = container.current
    if (!element) return
    const colors = readPalette()

    const created = createChart(element, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: colors.text,
        fontFamily: getComputedStyle(document.body).fontFamily || 'sans-serif',
        fontSize: 11,
        // The library's licence asks for a link to TradingView on the page: its logo is one.
        attributionLogo: true,
        panes: { enableResize: false, separatorColor: colors.axis },
      },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      // The horizontal line follows the pointer, not the nearest close: any level can be read off the scale.
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: colors.axis },
      timeScale: {
        borderColor: colors.axis,
        timeVisible: true,
        rightOffset: 2,
        // A day of ten-second bars is five thousand of them: let them all fit the width.
        minBarSpacing: 0.02,
      },
      // A finger moving up or down on the chart scrolls the page.
      handleScroll: { vertTouchDrag: false },
      localization: { locale: 'en-GB', dateFormat: 'dd MMM' },
    })
    // Up is hollow and down is filled: the direction of a bar does not rest on its colour alone.
    const series = created.addSeries(CandlestickSeries, {
      upColor: withAlpha(colors.up, 0.25),
      borderUpColor: colors.up,
      wickUpColor: colors.up,
      downColor: colors.down,
      borderDownColor: colors.down,
      wickDownColor: colors.down,
      borderVisible: true,
    })
    const onCrosshairMove = (event: MouseEventParams<Time>) => {
      hover.current(typeof event.time === 'number' ? event.time : null)
    }
    created.subscribeCrosshairMove(onCrosshairMove)

    chart.current = created
    palette.current = colors
    candleSeries.current = series
    candleMarkers.current = createSeriesMarkers(series, [])
    const lines = overlaySeries.current

    return () => {
      created.unsubscribeCrosshairMove(onCrosshairMove)
      created.remove()
      chart.current = null
      candleSeries.current = null
      candleMarkers.current = null
      lineSeries.current = null
      lineMarkers.current = null
      volumeSeries.current = null
      lines.clear()
    }
  }, [])

  const drawLive = useCallback((update: LiveUpdate) => {
    for (const candle of update.candles) updated(candleSeries.current, candle)
    for (const point of update.volume) updated(volumeSeries.current, point)
  }, [])

  useEffect(() => {
    chart.current?.timeScale().applyOptions({ secondsVisible: showSeconds })
  }, [showSeconds])

  // The data of both panels goes in here, in one place and in this order.
  useEffect(() => {
    const created = chart.current
    const colors = palette.current
    const prices = candleSeries.current
    if (!created || !colors || !prices) return

    if (!indexLine && lineSeries.current) {
      // Removing its only series removes the panel with it.
      created.removeSeries(lineSeries.current)
      lineSeries.current = null
      lineMarkers.current = null
    }
    if (indexLine && !lineSeries.current) {
      // Emptied before a second series joins. Lightweight Charts 5.2.1, given the same
      // times again while it holds a single series, rebuilds one of its two indexes of
      // the time points and keeps the other as it was (DataLayer.setSeriesData, "if
      // there is only 1 series"). Nothing shows until a second series arrives; from
      // then on each new set of bars lands at the wrong place, or nowhere. Showing an
      // index and then one of its turbos is that case exactly: same day, same times.
      // Emptying the series puts the two indexes back in step.
      prices.setData([])
      const series = created.addSeries(
        LineSeries,
        { color: colors.line, lineWidth: 2, priceLineVisible: false, crosshairMarkerRadius: 3 },
        INDEX_PANE,
      )
      lineSeries.current = series
      lineMarkers.current = createSeriesMarkers(series, [])
      const [main, index] = created.panes()
      main?.setStretchFactor(MAIN_PANE_SHARE)
      index?.setStretchFactor(1)
    }

    // The lines over the prices: those no longer asked for go, the new ones join.
    const lines = overlaySeries.current
    const alone = () => lines.size === 0 && !lineSeries.current && !volumeSeries.current
    const wanted = new Map((overlays ?? []).map((overlay) => [overlay.id, overlay]))
    for (const [id, line] of lines) {
      if (wanted.has(id)) continue
      created.removeSeries(line)
      lines.delete(id)
    }
    for (const [id, overlay] of wanted) {
      if (lines.has(id)) continue
      // A second series joining the prices: emptied first, for the same reason as above.
      if (alone()) prices.setData([])
      lines.set(id, created.addSeries(LineSeries, overlayOptions(overlay.tone, colors)))
    }

    // The volume, along the bottom of the prices on a scale of its own.
    if (!volume && volumeSeries.current) {
      created.removeSeries(volumeSeries.current)
      volumeSeries.current = null
    }
    if (volume && !volumeSeries.current) {
      if (alone()) prices.setData([])
      const series = created.addSeries(HistogramSeries, {
        priceScaleId: VOLUME_SCALE,
        priceFormat: { type: 'volume' },
        color: withAlpha(colors.line, 0.35),
        priceLineVisible: false,
        lastValueVisible: false,
      })
      series.priceScale().applyOptions({ scaleMargins: { top: VOLUME_TOP_MARGIN, bottom: 0 } })
      volumeSeries.current = series
    }

    prices.setData(timed(candles))
    if (indexLine) lineSeries.current?.setData(timed(indexLine))
    for (const [id, line] of lines) {
      const overlay = wanted.get(id)
      if (overlay) line.setData(timed(overlayData(overlay, colors)))
    }
    if (volume) volumeSeries.current?.setData(timed(volume))
    // The bars read are older than what the stream moved since: put back on them.
    const moving = liveChart.current?.current()
    if (moving) drawLive(moving)
  }, [candles, indexLine, overlays, volume, drawLive])

  // What the stream moves, drawn as it comes: no render of the page for a quote.
  useEffect(() => {
    liveChart.current = live
    if (!live) return
    const moving = live.current()
    if (moving) drawLive(moving)
    return live.subscribe(drawLive)
  }, [live, drawLive])

  // A change of format makes the library lay the whole chart out again: only when it does change.
  useEffect(() => {
    candleSeries.current?.applyOptions({ priceFormat: priceFormat(decimals) })
  }, [decimals])

  const hasIndex = indexLine !== undefined
  useEffect(() => {
    if (hasIndex) lineSeries.current?.applyOptions({ priceFormat: priceFormat(indexDecimals) })
  }, [hasIndex, indexDecimals])

  // After the bars they sit on.
  useEffect(() => {
    const colors = palette.current
    if (!colors) return
    candleMarkers.current?.setMarkers(seriesMarkers(markers, colors.tones))
    lineMarkers.current?.setMarkers(seriesMarkers(indexMarkers ?? [], colors.tones))
  }, [markers, indexMarkers, hasIndex])

  // After the data of the new view went in (effects run in order).
  const focusFrom = focus?.from
  const focusTo = focus?.to
  useEffect(() => {
    const scale = chart.current?.timeScale()
    if (!scale) return
    if (focusFrom === undefined || focusTo === undefined) {
      scale.fitContent()
      return
    }
    try {
      scale.setVisibleRange({ from: focusFrom as Time, to: focusTo as Time })
    } catch {
      // Times the chart has no bar for: the whole period then.
      scale.fitContent()
    }
  }, [viewKey, focusFrom, focusTo])

  // Not an image: the chart's link to TradingView stays reachable inside it.
  return <div ref={container} role="group" aria-label={label} className={cn('w-full', className)} />
}
