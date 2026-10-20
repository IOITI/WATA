import { describe, expect, it } from 'vitest'

import { answer } from './workerCore'
import type { SeriesAsker } from './workerCore'
import type { Candle } from './workerProtocol'

function candles(count: number, base: number, seconds = 60): Candle[] {
  const start = Date.UTC(2026, 9, 15, 6, 0)
  return Array.from({ length: count }, (_, index) => ({
    openTime: start + index * seconds * 1000,
    closeTime: start + (index + 1) * seconds * 1000,
    open: base + index,
    high: base + index + 1,
    low: base + index - 1,
    close: base + index,
    volume: 1,
  }))
}

const noSeries: SeriesAsker = async () => {
  throw new Error('nothing else to read')
}

describe('compile', () => {
  it('answers the declaration and the inputs, without running anything', async () => {
    const response = await answer(
      { id: 1, op: 'compile', source: '//@version=6\nindicator("x")\nlength = input.int(14, "Length")\nplot(ta.sma(close, length))\n' },
      noSeries,
    )

    expect(response).toMatchObject({ id: 1, ok: true, op: 'compile', declaration: 'indicator' })
    expect(response.ok && response.op === 'compile' && response.inputs.map((input) => [input.title, input.defval])).toEqual([
      ['Length', 14],
    ])
  })

  it('says where a source does not compile', async () => {
    const response = await answer({ id: 2, op: 'compile', source: '//@version=6\nindicator("x")\nx = ta.sma(close, 10\nplot(x)\n' }, noSeries)

    expect(response).toMatchObject({ id: 2, ok: false, error: { line: 5, column: 1 } })
  })
})

describe('run', () => {
  it('runs on the bars handed over and asks the page for the other series, without the exchange prefix', async () => {
    const asked: string[] = []
    const ask: SeriesAsker = async (id, symbol, timeframe) => {
      asked.push(`${id} ${symbol} ${timeframe}`)
      return candles(100, 1000)
    }

    const response = await answer(
      {
        id: 3,
        op: 'run',
        source: `//@version=6
indicator("x")
other = request.security("dukascopy:DUKASCOPY_US100", "1", close)
plot(other, "Other")
plot(close, "Close")
`,
        bars: candles(100, 25000),
        symbol: 'CAPITAL_US100',
        timeframe: '1',
      },
      ask,
    )

    expect(response.ok).toBe(true)
    if (!response.ok || response.op !== 'run') return
    expect(asked).toEqual(['3 DUKASCOPY_US100 1'])
    expect(response.bars).toBe(100)
    expect(response.plots.find((plot) => plot.title === 'Close')?.last).toBe(25099)
    expect(response.plots.find((plot) => plot.title === 'Other')?.last).toBe(1099)
  })

  it('reports a series the page could not give as the run\'s failure', async () => {
    const response = await answer(
      {
        id: 4,
        op: 'run',
        source: '//@version=6\nindicator("x")\nplot(request.security("SKILLING:US100", "1", close))\n',
        bars: candles(20, 25000),
        symbol: 'CAPITAL_US100',
        timeframe: '1',
      },
      async (_, symbol) => {
        throw new Error(`${symbol} is not an asset of WATA`)
      },
    )

    expect(response).toMatchObject({ id: 4, ok: false, error: { message: expect.stringContaining('US100 is not an asset of WATA') } })
  })
})
