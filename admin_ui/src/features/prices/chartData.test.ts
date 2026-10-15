import { describe, expect, it } from 'vitest'

import type { JournalEntry, Position, PriceBarRow, PriceInstrument, PriceRangeRow } from '@/lib/types'

import {
  allowedIntervals,
  barsByChartTime,
  chartTime,
  chartTimeIso,
  daysBetween,
  defaultInterval,
  drawnTimes,
  formatChartClock,
  formatChartTime,
  groupInstruments,
  instrumentLabel,
  intervalLabel,
  MAX_BUCKETS,
  placeRangeBars,
  priceDecimals,
  RANGE,
  rangeCandles,
  rangeIndexLine,
  rangeLocator,
  shiftDay,
  signalMarkers,
  sizeOf,
  stepRange,
  ticksOf,
  timeLocator,
  todayIn,
  toCandles,
  toLine,
  tradeMarkers,
  valueDecimals,
  withAlpha,
  zoneOffsetSeconds,
} from './chartData'

const PARIS = 'Europe/Paris'
const utc = (iso: string) => Date.parse(iso) / 1000

/** A bar at `iso` whose bid goes 8.40 / 8.52 / 8.36 / 8.45 and whose ask is five cents above. */
function bar(iso: string, changes: Partial<Record<number, number | null>> = {}): PriceBarRow {
  const row: PriceBarRow = [utc(iso), 8.4, 8.52, 8.36, 8.45, 8.45, 8.57, 8.41, 8.5, 3]
  for (const [index, value] of Object.entries(changes)) (row as (number | null)[])[Number(index)] = value as number | null
  return row
}

describe('intervals', () => {
  it('names a bar length the way it is said', () => {
    expect([10, 30, 60, 300, 900, 3600].map(intervalLabel)).toEqual(['10 s', '30 s', '1 min', '5 min', '15 min', '1 h'])
  })

  it('starts a period on the shortest bars that fit one chart', () => {
    expect(defaultInterval(1)).toBe(10)
    expect(defaultInterval(2)).toBe(30)
    expect(defaultInterval(7)).toBe(60)
    expect(defaultInterval(30)).toBe(300)
    expect(defaultInterval(90)).toBe(900)
    expect(defaultInterval(366)).toBe(3600)
    for (const days of [1, 2, 7, 30, 90, 366]) {
      expect((days * 86_400) / defaultInterval(days)).toBeLessThanOrEqual(MAX_BUCKETS)
    }
  })

  it('offers only the bar lengths the period can be shown at', () => {
    expect(allowedIntervals(1)).toEqual([10, 30, 60, 300, 900, 3600])
    expect(allowedIntervals(7)).toEqual([60, 300, 900, 3600])
  })
})

describe('days', () => {
  it('counts a period with both its ends', () => {
    expect(daysBetween('2026-10-12', '2026-10-12')).toBe(1)
    expect(daysBetween('2026-10-06', '2026-10-12')).toBe(7)
    expect(daysBetween('2026-10-24', '2026-10-26')).toBe(3)
  })

  it('knows the date it is in the trading timezone, not in the browser', () => {
    // 23:30 UTC on the 12th is already the 13th in Paris, and still the 12th in New York.
    const late = new Date('2026-10-12T23:30:00Z')
    expect(todayIn(PARIS, late)).toBe('2026-10-13')
    expect(todayIn('America/New_York', late)).toBe('2026-10-12')
  })

  it('moves a day across a month end', () => {
    expect(shiftDay('2026-10-01', -1)).toBe('2026-09-30')
    expect(shiftDay('2026-12-31', 1)).toBe('2027-01-01')
  })
})

describe('trading timezone', () => {
  it('shifts a time by the offset of its own date', () => {
    expect(zoneOffsetSeconds(utc('2026-07-01T10:00:00Z'), PARIS)).toBe(7200)
    expect(zoneOffsetSeconds(utc('2026-12-01T10:00:00Z'), PARIS)).toBe(3600)
    expect(zoneOffsetSeconds(utc('2026-07-01T10:00:00Z'), 'America/New_York')).toBe(-4 * 3600)
    expect(zoneOffsetSeconds(utc('2026-07-01T10:00:00Z'), 'Asia/Kolkata')).toBe(5.5 * 3600)
  })

  it('reads 08:00 UTC as ten in the morning in Paris in summer', () => {
    const shown = new Date(chartTime(utc('2026-10-12T08:00:00Z'), PARIS) * 1000)
    expect(shown.toISOString()).toBe('2026-10-12T10:00:00.000Z')
  })

  it('changes offset at the minute the clocks change', () => {
    // The clocks go back on 25 Oct 2026 at 01:00 UTC.
    expect(zoneOffsetSeconds(utc('2026-10-25T00:59:50Z'), PARIS)).toBe(7200)
    expect(zoneOffsetSeconds(utc('2026-10-25T01:00:00Z'), PARIS)).toBe(3600)
    // And forward on 29 Mar 2026 at 01:00 UTC.
    expect(zoneOffsetSeconds(utc('2026-03-29T00:59:50Z'), PARIS)).toBe(3600)
    expect(zoneOffsetSeconds(utc('2026-03-29T01:00:00Z'), PARIS)).toBe(7200)
  })
})

describe('candles', () => {
  it('takes the side asked for, at the time of the trading timezone', () => {
    const bars = [bar('2026-10-12T08:00:00Z')]

    expect(toCandles(bars, 'bid', 10, PARIS)).toEqual([
      { time: utc('2026-10-12T10:00:00Z'), open: 8.4, high: 8.52, low: 8.36, close: 8.45 },
    ])
    expect(toCandles(bars, 'ask', 10, PARIS)).toEqual([
      { time: utc('2026-10-12T10:00:00Z'), open: 8.45, high: 8.57, low: 8.41, close: 8.5 },
    ])
  })

  it('leaves a short stretch without bars empty, one slot per missing bar', () => {
    const bars = [bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:00:40Z')]

    const points = toCandles(bars, 'bid', 10, PARIS)

    expect(points.map((point) => point.time - utc('2026-10-12T10:00:00Z'))).toEqual([0, 10, 20, 30, 40])
    expect(points.map((point) => 'open' in point)).toEqual([true, false, false, false, true])
  })

  it('closes up a night instead of drawing ten empty hours', () => {
    const bars = [bar('2026-10-12T19:59:50Z'), bar('2026-10-13T06:00:00Z')]

    expect(toCandles(bars, 'bid', 10, PARIS)).toHaveLength(2)
  })

  it('counts a hole in the bars of the interval shown, not in ten-second ones', () => {
    const bars = [bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:15:00Z')]

    const points = toCandles(bars, 'bid', 300, PARIS)

    expect(points).toHaveLength(4)
    expect(points.filter((point) => !('open' in point))).toHaveLength(2)
  })

  it('leaves out a bar with no ask when the ask is shown, and keeps it for the bid', () => {
    const bars = [bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:00:10Z', { 5: null, 6: null, 7: null, 8: null })]

    expect(toCandles(bars, 'ask', 10, PARIS)).toHaveLength(1)
    expect(toCandles(bars, 'bid', 10, PARIS)).toHaveLength(2)
  })

  it('never gives the chart a time that does not come after the one before', () => {
    // The hour from 02:00 to 03:00 happens twice in Paris on 25 Oct 2026.
    const bars: PriceBarRow[] = []
    for (let t = utc('2026-10-25T00:30:00Z'); t < utc('2026-10-25T02:00:00Z'); t += 300) {
      bars.push([t, 1, 1, 1, 1, 1, 1, 1, 1, 0])
    }

    const times = toCandles(bars, 'bid', 300, PARIS).map((point) => point.time)

    expect(times.length).toBeGreaterThan(0)
    expect(times.length).toBeLessThan(bars.length)
    for (let index = 1; index < times.length; index += 1) expect(times[index]).toBeGreaterThan(times[index - 1])
  })

  it('draws the hour that does not exist when the clocks go forward as a closed-up step, with ascending times', () => {
    const bars: PriceBarRow[] = []
    for (let t = utc('2026-03-29T00:30:00Z'); t < utc('2026-03-29T01:30:00Z'); t += 300) {
      bars.push([t, 1, 1, 1, 1, 1, 1, 1, 1, 0])
    }

    const times = toCandles(bars, 'bid', 300, PARIS).map((point) => point.time)

    expect(times).toHaveLength(bars.length)
    for (let index = 1; index < times.length; index += 1) expect(times[index]).toBeGreaterThan(times[index - 1])
  })

  it('is empty for no bars', () => {
    expect(toCandles([], 'bid', 10, PARIS)).toEqual([])
  })
})

describe('what is drawn', () => {
  it('knows which times carry a value, the empty slots of a hole left out', () => {
    const points = toCandles([bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:00:30Z')], 'bid', 10, PARIS)

    expect([...drawnTimes(points)]).toEqual([utc('2026-10-12T10:00:00Z'), utc('2026-10-12T10:00:30Z')])
  })

  it('shows prices with the decimals they are quoted in, two at least', () => {
    expect(priceDecimals([bar('2026-10-12T08:00:00Z')])).toBe(2)
    expect(priceDecimals([bar('2026-10-12T08:00:00Z', { 4: 0.855 })])).toBe(3)
    expect(priceDecimals([bar('2026-10-12T08:00:00Z', { 4: 25000, 8: 25000 })])).toBe(2)
    expect(priceDecimals([bar('2026-10-12T08:00:00Z', { 4: 1.123456789 })])).toBe(5)
    expect(priceDecimals([bar('2026-10-12T08:00:00Z', { 8: null })])).toBe(2)
    expect(priceDecimals([])).toBe(2)
  })

  it('writes a chart time as the clock of the trading timezone', () => {
    const time = chartTime(utc('2026-10-12T08:00:10Z'), PARIS)

    expect(formatChartTime(time)).toBe('12 Oct, 10:00:10')
    expect(chartTimeIso(time)).toBe('2026-10-12 10:00:10')
  })

  it('makes a colour see-through for the canvas, and leaves alone what it does not know', () => {
    expect(withAlpha('#059669', 0.25)).toBe('rgba(5, 150, 105, 0.25)')
    expect(withAlpha('transparent', 0.25)).toBe('transparent')
  })
})

describe('index line and readout', () => {
  it('follows the close of the bid, with the same holes as the candles', () => {
    const bars = [bar('2026-10-12T08:00:00Z', { 4: 24150.5 }), bar('2026-10-12T08:00:30Z', { 4: 24152 })]

    const points = toLine(bars, 10, PARIS)

    expect(points).toHaveLength(4)
    expect(points[0]).toEqual({ time: utc('2026-10-12T10:00:00Z'), value: 24150.5 })
    expect(points[3]).toEqual({ time: utc('2026-10-12T10:00:30Z'), value: 24152 })
  })

  it('finds the bar under the pointer from the time the chart reports', () => {
    const bars = [bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:00:10Z')]

    const lookup = barsByChartTime(bars, PARIS)

    expect(lookup.get(utc('2026-10-12T10:00:10Z'))).toBe(bars[1])
    expect(lookup.get(utc('2026-10-12T08:00:10Z'))).toBeUndefined()
  })
})

function options(bars: PriceBarRow[], interval = 10) {
  return timeLocator(interval, PARIS, drawnTimes(toCandles(bars, 'bid', interval, PARIS)))
}

function trade(changes: Partial<Position> = {}): Position {
  return {
    position_id: '5001',
    action: 'long',
    position_open_price: 8.42,
    position_close_price: 8.52,
    position_total_performance_percent: 1.19,
    execution_time_open: '2026-10-12T08:00:07Z',
    execution_time_close: '2026-10-12T08:00:33Z',
    instrument_currency: 'EUR',
    ...changes,
  } as Position
}

describe('trade markers', () => {
  const bars = ['00', '10', '20', '30'].map((second) => bar(`2026-10-12T08:00:${second}Z`))

  it('puts the entry and the exit at their prices, on the bars they happened in', () => {
    const [entry, exit] = tradeMarkers([trade()], true, options(bars))

    expect(entry).toMatchObject({
      time: utc('2026-10-12T10:00:00Z'), position: 'atPriceBottom', price: 8.42, shape: 'arrowUp', tone: 'entry', text: 'Buy €8.42',
    })
    expect(exit).toMatchObject({
      time: utc('2026-10-12T10:00:30Z'), position: 'atPriceTop', price: 8.52, shape: 'arrowDown', tone: 'profit', text: '+1.19%',
    })
  })

  it('says a loss with its sign, not with its colour alone', () => {
    const [, exit] = tradeMarkers([trade({ position_total_performance_percent: -3.59 })], true, options(bars))

    expect(exit).toMatchObject({ tone: 'loss', text: '−3.59%' })
  })

  it('only says when on the index chart, whose prices are not the turbo\'s', () => {
    const [entry, exit] = tradeMarkers([trade({ action: 'short' })], false, options(bars))

    expect(entry).toMatchObject({ position: 'belowBar', text: 'Short' })
    expect(entry).not.toHaveProperty('price')
    expect(exit).toMatchObject({ position: 'aboveBar', text: 'Short +1.19%' })
  })

  it('has no exit for a position that is still open', () => {
    const markers = tradeMarkers([trade({ execution_time_close: null, position_close_price: null })], true, options(bars))

    expect(markers.map((marker) => marker.id)).toEqual(['5001-in'])
  })

  it('falls on the bar of the interval shown', () => {
    const minutes = [bar('2026-10-12T08:00:00Z'), bar('2026-10-12T08:01:00Z')]

    const [entry, exit] = tradeMarkers([trade({ execution_time_close: '2026-10-12T08:01:59Z' })], true, options(minutes, 60))

    expect(entry.time).toBe(utc('2026-10-12T10:00:00Z'))
    expect(exit.time).toBe(utc('2026-10-12T10:01:00Z'))
  })

  it('leaves out what happened where there is no bar to put it on', () => {
    const before = trade({ execution_time_open: '2026-10-12T07:00:00Z', execution_time_close: '2026-10-12T07:30:00Z' })

    expect(tradeMarkers([before], true, options(bars))).toEqual([])
  })

  it('comes out in time order whatever the order of the trades', () => {
    const later = trade({ position_id: '5002', execution_time_open: '2026-10-12T08:00:21Z', execution_time_close: null })
    const earlier = trade({ position_id: '5001', execution_time_open: '2026-10-12T08:00:02Z', execution_time_close: '2026-10-12T08:00:12Z' })

    const times = tradeMarkers([later, earlier], true, options(bars)).map((marker) => marker.time)

    expect(times).toEqual([...times].sort((a, b) => a - b))
  })
})

describe('signal markers', () => {
  const bars = ['00', '10', '20'].map((second) => bar(`2026-10-12T08:00:${second}Z`))
  const signal = (changes: Partial<JournalEntry>): JournalEntry =>
    ({ signal_uuid: 'a', action: 'long', outcome: 'executed', decided_at: '2026-10-12T08:00:11Z', ...changes }) as JournalEntry

  it('names the signal, and what the trader did when it did not act on it', () => {
    const markers = signalMarkers(
      [
        signal({ signal_uuid: 'a' }),
        signal({ signal_uuid: 'b', action: 'short', outcome: 'refused', decided_at: '2026-10-12T08:00:21Z' }),
        signal({ signal_uuid: 'c', action: 'close-position', outcome: 'ignored', decided_at: '2026-10-12T08:00:01Z' }),
      ],
      options(bars),
    )

    expect(markers.map((marker) => [marker.text, marker.tone, marker.position, marker.shape])).toEqual([
      ['Close all ignored', 'warning', 'aboveBar', 'circle'],
      ['Long', 'neutral', 'aboveBar', 'circle'],
      ['Short refused', 'warning', 'aboveBar', 'circle'],
    ])
  })

  it('leaves out a signal with no time or outside the bars shown', () => {
    expect(signalMarkers([signal({ decided_at: null, received_timestamp: null })], options(bars))).toEqual([])
    expect(signalMarkers([signal({ decided_at: '2026-10-12T12:00:00Z' })], options(bars))).toEqual([])
  })
})

describe('range bars', () => {
  const T0 = utc('2026-10-12T08:00:00Z')
  /** A range bar that began `after` seconds past ten in the morning (Paris) and lasted `seconds`. */
  const range = (after: number, seconds: number, changes: Partial<Record<keyof typeof RANGE, unknown>> = {}): PriceRangeRow => {
    const row: PriceRangeRow = [T0 + after, 8.4, 8.5, 8.4, 8.5, T0 + after, T0 + after + seconds, true, false, 25000 + after]
    for (const [name, value] of Object.entries(changes)) (row as unknown[])[RANGE[name as keyof typeof RANGE]] = value
    return row
  }
  const SHOWN = utc('2026-10-12T10:00:00Z')

  it('are placed at the time of the trading timezone, one after the other', () => {
    const placed = placeRangeBars([range(0, 30), range(20, 10), range(21, 10)], PARIS)

    expect(placed.map((bar) => bar.time - SHOWN)).toEqual([0, 20, 21])
    expect(rangeCandles(placed)[0]).toEqual({ time: SHOWN, open: 8.4, high: 8.5, low: 8.4, close: 8.5 })
  })

  it('leave one empty slot where the data paused', () => {
    const placed = placeRangeBars([range(0, 30), range(3600, 10, { afterPause: true }), range(3620, 10)], PARIS)

    const points = rangeCandles(placed)

    expect(points.map((point) => [point.time - SHOWN, 'open' in point])).toEqual([
      [0, true], [3599, false], [3600, true], [3620, true],
    ])
  })

  it('force no empty slot where there is no room for one', () => {
    // A bar flagged as following a pause, one second after the bar before.
    const placed = placeRangeBars([range(0, 10), range(1, 10, { afterPause: true })], PARIS)

    expect(rangeCandles(placed).map((point) => point.time - SHOWN)).toEqual([0, 1])
  })

  it('carry the index as it was at the end of each bar, at the same times as the candles', () => {
    const placed = placeRangeBars([range(0, 30), range(3600, 10, { afterPause: true, index: null }), range(3620, 10)], PARIS)

    const line = rangeIndexLine(placed)

    expect(line.map((point) => point.time)).toEqual(rangeCandles(placed).map((point) => point.time))
    expect(line.map((point) => ('value' in point ? point.value : null))).toEqual([25000, null, null, 28620])
  })

  it('never give the chart a time that does not come after the one before', () => {
    // The hour from 02:00 to 03:00 happens twice in Paris on 25 Oct 2026.
    const rows: PriceRangeRow[] = []
    for (let t = utc('2026-10-25T00:30:00Z'); t < utc('2026-10-25T02:00:00Z'); t += 600) {
      rows.push([t, 1, 1, 1, 1, t, t + 10, true, false, null])
    }

    const times = placeRangeBars(rows, PARIS).map((bar) => bar.time)

    expect(times.length).toBeGreaterThan(0)
    expect(times.length).toBeLessThan(rows.length)
    for (let index = 1; index < times.length; index += 1) expect(times[index]).toBeGreaterThan(times[index - 1])
  })

  describe('a moment on them', () => {
    // 10:00:00 for 30 s, then 10:00:20 for 40 s; a pause; 11:00:00 for 10 s.
    const placed = placeRangeBars([range(0, 30), range(20, 40), range(3600, 10, { afterPause: true })], PARIS)
    const locate = rangeLocator(placed)
    const at = (seconds: number) => {
      const time = locate(T0 + seconds)
      return time === null ? null : time - SHOWN
    }

    it('is on the bar that was growing then', () => {
      expect(at(0)).toBe(0)
      expect(at(19)).toBe(0)
      expect(at(20)).toBe(20)
      expect(at(45)).toBe(20)
    })

    it('is on no bar before the first one', () => {
      expect(at(-1)).toBeNull()
    })

    it('is on no bar during a pause', () => {
      expect(at(59)).toBe(20)
      expect(at(60)).toBeNull()
      expect(at(1800)).toBeNull()
      expect(at(3600)).toBe(3600)
    })

    it('is on no bar after the last one ended', () => {
      expect(at(3609)).toBe(3600)
      expect(at(3610)).toBeNull()
    })

    it('puts a trade on its range bar, at its price', () => {
      const [entry, exit] = tradeMarkers([trade({ execution_time_close: '2026-10-12T08:00:33Z' })], true, locate)

      expect(entry).toMatchObject({ time: SHOWN, price: 8.42, text: 'Buy €8.42' })
      expect(exit).toMatchObject({ time: SHOWN + 20, price: 8.52 })
    })

    it('has no bar for an empty chart', () => {
      expect(rangeLocator([])(T0)).toBeNull()
    })

    it('goes by when a bar really began, not by the place the chart gave it', () => {
      // Three bars born in the same ten seconds: the chart is given 10:00:10, :11 and :12 for them.
      const burst = placeRangeBars(
        [range(0, 20), range(10, 10), range(11, 10, { began: T0 + 10, ended: T0 + 20 }), range(12, 20, { began: T0 + 10, ended: T0 + 30 })],
        PARIS,
      )
      const find = rangeLocator(burst)

      // What happened in those ten seconds is on the last of them, the one still growing when they ended.
      expect(find(T0 + 10)).toBe(SHOWN + 12)
      expect(find(T0 + 15)).toBe(SHOWN + 12)
      expect(find(T0 + 9)).toBe(SHOWN)
    })
  })

  it('counts a range in ticks and back, without a hair of rounding', () => {
    expect(ticksOf(0.6, 0.01)).toBe(60)
    expect(ticksOf(15, 0.01)).toBe(1500)
    expect(ticksOf(0.07, 0.001)).toBe(70)
    expect(sizeOf(60, 0.01, 2)).toBe(0.6)
    expect(sizeOf(7, 0.01, 2)).toBe(0.07)
    expect(sizeOf(3, 0.001, 3)).toBe(0.003)
  })

  it('steps a range along 1, 2, 5, 10, 20, 50…, never under one tick', () => {
    const up = (ticks: number) => stepRange(ticks, 1)
    const down = (ticks: number) => stepRange(ticks, -1)

    expect([1, 2, 5, 10, 20, 50, 100].map(up)).toEqual([2, 5, 10, 20, 50, 100, 200])
    expect([200, 100, 50, 20, 10, 5, 2, 1].map(down)).toEqual([100, 50, 20, 10, 5, 2, 1, 1])
    // From a size that is not on a rung, to the nearest rung on that side.
    expect([up(60), down(60), up(3), down(3), up(1500), down(1500)]).toEqual([100, 50, 5, 2, 2000, 1000])
    expect(up(0)).toBe(2)
  })

  it('writes the clock of a chart time without its day', () => {
    expect(formatChartClock(chartTime(utc('2026-10-12T08:00:20Z'), PARIS))).toBe('10:00:20')
  })

  it('reads the decimals of a list of values, two at least, the missing ones aside', () => {
    expect(valueDecimals([25000, 25000.5, null])).toBe(2)
    expect(valueDecimals([0.855])).toBe(3)
    expect(valueDecimals([])).toBe(2)
  })
})

describe('instruments', () => {
  const instrument = (changes: Partial<PriceInstrument>): PriceInstrument =>
    ({ id: 1, uic: 1, role: 'turbo', direction: 'long', barrier: 24150, issuer: 'SG', symbol: 'S', description: 'D', ...changes }) as PriceInstrument

  it('names a turbo by its direction, knock-out level and issuer, and an index by its name', () => {
    expect(instrumentLabel(instrument({}))).toBe('Long 24,150 · SG')
    expect(instrumentLabel(instrument({ direction: 'short', barrier: 25900.5, issuer: null }))).toBe('Short 25,900.5')
    expect(instrumentLabel(instrument({ role: 'underlying', description: 'NASDAQ 100' }))).toBe('NASDAQ 100')
    expect(instrumentLabel(instrument({ role: 'underlying', description: null, symbol: null, uic: 1909050 }))).toBe('Index 1909050')
  })

  it('lists the index, then each direction from the nearest knock-out level', () => {
    const groups = groupInstruments([
      instrument({ id: 2, barrier: 24150 }),
      instrument({ id: 3, barrier: 24450 }),
      instrument({ id: 5, direction: 'short', barrier: 26200 }),
      instrument({ id: 6, direction: 'short', barrier: 25900 }),
      instrument({ id: 1, role: 'underlying', direction: null, barrier: null }),
    ])

    expect(groups.index.map((item) => item.id)).toEqual([1])
    expect(groups.long.map((item) => item.barrier)).toEqual([24450, 24150])
    expect(groups.short.map((item) => item.barrier)).toEqual([25900, 26200])
  })
})
