import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charts, resetCharts, seriesOf, theChart } from '@/test/fakeCharts'

import type { Candle, ChartMarker, ChartOverlay, LinePoint } from './chartData'
import type { LiveChart, LiveUpdate } from './liveTail'
import { PriceChart } from './PriceChart'
import type { PriceChartProps } from './PriceChart'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

const candle = (time: number, open = 8.4, close = 8.45): Candle => ({ time, open, high: 8.52, low: 8.36, close })
const point = (time: number, value = 25000): LinePoint => ({ time, value })

const CANDLES = [candle(100), candle(110), candle(120)]
const LINE = [point(100), point(110), point(120)]

function props(changes: Partial<PriceChartProps> = {}): PriceChartProps {
  return {
    candles: CANDLES,
    markers: [],
    decimals: 2,
    showSeconds: true,
    viewKey: 'a',
    onHover: () => {},
    label: 'Bid of Long 24,150 · SG',
    ...changes,
  }
}

beforeEach(resetCharts)

describe('the price chart', () => {
  it('draws the candles it is given, hollow when the price rose and filled when it fell', () => {
    render(<PriceChart {...props()} />)

    const candles = seriesOf(theChart(), 'Candlestick')
    expect(candles?.data).toEqual(CANDLES)
    expect(candles?.options).toMatchObject({
      upColor: 'rgba(5, 150, 105, 0.25)',
      borderUpColor: '#059669',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
    })
    expect(candles?.options.priceFormat).toEqual({ type: 'price', precision: 2, minMove: 0.01 })
  })

  it('shows prices with the decimals asked for', () => {
    render(<PriceChart {...props({ decimals: 3 })} />)

    expect(seriesOf(theChart(), 'Candlestick')?.options.priceFormat).toEqual({ type: 'price', precision: 3, minMove: 0.001 })
  })

  it('keeps the link to TradingView that the licence asks for', () => {
    render(<PriceChart {...props()} />)

    expect(theChart().options.layout.attributionLogo).toBe(true)
  })

  it('leaves vertical swipes to the page, so a phone can scroll past the chart', () => {
    render(<PriceChart {...props()} />)

    expect(theChart().options.handleScroll).toEqual({ vertTouchDrag: false })
  })

  it('shows seconds on the time axis only for bars shorter than a minute', () => {
    const { rerender } = render(<PriceChart {...props({ showSeconds: true })} />)
    expect(theChart().timeScaleOptions.secondsVisible).toBe(true)

    rerender(<PriceChart {...props({ showSeconds: false })} />)
    expect(theChart().timeScaleOptions.secondsVisible).toBe(false)
  })

  it('keeps the same chart when the bars are refreshed, and does not undo the zoom', () => {
    const { rerender } = render(<PriceChart {...props()} />)
    const chart = theChart()
    expect(chart.fits).toBe(1)

    const refreshed = [...CANDLES, candle(130)]
    rerender(<PriceChart {...props({ candles: refreshed })} />)

    expect(charts).toHaveLength(1)
    expect(seriesOf(chart, 'Candlestick')?.data).toEqual(refreshed)
    expect(chart.fits).toBe(1)
  })

  it('brings the whole period back into view when another view is shown, after its bars went in', () => {
    const { rerender } = render(<PriceChart {...props()} />)
    const chart = theChart()
    chart.calls.length = 0

    rerender(<PriceChart {...props({ candles: [candle(500), candle(560)], viewKey: 'b' })} />)

    expect(chart.calls).toEqual(['Candlestick data 2', 'fit'])
  })

  it('is removed with the page', () => {
    const { unmount } = render(<PriceChart {...props()} />)
    const chart = theChart()

    unmount()

    expect(chart.removed).toBe(true)
  })
})

describe('the index under a turbo', () => {
  it('is a line in a second panel', () => {
    render(<PriceChart {...props({ indexLine: LINE, indexDecimals: 2 })} />)

    const line = seriesOf(theChart(), 'Line')
    expect(line?.pane).toBe(1)
    expect(line?.data).toEqual(LINE)
    expect(line?.options).toMatchObject({ color: '#3b82f6', priceLineVisible: false })
  })

  it('is not there for an index shown by itself', () => {
    render(<PriceChart {...props()} />)

    expect(seriesOf(theChart(), 'Line')).toBeUndefined()
  })

  // The library loses track of its time points when a second series joins one that was
  // given the same times twice: see the note in PriceChart.tsx.
  it('joins a chart whose prices were emptied first, then filled again', () => {
    const { rerender } = render(<PriceChart {...props()} />)
    const chart = theChart()
    chart.calls.length = 0

    rerender(<PriceChart {...props({ indexLine: LINE, viewKey: 'turbo' })} />)

    expect(chart.calls).toEqual(['Candlestick data 0', 'add Line', 'Candlestick data 3', 'Line data 3', 'fit'])
  })

  it('does not empty the prices at each refresh once it is there', () => {
    const { rerender } = render(<PriceChart {...props({ indexLine: LINE })} />)
    const chart = theChart()
    chart.calls.length = 0

    rerender(<PriceChart {...props({ candles: [...CANDLES, candle(130)], indexLine: [...LINE, point(130)] })} />)

    expect(chart.calls).toEqual(['Candlestick data 4', 'Line data 4'])
  })

  it('goes away, with its panel, when an index is shown again', () => {
    const { rerender } = render(<PriceChart {...props({ indexLine: LINE })} />)
    const chart = theChart()

    rerender(<PriceChart {...props({ viewKey: 'index' })} />)

    expect(seriesOf(chart, 'Line')).toBeUndefined()
    expect(chart.series.filter((series) => series.type === 'Line' && series.removed)).toHaveLength(1)
    expect(seriesOf(chart, 'Candlestick')?.data).toEqual(CANDLES)
  })

  it('comes back after having gone', () => {
    const { rerender } = render(<PriceChart {...props({ indexLine: LINE })} />)
    rerender(<PriceChart {...props()} />)
    rerender(<PriceChart {...props({ indexLine: LINE })} />)

    expect(seriesOf(theChart(), 'Line')?.data).toEqual(LINE)
  })
})

describe('markers', () => {
  const bought: ChartMarker = { id: 'in', time: 100, position: 'atPriceBottom', price: 8.42, shape: 'arrowUp', tone: 'entry', text: 'Buy €8.42' }
  const sold: ChartMarker = { id: 'out', time: 120, position: 'atPriceTop', price: 8.3, shape: 'arrowDown', tone: 'loss', text: '−1.43%' }
  const signal: ChartMarker = { id: 's', time: 110, position: 'aboveBar', shape: 'circle', tone: 'warning', text: 'Long refused' }

  it('go on the prices in the colour of what they say, at their price', () => {
    render(<PriceChart {...props({ markers: [bought, sold] })} />)

    expect(seriesOf(theChart(), 'Candlestick')?.markers).toEqual([
      { ...bought, color: '#38bdf8' },
      { ...sold, color: '#f87171' },
    ])
  })

  it('of the index go on its line', () => {
    render(<PriceChart {...props({ markers: [bought], indexLine: LINE, indexMarkers: [signal] })} />)

    expect(seriesOf(theChart(), 'Line')?.markers).toEqual([{ ...signal, color: '#fbbf24' }])
    expect(seriesOf(theChart(), 'Candlestick')?.markers).toHaveLength(1)
  })

  it('are still there after the index joined and the prices were emptied for it', () => {
    const { rerender } = render(<PriceChart {...props({ markers: [bought] })} />)

    rerender(<PriceChart {...props({ markers: [bought], indexLine: LINE, indexMarkers: [signal], viewKey: 'turbo' })} />)

    expect(seriesOf(theChart(), 'Candlestick')?.markers).toHaveLength(1)
    expect(seriesOf(theChart(), 'Line')?.markers).toHaveLength(1)
  })

  it('arrive after the bars without the bars being set again', () => {
    const { rerender } = render(<PriceChart {...props()} />)
    const chart = theChart()
    chart.calls.length = 0

    rerender(<PriceChart {...props({ markers: [bought] })} />)

    expect(chart.calls).toEqual([])
    expect(seriesOf(chart, 'Candlestick')?.markers).toHaveLength(1)
  })
})

describe('lines over the prices', () => {
  const VWAP: ChartOverlay = { id: 'vwap', tone: 'reference', points: [point(100, 8.4), point(110, 8.41), point(120, 8.42)] }
  const BAND: ChartOverlay = { id: 'upper', tone: 'band', points: [point(100, 8.5), point(110, 8.5), point(120, 8.5)] }
  const KERNEL: ChartOverlay = {
    id: 'kernel',
    tone: 'trend',
    points: [{ time: 100 }, { time: 110, value: 8.43, rising: true }, { time: 120, value: 8.41, rising: false }],
  }
  const lines = () => theChart().series.filter((series) => series.type === 'Line' && !series.removed)

  it('are drawn in the panel of the prices, each with its points', () => {
    render(<PriceChart {...props({ overlays: [BAND, VWAP] })} />)

    expect(lines().map((line) => [line.pane, line.data])).toEqual([
      [0, BAND.points],
      [0, VWAP.points],
    ])
    // The prices keep their own panel to themselves: no second one is made.
    expect(seriesOf(theChart(), 'Candlestick')?.data).toEqual(CANDLES)
  })

  it('are told apart by more than their colour: a reference is thick, a band thin and dashed', () => {
    render(<PriceChart {...props({ overlays: [BAND, VWAP] })} />)
    const [band, vwap] = lines()

    expect(vwap.options).toMatchObject({ color: '#3b82f6', lineWidth: 2 })
    expect(band.options).toMatchObject({ lineWidth: 1, lineStyle: 2 })
    // Four more labels on the price scale would bury the last price.
    expect(vwap.options).toMatchObject({ lastValueVisible: false, priceLineVisible: false })
  })

  it('take the colour of their direction at each point when they are a trend', () => {
    render(<PriceChart {...props({ overlays: [KERNEL] })} />)

    expect(lines()[0].data).toEqual([
      { time: 100 },
      { time: 110, value: 8.43, color: '#059669' },
      { time: 120, value: 8.41, color: '#ef4444' },
    ])
  })

  it('join prices that were emptied first, then filled again', () => {
    const { rerender } = render(<PriceChart {...props()} />)
    theChart().calls.length = 0

    rerender(<PriceChart {...props({ overlays: [VWAP, BAND] })} />)

    // Emptied once, for the first line that joins; the second finds a chart of two series already.
    expect(theChart().calls).toEqual(['Candlestick data 0', 'add Line', 'add Line', 'Candlestick data 3', 'Line data 3', 'Line data 3'])
  })

  it('are given their new points at a refresh without being made again', () => {
    const { rerender } = render(<PriceChart {...props({ overlays: [VWAP] })} />)
    theChart().calls.length = 0
    const moved = { ...VWAP, points: [point(100, 8.4), point(110, 8.44)] }

    rerender(<PriceChart {...props({ overlays: [moved] })} />)

    expect(theChart().calls).toEqual(['Candlestick data 3', 'Line data 2'])
    expect(lines()).toHaveLength(1)
  })

  it('go away when they are no longer asked for', () => {
    const { rerender } = render(<PriceChart {...props({ overlays: [VWAP, BAND] })} />)

    rerender(<PriceChart {...props({ overlays: [VWAP] })} />)

    expect(lines().map((line) => line.data)).toEqual([VWAP.points])
    expect(theChart().series.filter((series) => series.removed)).toHaveLength(1)
  })
})

describe('the pointer', () => {
  it('reports the time it is on, and that it left', () => {
    const onHover = vi.fn()
    render(<PriceChart {...props({ onHover })} />)

    theChart().hover(110)
    theChart().hover(undefined)

    expect(onHover.mock.calls).toEqual([[110], [null]])
  })

  it('reports to whoever listens now, without the chart being made again', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = render(<PriceChart {...props({ onHover: first })} />)

    rerender(<PriceChart {...props({ onHover: second })} />)
    theChart().hover(110)

    expect(charts).toHaveLength(1)
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledWith(110)
  })
})

describe('the volume', () => {
  const VOLUME = [point(100, 3), point(110, 0), point(120, 7)]

  it('is drawn along the bottom of the prices, on a scale of its own, and taken away when not asked for', () => {
    const { rerender } = render(<PriceChart {...props({ volume: VOLUME })} />)

    const chart = theChart()
    const volume = seriesOf(chart, 'Histogram')
    expect(volume?.data).toEqual(VOLUME)
    expect(volume?.options.priceScaleId).toBe('volume')
    expect(volume?.scaleOptions).toEqual({ scaleMargins: { top: 0.8, bottom: 0 } })
    // A second series joins the prices only after they were emptied (Lightweight Charts 5.2.1).
    expect(chart.calls.slice(0, 3)).toEqual(['add Candlestick', 'Candlestick data 0', 'add Histogram'])
    expect(seriesOf(chart, 'Candlestick')?.data).toEqual(CANDLES)

    rerender(<PriceChart {...props()} />)
    expect(seriesOf(chart, 'Histogram')).toBeUndefined()
  })
})

describe('the stream', () => {
  function channel() {
    let listener: ((update: LiveUpdate) => void) | null = null
    let current: LiveUpdate | null = null
    const live: LiveChart = {
      current: () => current,
      subscribe: (next) => {
        listener = next
        return () => {
          listener = null
        }
      },
    }
    return {
      live,
      send(update: LiveUpdate) {
        current = update
        listener?.(update)
      },
      get listening() {
        return listener !== null
      },
    }
  }

  it('moves the last candles as it sends them, without the bars being set again', () => {
    const stream = channel()
    render(<PriceChart {...props({ volume: [point(120, 4)], live: stream.live })} />)
    const chart = theChart()
    const settings = chart.calls.filter((call) => call.endsWith('data 3')).length

    stream.send({ candles: [candle(120, 8.4, 8.5), candle(130, 8.5, 8.55)], volume: [point(120, 5), point(130, 1)] })

    expect(seriesOf(chart, 'Candlestick')?.data.at(-1)).toEqual(candle(130, 8.5, 8.55))
    expect(seriesOf(chart, 'Candlestick')?.data).toHaveLength(4)
    expect(seriesOf(chart, 'Histogram')?.updates).toEqual([point(120, 5), point(130, 1)])
    expect(chart.calls.filter((call) => call.endsWith('data 3')).length).toBe(settings)
  })

  it('puts back what it moved after the bars are read again', () => {
    const stream = channel()
    const { rerender } = render(<PriceChart {...props({ live: stream.live })} />)
    stream.send({ candles: [candle(130, 8.5, 8.55)], volume: [] })

    rerender(<PriceChart {...props({ candles: [...CANDLES], live: stream.live })} />)

    expect(seriesOf(theChart(), 'Candlestick')?.data.at(-1)).toEqual(candle(130, 8.5, 8.55))
  })

  it('leaves alone a candle older than the last one drawn, which the library refuses', () => {
    const stream = channel()
    render(<PriceChart {...props({ live: stream.live })} />)

    expect(() => stream.send({ candles: [candle(90)], volume: [] })).not.toThrow()
    expect(seriesOf(theChart(), 'Candlestick')?.data).toEqual(CANDLES)
  })

  it('stops listening when it is no longer given', () => {
    const stream = channel()
    const { rerender } = render(<PriceChart {...props({ live: stream.live })} />)
    expect(stream.listening).toBe(true)

    rerender(<PriceChart {...props({ live: null })} />)

    expect(stream.listening).toBe(false)
  })
})
