import { formatNumber, formatSpan } from '@/lib/format'
import type { PriceBarRow, PriceRangeRow } from '@/lib/types'

import { chartTime, formatChartClock, formatChartTime, RANGE } from './chartData'
import { rangeBarState } from './chartModel'

interface PriceReadoutProps {
  /** The chart time read: under the pointer, or of the last bar. */
  time: number
  bar: PriceBarRow | undefined
  decimals: number
  /** Nothing is pointed at: the figures are those of the last bar. */
  isLast: boolean
  /** The index at that time, under a turbo. */
  indexName?: string
  indexBar?: PriceBarRow
  indexDecimals?: number
}

const OHLC = [
  ['O', 'open'],
  ['H', 'high'],
  ['L', 'low'],
  ['C', 'close'],
] as const

function Quote({ label, values, decimals }: { label: string; values: (number | null)[]; decimals: number }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="flex gap-2 tabular-nums">
        {OHLC.map(([letter, name], index) => (
          <span key={letter}>
            <abbr title={name} className="mr-0.5 text-muted-foreground no-underline">
              {letter}
            </abbr>
            {formatNumber(values[index], decimals)}
          </span>
        ))}
      </dd>
    </div>
  )
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  )
}

/** The figures of one bar, written out: the bar under the pointer, or the last one. */
export function PriceReadout({ time, bar, decimals, isLast, indexName, indexBar, indexDecimals = 2 }: PriceReadoutProps) {
  const when = formatChartTime(time)
  const spread = bar && bar[8] !== null ? bar[8] - bar[4] : null

  return (
    // As tall as its figures at their longest (four lines on a phone, two beside the list of
    // instruments): the chart under it must not jump as the pointer moves over a gap.
    <dl className="flex min-h-[5.25rem] flex-wrap content-start items-baseline gap-x-5 gap-y-1 text-xs sm:min-h-10">
      <div className="flex items-baseline gap-2">
        <dt className="text-muted-foreground">{isLast ? 'Last bar' : 'Bar'}</dt>
        <dd className="tabular-nums">{when}</dd>
      </div>
      {bar ? (
        <>
          <Quote label="Bid" values={[bar[1], bar[2], bar[3], bar[4]]} decimals={decimals} />
          <Quote label="Ask" values={[bar[5], bar[6], bar[7], bar[8]]} decimals={decimals} />
          <Figure label="Spread" value={formatNumber(spread, decimals)} />
          <Figure label="Quote changes" value={formatNumber(bar[9])} />
        </>
      ) : (
        <div className="text-muted-foreground">Nothing recorded</div>
      )}
      {indexName && indexBar && <Figure label={indexName} value={formatNumber(indexBar[4], indexDecimals)} />}
    </dl>
  )
}

interface RangeReadoutProps {
  row: PriceRangeRow | undefined
  /** The chart time pointed at, for when no bar is there (the slot a pause leaves empty). */
  time: number
  timeZone: string
  decimals: number
  /** Nothing is pointed at: the figures are those of the last bar, the one still growing. */
  isLast: boolean
  /** "Bid" or "Ask": what the bars are made of. */
  sideLabel: string
  indexName?: string
  indexDecimals?: number
}

const RANGE_STATES = { complete: null, growing: 'In progress', cut: 'Cut by a pause in the data' }

/**
 * The figures of one range bar: when it really began and ended (not the place
 * the chart gave it on its axis), its prices, how long the price took to cover
 * it, and the index when it ended.
 */
export function RangeReadout({ row, time, timeZone, decimals, isLast, sideLabel, indexName, indexDecimals = 2 }: RangeReadoutProps) {
  const state = row ? RANGE_STATES[rangeBarState(row, isLast)] : null
  const level = row?.[RANGE.index] ?? null

  return (
    // Fewer figures than a bar of a fixed length: one line beside the list of instruments, three on a phone.
    <dl className="flex min-h-[4rem] flex-wrap content-start items-baseline gap-x-5 gap-y-1 text-xs sm:min-h-5">
      <div className="flex items-baseline gap-2">
        <dt className="text-muted-foreground">{isLast ? 'Last bar' : 'Bar'}</dt>
        <dd className="tabular-nums">
          {row
            ? `${formatChartTime(chartTime(row[RANGE.began], timeZone))} → ${formatChartClock(chartTime(row[RANGE.ended], timeZone))}`
            : formatChartTime(time)}
        </dd>
      </div>
      {row ? (
        <>
          <Quote label={sideLabel} values={[row[RANGE.open], row[RANGE.high], row[RANGE.low], row[RANGE.close]]} decimals={decimals} />
          <Figure label="Lasted" value={formatSpan(row[RANGE.ended] - row[RANGE.began])} />
          {state && <div className="text-muted-foreground">{state}</div>}
          {indexName && level !== null && <Figure label={indexName} value={formatNumber(level, indexDecimals)} />}
        </>
      ) : (
        <div className="text-muted-foreground">Nothing recorded</div>
      )}
    </dl>
  )
}
