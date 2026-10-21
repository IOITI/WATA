import { describe, expect, it } from 'vitest'

import {
  barEnd,
  daysPerRequest,
  DEFAULT_TIMEFRAME,
  readTimeframe,
  shortName,
  TIMEFRAMES,
  timeframeOf,
  unevenNote,
} from './timeframes'

describe('timeframes', () => {
  it('offers bars of seconds and of minutes', () => {
    expect(TIMEFRAMES.map((timeframe) => [timeframe.pine, timeframe.label])).toEqual([
      ['10S', '10 s'],
      ['20S', '20 s'],
      ['30S', '30 s'],
      ['45S', '45 s'],
      ['1', '1 min'],
      ['5', '5 min'],
      ['15', '15 min'],
      ['60', '1 hour'],
    ])
    expect(DEFAULT_TIMEFRAME.pine).toBe('1')
  })

  it.each([
    // As Pine and Vela name them.
    ['10S', 10, '10S'],
    ['45S', 45, '45S'],
    ['1', 60, '1'],
    ['5', 300, '5'],
    ['60', 3600, '60'],
    ['240', 14_400, '240'],
    ['1440', 86_400, '1440'],
    // As PineTS writes two minutes, and as Vela also writes them.
    ['120S', 120, '2'],
    ['1h', 3600, '60'],
    ['15m', 900, '15'],
    // As a person types them.
    ['45s', 45, '45S'],
    ['90 s', 90, '90S'],
    ['30 sec', 30, '30S'],
    ['2 min', 120, '2'],
    ['4h', 14_400, '240'],
    ['1 hour', 3600, '60'],
    ['1d', 86_400, '1440'],
    ['  7  ', 420, '7'],
  ])('reads %s as %i seconds, named %s', (text, seconds, pine) => {
    expect(timeframeOf(text)).toMatchObject({ seconds, pine })
  })

  it.each([
    ['', 'Write a length: 45s, 2m, 1h.'],
    ['D', 'Not a length of time: write it as 45s, 2m or 1h.'],
    ['1W', 'Not a length of time: write it as 45s, 2m or 1h.'],
    ['1.5m', 'Not a length of time: write it as 45s, 2m or 1h.'],
    ['-10s', 'Not a length of time: write it as 45s, 2m or 1h.'],
    ['5S', 'WATA records ten-second bars: 10 seconds at least.'],
    ['0', 'WATA records ten-second bars: 10 seconds at least.'],
    ['2d', 'A day at most.'],
    ['1441', 'A day at most.'],
  ])('has no bars of %j', (text, error) => {
    expect(timeframeOf(text)).toBeNull()
    expect(readTimeframe(text)).toEqual({ error })
  })

  it('writes a length as it is typed', () => {
    expect([10, 45, 90, 60, 900, 3600, 14_400, 86_400].map(shortName)).toEqual(['10s', '45s', '90s', '1m', '15m', '1h', '4h', '24h'])
    expect(timeframeOf('90S')?.label).toBe('90 s')
    expect(timeframeOf('7200S')?.label).toBe('2 hours')
  })

  it('ends a bar with the last ten seconds that begin in it', () => {
    // A length of whole ten seconds ends when the next bar begins.
    expect(barEnd(600, 60)).toBe(660)
    expect(barEnd(600, 30)).toBe(630)
    // 45 s bars at 0, 45, 90, 135 hold the ten seconds that begin at 0..40, 50..80, 90..130, 140..170.
    expect([0, 45, 90, 135].map((start) => barEnd(start, 45))).toEqual([50, 90, 140, 180])
  })

  it('says what a length that cuts no whole ten seconds holds', () => {
    expect(unevenNote(30)).toBeNull()
    expect(unevenNote(90)).toBeNull()
    expect(unevenNote(45)).toBe(
      '45 s is no multiple of the ten seconds WATA records: each bar holds the ten-second bars that begin in it, ' +
        '40 or 50 s of prices, and closes when the last of them does.',
    )
    expect(unevenNote(15)).toContain('10 or 20 s of prices')
  })

  it('asks for as many whole days as stay under the cap', () => {
    expect(daysPerRequest(60)).toBe(8)
    expect(daysPerRequest(300)).toBe(41)
    expect(daysPerRequest(3600, 31)).toBe(31)
    expect(daysPerRequest(10)).toBe(1)
    expect(daysPerRequest(45)).toBe(6)
  })
})
