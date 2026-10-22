import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ThemeContext } from '@/components/theme-context'
import type { Theme } from '@/lib/theme'
import { charts, resetCharts, seriesOf, theChart } from '@/test/fakeCharts'

import type { Candle } from '../engine/workerProtocol'

import type { TestDone } from './TestPanel'
import { TestResult } from './TestResult'

vi.mock('lightweight-charts', async () => (await import('@/test/fakeCharts')).fakeLightweightCharts)

// 20 minute bars: 10 on the evening of the 20th (the warm-up), 10 on the 21st, Paris time.
const START = Date.parse('2026-10-20T21:50:00Z')
const at = (index: number) => START + index * 60_000
const CANDLES: Candle[] = Array.from({ length: 20 }, (_, index) => {
  const close = 24_000 + index * 2.5
  return { openTime: at(index), closeTime: at(index + 1), open: close - 1, high: close + 3, low: close - 3, close, volume: 1 }
})

const DONE: TestDone = {
  day: '2026-10-21',
  ticker: { provider: 'capital', key: 'US100', side: 'bid', rangeTicks: null },
  timeframe: '1',
  candles: CANDLES,
  response: {
    milliseconds: 41,
    bars: 20,
    plots: [
      { title: 'Fast EMA', points: 20, last: 24_045.6, data: CANDLES.slice(10).map((candle) => [candle.openTime, candle.close - 0.4] as [number, number]) },
      { title: 'RSI', points: 20, last: 55, data: CANDLES.slice(10).map((candle) => [candle.openTime, 55] as [number, number]) },
    ],
    alerts: [
      { type: 'alertcondition', title: 'Long', message: '{"action": "long"}', barIndex: 12, time: at(12) },
      { type: 'alertcondition', title: 'Long', message: '', barIndex: 3, time: at(3) },
    ],
    warnings: [],
  },
}

beforeEach(resetCharts)

describe('the result of a test', () => {
  it('draws the day with the plots near the prices and the alerts on their bars, and names what is not drawn', () => {
    render(<TestResult run={DONE} scriptName="EMA" timeZone="Europe/Paris" />)

    const chart = theChart()
    expect(seriesOf(chart, 'Candlestick')?.data).toHaveLength(10)
    expect(seriesOf(chart, 'Candlestick')?.markers.map((marker) => marker.text)).toEqual(['Long'])
    // The plot near the prices, in the first of the colours runs compared on one chart take.
    const line = seriesOf(chart, 'Line')
    expect(line?.data).toHaveLength(10)
    expect(line?.options.color).toBe('#3b82f6')
    const plots = screen.getByRole('list', { name: 'Plots' })
    expect(within(plots).getByText('Fast EMA')).toBeInTheDocument()
    expect(within(plots).getByText('Not drawn over the prices: RSI.')).toBeInTheDocument()
    // The alert of the warm-up day is not one of the day's.
    expect(screen.getByText('Alerts on 2026-10-21 (1)')).toBeInTheDocument()
    expect(screen.getByText('TradingView Lightweight Charts™', { exact: false })).toBeInTheDocument()
  })

  it('brings an alert chosen in the list into view on the chart', async () => {
    const user = userEvent.setup()
    render(<TestResult run={DONE} scriptName="EMA" timeZone="Europe/Paris" />)

    await user.click(screen.getByRole('button', { name: /Long/ }))

    const candles = seriesOf(theChart(), 'Candlestick')?.data as { time: number }[]
    expect(theChart().range).toEqual({ from: candles[0].time, to: candles[9].time })
  })

  it('draws the chart again in another theme, its colours read when it was made', () => {
    const themed = (theme: Theme) => (
      <ThemeContext.Provider value={{ preference: theme, theme, setPreference: () => {} }}>
        <TestResult run={DONE} scriptName="EMA" timeZone="Europe/Paris" />
      </ThemeContext.Provider>
    )
    const { rerender } = render(themed('dark'))
    const first = theChart()

    rerender(themed('light'))

    expect(first.removed).toBe(true)
    expect(charts).toHaveLength(2)
    expect(seriesOf(theChart(), 'Candlestick')?.data).toHaveLength(10)
    expect(seriesOf(theChart(), 'Line')?.data).toHaveLength(10)
  })
})
