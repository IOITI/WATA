import { describe, expect, it } from 'vitest'

import type { PriceBarRow } from '@/lib/types'

import { chartTime } from './chartData'
import { bucketOf, fold, quoteAge, rowCandle, rowVolume, startTail, tailRows, withBar, withQuote } from './liveTail'

// Thursday 15 Oct 2026, 08:00:00 UTC.
const T = 1792051200

function bar(t: number, bid: number, updates = 1, ask: number | null = bid + 1): PriceBarRow {
  return [t, bid, bid + 0.5, bid - 0.5, bid + 0.25, ask, ask === null ? null : ask + 0.5, ask === null ? null : ask - 0.5, ask === null ? null : ask + 0.25, updates]
}

/** The server's rule (src/admin/data.py, aggregate), written again here to hold the fold to it. */
function aggregate(rows: PriceBarRow[], interval: number): PriceBarRow[] {
  const out: PriceBarRow[] = []
  for (const row of rows) {
    const bucket = row[0] - (row[0] % interval)
    const last = out[out.length - 1]
    if (!last || last[0] !== bucket) {
      out.push([bucket, ...row.slice(1)] as PriceBarRow)
      continue
    }
    last[2] = Math.max(last[2], row[2])
    last[3] = Math.min(last[3], row[3])
    last[4] = row[4]
    if (last[5] === null) last[5] = row[5]
    if (row[6] !== null) last[6] = last[6] === null ? row[6] : Math.max(last[6], row[6])
    if (row[7] !== null) last[7] = last[7] === null ? row[7] : Math.min(last[7], row[7])
    if (row[8] !== null) last[8] = row[8]
    last[9] += row[9]
  }
  return out
}

describe('folding the stream into the bars shown', () => {
  it('folds ten seconds as the server merges them: first open, highest high, lowest low, last close', () => {
    const merged = fold(fold(bar(T, 100, 2), bar(T + 10, 103, 3)), bar(T + 20, 99, 1, null))

    expect(merged).toEqual([T, 100, 103.5, 98.5, 99.25, 101, 104.5, 100.5, 104.25, 6])
  })

  it('gives, ten seconds after ten seconds, exactly the bars the server would read afterwards', () => {
    for (const interval of [10, 30, 60, 300]) {
      const all = Array.from({ length: 90 }, (_, index) =>
        bar(T + 10 * index, 25000 + ((index * 37) % 11) - 5, (index % 4) + 1, index % 7 === 0 ? null : 25001 + index),
      )
      // Read up to some ten seconds of the period, then followed live.
      const cut = 41
      const read = aggregate(all.slice(0, cut), interval)
      let tail = startTail(read, all[cut - 1][0], interval)
      const drawn = new Map<number, PriceBarRow>()
      for (const row of all.slice(cut)) {
        tail = withBar(tail, row)
        for (const shown of tailRows(tail)) drawn.set(shown[0], shown)
      }
      const expected = aggregate(all, interval).filter((row) => drawn.has(row[0]))
      expect(expected.length).toBeGreaterThan(0)
      for (const row of expected) expect(drawn.get(row[0])).toEqual(row)
    }
  })

  it('never counts again what the bars read already hold', () => {
    let tail = startTail([bar(T, 100, 3)], T + 20, 60)

    tail = withBar(tail, bar(T + 10, 500, 9))
    tail = withQuote(tail, bar(T + 20, 600, 9))

    expect(tailRows(tail)).toEqual([bar(T, 100, 3)])
  })

  it('shows the ten seconds in progress inside the bar they are part of, or as the first of the next', () => {
    let tail = startTail([bar(T, 100, 3)], T + 40, 60)

    tail = withQuote(tail, bar(T + 50, 102, 2))
    expect(tailRows(tail)).toEqual([[T, 100, 102.5, 99.5, 102.25, 101, 103.5, 100.5, 103.25, 5]])

    tail = withQuote(tail, bar(T + 60, 104, 1))
    expect(tailRows(tail)).toEqual([bar(T, 100, 3), [T + 60, ...bar(T + 60, 104, 1).slice(1)]])
  })

  it('takes the closed ten seconds in place of the quotes that made them, and keeps later quotes', () => {
    let tail = startTail([], null, 10)
    tail = withQuote(tail, bar(T, 100, 2))
    tail = withQuote(tail, bar(T + 10, 101, 1))

    // The first ten seconds close, as recorded: one more quote than the browser saw.
    tail = withBar(tail, bar(T, 100.5, 3))

    expect(tailRows(tail)).toEqual([bar(T, 100.5, 3), bar(T + 10, 101, 1)])
  })

  it('starts from nothing on a day without a bar yet', () => {
    expect(tailRows(startTail([], null, 60))).toEqual([])
    expect(tailRows(withQuote(startTail([], null, 60), bar(T + 30, 100)))).toEqual([[T, ...bar(T + 30, 100).slice(1)]])
  })
})

describe('what the chart is given', () => {
  it('a candle of the side shown, in the chart time of the trading timezone', () => {
    const row = bar(T, 100)

    expect(rowCandle(row, 'bid', 'Europe/Paris')).toEqual({ time: chartTime(T, 'Europe/Paris'), open: 100, high: 100.5, low: 99.5, close: 100.25 })
    expect(rowCandle(row, 'ask', 'UTC')).toEqual({ time: T, open: 101, high: 101.5, low: 100.5, close: 101.25 })
    expect(rowCandle(bar(T, 100, 1, null), 'ask', 'UTC')).toBeNull()
    expect(rowVolume(bar(T, 100, 7), 'UTC')).toEqual({ time: T, value: 7 })
  })

  it('says how long ago Capital.com quoted, on the server clock', () => {
    // The browser's clock is 2 s behind the server's.
    expect(quoteAge({ t: 1_000_000 }, 2000, 998_100)).toBe(100)
    expect(quoteAge({ t: null }, 0, 1)).toBeNull()
    expect(quoteAge({ t: 2_000_000 }, 0, 1_000_000)).toBe(0)
  })

  it('places a moment in the bar of its interval', () => {
    expect(bucketOf(T + 59, 60)).toBe(T)
    expect(bucketOf(T + 60, 60)).toBe(T + 60)
    expect(bucketOf(T + 9, 10)).toBe(T)
  })
})
