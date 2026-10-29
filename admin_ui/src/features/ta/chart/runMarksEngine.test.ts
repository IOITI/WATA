import type { ExecutionRequest, IndicatorModel } from '@luxalgo/vela'
import { describe, expect, it, vi } from 'vitest'

import type { RunLayer } from './runDayModel'
import { RUN_LANGUAGE, runMarksEngine } from './runMarksEngine'

const LAYER: RunLayer = {
  title: 'The run: signals and trades',
  labels: [
    {
      id: 'signal-12', paneId: 'unrouted', xloc: 'bar_time', x: 1_792_997_400_000, y: 0, yloc: 'abovebar', text: 'Short',
      style: 'arrowdown', color: '#0000ff', textColor: '#0000ff', size: 'small', textAlign: 'center', fontFamily: 'default',
    },
  ],
  trades: [{ time: 1_792_997_460_000, price: 24855.2, side: 'sell', kind: 'entry', label: '#1 short', tradeId: '1' }],
  backgrounds: [],
}

async function run(engine = runMarksEngine()) {
  const prepared = await engine.prepare(JSON.stringify(LAYER), 'run')
  const models: IndicatorModel[] = []
  const done = vi.fn()
  const session = engine.execute({ prepared, market: { symbol: 'capital:CAPITAL_US100', timeframe: '1' }, bars: [], mode: 'static' } as ExecutionRequest, {
    onModel: (model) => models.push(model),
    onDone: done,
  })
  await Promise.resolve()
  return { prepared, models, done, session }
}

describe('the run\'s layer as an engine of Vela', () => {
  it('is an overlay with no input, whose model is the run\'s labels and trade markers', async () => {
    const { prepared, models, done } = await run()

    expect(prepared).toMatchObject({ language: RUN_LANGUAGE, inputs: [], meta: { title: LAYER.title, overlay: true } })
    expect(models).toEqual([
      {
        id: 'run', title: LAYER.title, shorttitle: 'The run', overlay: true, paneHint: 'price', series: [], fills: [],
        backgrounds: [], priceLines: [], labels: LAYER.labels, trades: LAYER.trades, inputs: [], inputValues: {},
      },
    ])
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('says its model again when Vela asks for a run, and nothing once stopped', async () => {
    const { models, session } = await run()

    session.notifyBars()
    await Promise.resolve()
    expect(models).toHaveLength(1)
    session.notifyBars('complete')
    session.update({})
    await Promise.resolve()
    expect(models).toHaveLength(3)
    session.stop()
    session.notifyBars('complete')
    await Promise.resolve()
    expect(models).toHaveLength(3)
  })
})
