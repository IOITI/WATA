import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { charts, resetCharts, seriesOf, theChart } from '@/test/fakeCharts'

import type { Candle, ChartMarker, LinePoint } from './chartData'
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
