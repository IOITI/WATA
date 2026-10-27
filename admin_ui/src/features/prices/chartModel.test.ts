import { toCsv } from 'ioiti-ui'
import { describe, expect, it } from 'vitest'

import type { PriceInstrument, PriceRangeBars, PriceRangeRow } from '@/lib/types'

import { rangeBarsCsv, rangeBarState, rangeChart } from './chartModel'

const utc = (iso: string) => Date.parse(iso) / 1000
const T0 = utc('2026-10-12T08:00:00Z')
const SHOWN = utc('2026-10-12T10:00:00Z')

const TURBO = { ref: 'saxo/2', id: 2, uic: 40000002, role: 'turbo', indice: 'us100', symbol: 'SG-NDX-L2', direction: 'long', barrier: 24150, issuer: 'SG' } as PriceInstrument
const INDEX = { ref: 'saxo/1', id: 1, uic: 1909050, role: 'underlying', indice: 'us100', description: 'NASDAQ 100' } as PriceInstrument

// Two bars born in the same ten seconds: the chart is given 10:00:10 and 10:00:11 for them.
const ROWS: PriceRangeRow[] = [
  [T0, 8.4, 8.5, 8.4, 8.5, T0, T0 + 20, true, false, 25000.5],
  [T0 + 10, 8.5, 8.6, 8.5, 8.6, T0 + 10, T0 + 20, true, false, 25010],
  [T0 + 11, 8.6, 8.64, 8.6, 8.62, T0 + 10, T0 + 30, false, false, 25012.25],
]

function answer(changes: Partial<PriceRangeBars> = {}): PriceRangeBars {
  return {
    instrument: TURBO, under: INDEX, from: '2026-10-12', to: '2026-10-12', timezone: 'Europe/Paris', side: 'bid',
    tick: 0.01, decimals: 2, ticks: 10, size: 0.1, auto: false, columns: [], bars: ROWS, ...changes,
  }
}

describe('range bars as a chart', () => {
  it('are placed where the chart needs them, and found again by that place', () => {
    const chart = rangeChart(answer())

    expect(chart.candles.map((candle) => candle.time - SHOWN)).toEqual([0, 10, 11])
    expect(chart.rowAt.get(SHOWN + 11)).toBe(ROWS[2])
    expect(chart.lastTime).toBe(SHOWN + 11)
    expect(chart.barsLabel).toBe('range bars of 0.10')
    expect(chart.key).toBe('saxo/2|2026-10-12|2026-10-12|range|0.1|bid')
  })

  it('carry the index as a line on the same times, named', () => {
    const chart = rangeChart(answer())

    expect(chart.indexName).toBe('NASDAQ 100')
    expect(chart.indexLine?.map((point) => point.time)).toEqual(chart.candles.map((candle) => candle.time))
    expect(chart.indexDecimals).toBe(2)
  })

  it('have no index line for an index shown by itself', () => {
    const alone = rangeChart(
      answer({ instrument: INDEX, under: null, bars: ROWS.map((row) => [...row.slice(0, 9), null] as unknown as PriceRangeRow) }),
    )

    expect(alone.indexLine).toBeUndefined()
    expect(alone.indexLocate).toBeUndefined()
  })

  it('put a moment on the bar that was growing then', () => {
    const chart = rangeChart(answer())

    expect(chart.locate(T0 + 5)).toBe(SHOWN)
    expect(chart.locate(T0 + 25)).toBe(SHOWN + 11)
    expect(chart.locate(T0 + 30)).toBeNull()
  })

  it('know what became of each bar', () => {
    expect(rangeBarState(ROWS[0], false)).toBe('complete')
    expect(rangeBarState(ROWS[2], true)).toBe('growing')
    expect(rangeBarState(ROWS[2], false)).toBe('cut')
  })
})

describe('range bars as a file', () => {
  it('give the times a bar really began and ended, not the place the chart gave it', () => {
    // The file starts with a byte-order mark and ends its lines the way spreadsheets expect.
    const [header, , second, third] = toCsv(ROWS, rangeBarsCsv('Europe/Paris')).replace(/^\uFEFF/, '').trim().split(/\r?\n/)

    expect(header).toBe('began_utc,ended_utc,began_local,ended_local,open,high,low,close,seconds,complete,after_pause,index')
    // Both began at 10:00:10, whatever second the chart placed the later one at.
    expect(second).toBe('2026-10-12T08:00:10.000Z,2026-10-12T08:00:20.000Z,2026-10-12 10:00:10,2026-10-12 10:00:20,8.5,8.6,8.5,8.6,10,yes,no,25010')
    expect(third).toBe('2026-10-12T08:00:10.000Z,2026-10-12T08:00:30.000Z,2026-10-12 10:00:10,2026-10-12 10:00:30,8.6,8.64,8.6,8.62,20,no,no,25012.25')
  })
})
