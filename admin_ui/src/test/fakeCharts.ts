/**
 * A stand-in for lightweight-charts in tests: jsdom has no canvas to draw on.
 * It draws nothing and records what was asked of the library, in order.
 *
 *   vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)
 */

export interface FakeSeries {
  type: 'Candlestick' | 'Line'
  /** The panel it was added to: 0 for the prices, 1 under them. */
  pane: number
  options: Record<string, unknown>
  data: Record<string, unknown>[]
  markers: Record<string, unknown>[]
  removed: boolean
}

export interface FakeChart {
  container: HTMLElement
  options: Record<string, any>
  timeScaleOptions: Record<string, unknown>
  series: FakeSeries[]
  /** Every call that changes what is drawn, in the order it was made. */
  calls: string[]
  fits: number
  /** The last range the chart was told to bring into view. */
  range: { from: number; to: number } | null
  removed: boolean
  /** Moves the crosshair as the library would: to a time, or off the chart. */
  hover: (time?: number) => void
}

export const charts: FakeChart[] = []

export function resetCharts() {
  charts.length = 0
}

/** The chart on the page. Fails when there is none, or more than one still alive. */
export function theChart(): FakeChart {
  const alive = charts.filter((chart) => !chart.removed)
  if (alive.length !== 1) throw new Error(`Expected one chart on the page, found ${alive.length}`)
  return alive[0]
}

export function seriesOf(chart: FakeChart, type: FakeSeries['type']): FakeSeries | undefined {
  return chart.series.find((series) => series.type === type && !series.removed)
}

function createChart(container: HTMLElement, options: Record<string, any>) {
  let crosshairHandler: ((event: { time?: number }) => void) | null = null
  const chart: FakeChart = {
    container,
    options,
    timeScaleOptions: {},
    series: [],
    calls: [],
    fits: 0,
    range: null,
    removed: false,
    hover: (time) => crosshairHandler?.({ time }),
  }
  charts.push(chart)

  return {
    addSeries(type: FakeSeries['type'], seriesOptions: Record<string, unknown> = {}, pane = 0) {
      const series: FakeSeries = { type, pane, options: { ...seriesOptions }, data: [], markers: [], removed: false }
      chart.series.push(series)
      chart.calls.push(`add ${type}`)
      return {
        fake: series,
        setData(data: Record<string, unknown>[]) {
          series.data = data
          chart.calls.push(`${type} data ${data.length}`)
        },
        applyOptions(changes: Record<string, unknown>) {
          Object.assign(series.options, changes)
        },
      }
    },
    removeSeries(api: { fake: FakeSeries }) {
      api.fake.removed = true
      chart.calls.push(`remove ${api.fake.type}`)
    },
    panes() {
      const count = chart.series.some((series) => series.pane === 1 && !series.removed) ? 2 : 1
      return Array.from({ length: count }, () => ({ setStretchFactor() {} }))
    },
    timeScale() {
      return {
        applyOptions(changes: Record<string, unknown>) {
          Object.assign(chart.timeScaleOptions, changes)
        },
        fitContent() {
          chart.fits += 1
          chart.calls.push('fit')
        },
        setVisibleRange(range: { from: number; to: number }) {
          chart.range = range
          chart.calls.push('range')
        },
      }
    },
    subscribeCrosshairMove(handler: (event: { time?: number }) => void) {
      crosshairHandler = handler
    },
    unsubscribeCrosshairMove() {
      crosshairHandler = null
    },
    remove() {
      chart.removed = true
    },
  }
}

function createSeriesMarkers(api: { fake: FakeSeries }, markers: Record<string, unknown>[] = []) {
  api.fake.markers = markers
  return {
    setMarkers(next: Record<string, unknown>[]) {
      api.fake.markers = next
    },
    markers: () => api.fake.markers,
    detach() {},
  }
}

export const fakeLightweightCharts = {
  CandlestickSeries: 'Candlestick',
  LineSeries: 'Line',
  ColorType: { Solid: 'solid' },
  CrosshairMode: { Normal: 0 },
  LineStyle: { Solid: 0, Dashed: 2 },
  createChart,
  createSeriesMarkers,
}
