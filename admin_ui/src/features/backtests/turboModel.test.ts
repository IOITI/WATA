import { describe, expect, it } from 'vitest'

import type { BacktestTurboMeasure, BacktestTurboModel } from '@/lib/types'

import { defaultMeasurePeriod, measureSentence, measureState, underlyingOfRef } from './turboModel'

const MEASURE: BacktestTurboMeasure = {
  made_at: '2026-10-20T01:00:00Z',
  underlying: { provider: 'dukascopy', key: 'us100', side: 'bid' },
  indice: 'us100',
  days: 6,
  first: '2026-10-13',
  last: '2026-10-20',
  few_days: false,
  directions: {
    long: { factor: 0.0086, offset: -2, spacing: 50, turbos: 40, bars: 9000, r2: 0.998, error_eur: 0.02, error_percent: 1.24 },
    short: { factor: 0.0085, offset: 2, spacing: 50, turbos: 38, bars: 8800, r2: 0.997, error_eur: 0.03, error_percent: null },
  },
  quoting: { first_minute: 480, last_minute: 1320 },
  issuer: 'SG',
}

describe('the turbos made up from the index', () => {
  it('say what they were measured on and how well they price the recorded ones', () => {
    expect(measureSentence(MEASURE)).toBe(
      'Measured on 6 recorded days (13 Oct to 20 Oct 2026) against Dukascopy us100: long 0.0086 € a point, levels every 50 points, 1.2 % median error; short 0.0085 € a point, levels every 50 points.',
    )
    expect(measureSentence({ ...MEASURE, days: 1, few_days: true })).toMatch(/^Measured on 1 recorded day .* Few days: take what they give as a rough guess\.$/)
    expect(measureSentence(null)).toMatch(/^Not measured yet/)
  })

  it('say what the measure is doing', () => {
    const idle: BacktestTurboModel = { state: 'idle', error: null, asked: null, finished_at: null, measure: null, underlying: 'dukascopy/us100', max_days: 92 }
    expect(measureState(idle)).toBeNull()
    expect(measureState({ ...idle, state: 'running', asked: { from: '2026-09-21', to: '2026-10-20' } })).toBe('Measuring the turbos of 21 Sept to 20 Oct 2026…')
    expect(measureState({ ...idle, state: 'failed', error: 'No recorded long turbo' })).toBe('The last measure failed: No recorded long turbo')
  })

  it('name the index they are made up from', () => {
    expect(underlyingOfRef('dukascopy/us100')).toBe('Dukascopy us100')
  })

  it('measure the last thirty days by default', () => {
    expect(defaultMeasurePeriod('2026-10-20')).toEqual({ from: '2026-09-21', to: '2026-10-20' })
  })
})
