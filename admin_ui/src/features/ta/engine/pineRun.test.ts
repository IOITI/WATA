import { Indicator, PineTS } from 'pinets'
import { describe, expect, it } from 'vitest'

import { alertParts, compileFailure, runSummary } from './pineRun'
import type { Candle } from './workerProtocol'

function candles(count: number): Candle[] {
  return Array.from({ length: count }, (_, index) => {
    const close = 100 + Math.sin(index / 5) * 10
    return {
      openTime: 1_760_000_000_000 + index * 60_000,
      closeTime: 1_760_000_000_000 + (index + 1) * 60_000,
      open: close - 1,
      high: close + 2,
      low: close - 2,
      close,
      volume: 10,
    }
  })
}

describe('compileFailure', () => {
  it.each([
    ["Failed to transpile Pine Script version 6: Unexpected token EOF '' at 5:1", 5, 1],
    ['Unexpected token (12:34)', 12, 34],
    ['Something went wrong', null, null],
  ])('reads the position in %j', (message, line, column) => {
    expect(compileFailure(new Error(message))).toEqual({ message, line, column })
  })

  it('takes anything thrown', () => {
    expect(compileFailure('plain')).toEqual({ message: 'plain', line: null, column: null })
  })
})

describe('alertParts', () => {
  it('takes the title and message back from the object PineTS puts in the title', () => {
    expect(alertParts({ title: { title: 'Enter Long', message: '{"action": "long"}' }, message: '' })).toEqual({
      title: 'Enter Long',
      message: '{"action": "long"}',
    })
  })

  it('keeps a title and message given in their places', () => {
    expect(alertParts({ title: 'Cross', message: 'Up' })).toEqual({ title: 'Cross', message: 'Up' })
    expect(alertParts({ title: undefined, message: 'From alert()' })).toEqual({ title: null, message: 'From alert()' })
  })
})

describe('runSummary, on a real PineTS run', () => {
  it('gives the last value of each plot and every alert in bar order, named arguments included', async () => {
    const pine = new PineTS(candles(120))
    pine.setAlertMode('all')
    const context = await pine.run(
      new Indicator(`//@version=6
indicator("t")
fast = ta.sma(close, 5)
plot(fast, "Fast")
plot(na, "Never")
alertcondition(ta.crossover(close, fast), title = "Cross up", message = "up {{close}}")
`),
    )

    const summary = runSummary(context, 12.4)

    expect(summary.milliseconds).toBe(12)
    expect(summary.bars).toBe(120)
    const fast = summary.plots.find((plot) => plot.title === 'Fast')
    expect(fast?.points).toBe(120)
    expect(typeof fast?.last).toBe('number')
    expect(summary.plots.find((plot) => plot.title === 'Never')?.last).toBeNull()
    expect(summary.alerts.length).toBeGreaterThan(0)
    expect(summary.alerts.every((alert) => alert.title === 'Cross up' && alert.message === 'up {{close}}')).toBe(true)
    const times = summary.alerts.map((alert) => alert.time)
    expect(times).toEqual([...times].sort((a, b) => a - b))
  })
})
