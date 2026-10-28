import { BarChart, ChartLegend, formatDay, formatMoney, formatMoneyCompact, formatPercent, LineChart } from 'ioiti-ui'
import type { BarDatum, ChartDetail, ChartLegendItem, LinePoint } from 'ioiti-ui'

import type { BalancePoint, DailyPerformance } from '@/lib/types'

import { compareColor } from './compare'

// The charts are ioiti-ui's (LineChart, BarChart, ChartLegend): what is WATA's is here, which figures each
// shows and how they are said.

const CHART_HEIGHT = 260
const DISTRIBUTION_HEIGHT = 200

/** A day of the API ("2026-10-12") as a moment of a chart's time axis, read in UTC: the day itself, never shifted. */
function dayMoment(day: string): number {
  return Date.parse(`${day}T00:00:00Z`)
}

/** The day a moment of `dayMoment` stands for, as the tooltip names it. */
function dayTitle(t: number): string {
  return formatDay(new Date(t).toISOString().slice(0, 10))
}

function percentTick(value: number): string {
  return `${value}%`
}

interface ChartProps {
  days: DailyPerformance[]
  currency?: string
  /** What the figures of a day are: an amount, or a percentage (a backtest has no amounts). */
  unit?: 'money' | 'percent'
}

function amountOf(unit: 'money' | 'percent', currency: string): (value: number) => string {
  return (value) => (unit === 'percent' ? formatPercent(value, true) : formatMoney(value, currency, true))
}

/** Cumulative realised profit and loss: one series, so no legend; the title names it. */
export function CumulativeProfitChart({ days, currency = 'EUR', unit = 'money' }: ChartProps) {
  const show = amountOf(unit, currency)
  return (
    <LineChart
      ariaLabel="Cumulative profit and loss, day by day"
      data={days.map((day) => ({ t: dayMoment(day.day_date), cumulative: day.cumulative_profit_loss }))}
      series={[{ key: 'cumulative', label: 'Cumulative' }]}
      area
      zero
      height={CHART_HEIGHT}
      timeZone="UTC"
      formatValue={show}
      formatTick={unit === 'percent' ? percentTick : formatMoneyCompact}
      formatTime={dayTitle}
      details={(_, index) => [{ label: 'That day', value: show(days[index].profit_loss) }]}
    />
  )
}

/** Profit or loss of each day, as bars above or below zero. */
export function DailyProfitChart({ days, currency = 'EUR', unit = 'money' }: ChartProps) {
  const show = amountOf(unit, currency)
  return (
    <BarChart
      ariaLabel="Profit and loss of each day"
      data={days.map((day) => ({ label: formatDay(day.day_date, false), profit_loss: day.profit_loss }))}
      series={[{ key: 'profit_loss', label: 'Result' }]}
      tone="sign"
      height={CHART_HEIGHT}
      formatValue={show}
      formatTick={unit === 'percent' ? percentTick : formatMoneyCompact}
      formatTitle={(_, index) => formatDay(days[index].day_date)}
      details={(_, index) => {
        const day = days[index]
        return [
          // In percent the day's result is that figure already.
          ...(unit === 'percent' ? [] : [{ label: 'Day performance', value: formatPercent(day.performance_percent, true) }]),
          { label: 'Trades', value: `${day.trade_count} (${day.win_count} won, ${day.loss_count} lost)` },
        ]
      }}
      showLegend={false}
    />
  )
}

/** Key for the profit and loss bars: what the two colours mean. */
export function ProfitLossLegend({ profit = 'Profit day', loss = 'Loss day' }: { profit?: string; loss?: string }) {
  return (
    <ChartLegend
      items={[
        { label: profit, color: 'var(--chart-positive)' },
        { label: loss, color: 'var(--chart-negative)' },
      ]}
    />
  )
}

interface ProfitGroup {
  profit_loss: number
  trade_count: number
  win_rate: number | null
}

interface ProfitByGroupChartProps<T extends ProfitGroup> {
  groups: T[]
  /** What a group is called on the axis and in the tooltip. */
  label: (group: T) => string
  /** What the profit of a group is: an amount, or points of percent (a backtest counted without a capital). */
  unit?: 'money' | 'percent'
  /** More to say of a group under the pointer. */
  more?: (group: T) => ChartDetail[]
}

/**
 * Profit or loss of each group (hour of entry, holding time...), as bars above
 * or below zero. The groups have an order of their own, which the axis keeps,
 * each named.
 */
export function ProfitByGroupChart<T extends ProfitGroup>({ groups, label, unit = 'money', more }: ProfitByGroupChartProps<T>) {
  const show = amountOf(unit, 'EUR')
  return (
    <BarChart
      ariaLabel="Profit and loss by group"
      data={groups.map((group) => ({ label: label(group), profit_loss: group.profit_loss }))}
      series={[{ key: 'profit_loss', label: 'Result' }]}
      tone="sign"
      height={CHART_HEIGHT}
      formatValue={show}
      formatTick={unit === 'percent' ? percentTick : formatMoneyCompact}
      tickAt={() => true}
      details={(_, index) => {
        const group = groups[index]
        return [
          { label: 'Trades', value: String(group.trade_count) },
          { label: 'Won', value: formatPercent(group.win_rate, false, 0) },
          ...(more ? more(group) : []),
        ]
      }}
      showLegend={false}
    />
  )
}

export interface DistributionBin {
  from: number
  to: number
  count: number
  /** The one result set against the others falls in this bin. */
  marked: boolean
}

interface DistributionChartProps {
  bins: DistributionBin[]
  /** What the counted results are, in the tooltip: "Random runs". */
  counted: string
  /** The result set against them, and what to call it: "This run". */
  own: number
  ownLabel: string
}

/**
 * How many results fell in each range of percent, with one result of its own
 * marked among them. The others are context, in one muted colour; the marked
 * one has a line, a label and the series colour.
 */
export function DistributionChart({ bins, counted, own, ownLabel }: DistributionChartProps) {
  const markedAt = bins.findIndex((bin) => bin.marked)
  const range = (bin: DistributionBin) => `${formatPercent(bin.from, true, 1)} to ${formatPercent(bin.to, true, 1)}`
  return (
    <BarChart
      ariaLabel={`${counted}, by result`}
      data={bins.map((bin) => ({ label: formatPercent(bin.from, true, 1), count: bin.count }))}
      series={[{ key: 'count', label: counted }]}
      marked={markedAt >= 0 ? { index: markedAt, label: ownLabel } : undefined}
      integer
      height={DISTRIBUTION_HEIGHT}
      formatValue={String}
      formatTick={String}
      formatTitle={(_, index) => range(bins[index])}
      details={(_, index) => (bins[index].marked ? [{ label: ownLabel, value: formatPercent(own, true) }] : [])}
      showLegend={false}
    />
  )
}

/**
 * Total account value over time. A line, not an area: the value is nowhere
 * near zero, and a fill down to a cut-off axis would exaggerate its moves.
 */
export function BalanceChart({ points, currency = 'EUR' }: { points: BalancePoint[]; currency?: string }) {
  return (
    <LineChart
      ariaLabel="Account value, day by day"
      data={points.map((point) => ({ t: dayMoment(point.date_day), value: point.total_value }))}
      series={[{ key: 'value', label: 'Account value' }]}
      height={CHART_HEIGHT}
      timeZone="UTC"
      formatValue={(value) => formatMoney(value, currency)}
      formatTick={formatMoneyCompact}
      formatTime={dayTitle}
    />
  )
}

/** One thing among those compared: a name, and a value per day. */
export interface NamedCurve {
  id: string
  name: string
  points: { day: string; value: number }[]
}

/** Which line is which. Always shown: the colour of a line is not enough to know it by. */
export function CurvesLegend({ curves }: { curves: NamedCurve[] }) {
  return (
    <ChartLegend
      items={curves.map((curve, index) => {
        const last = curve.points[curve.points.length - 1]
        return {
          label: curve.name,
          color: compareColor(index),
          shape: 'line' as const,
          value: last ? formatPercent(last.value, true) : undefined,
        }
      })}
    />
  )
}

/** Several results over the same days, in percent, on one scale. A day one run did not test is no gap in its line. */
export function CompareCurvesChart({ curves }: { curves: NamedCurve[] }) {
  const values = curves.map((curve) => new Map(curve.points.map((point) => [point.day, point.value])))
  const days = [...new Set(curves.flatMap((curve) => curve.points.map((point) => point.day)))].sort()
  // Keys of their own: a curve's id could be the chart's `t`.
  const data: LinePoint[] = days.map((day) => ({
    t: dayMoment(day),
    ...Object.fromEntries(curves.map((_, index) => [`c${index}`, values[index].get(day) ?? null])),
  }))
  return (
    <LineChart
      ariaLabel="Results compared, day by day"
      data={data}
      series={curves.map((curve, index) => ({ key: `c${index}`, label: curve.name, color: compareColor(index) }))}
      zero
      connectGaps
      height={CHART_HEIGHT}
      timeZone="UTC"
      formatValue={(value) => formatPercent(value, true)}
      formatTick={percentTick}
      formatTime={dayTitle}
      showLegend={false}
    />
  )
}

// ── the spread of an asset ──

interface SpreadSlot {
  slot: string
  bars: number
  spread_average: number | null
  spread_widest: number | null
  without_ask: number
}

/**
 * The average spread of each slot of the day (half hours of the trading
 * timezone): when an asset is dear to trade. One series, named by the title;
 * the widest spread and the bars of a slot are in its tooltip.
 */
export function SpreadByTimeChart({ slots, decimals }: { slots: SpreadSlot[]; decimals: number }) {
  const show = (value: number | null) => (value === null ? '—' : value.toFixed(decimals))
  return (
    <BarChart
      ariaLabel="Average spread by time of day"
      data={slots.map((slot) => ({ label: slot.slot, spread: slot.spread_average }))}
      series={[{ key: 'spread', label: 'Average spread' }]}
      height={CHART_HEIGHT}
      formatValue={(value) => value.toFixed(decimals)}
      formatTick={(value) => value.toFixed(decimals)}
      formatTitle={(_, index) => `From ${slots[index].slot}`}
      details={(_, index) => {
        const slot = slots[index]
        return [
          { label: 'Widest', value: show(slot.spread_widest) },
          { label: 'Bars', value: String(slot.bars) },
          ...(slot.without_ask > 0 ? [{ label: 'Without an ask', value: String(slot.without_ask) }] : []),
        ]
      }}
      showLegend={false}
    />
  )
}

/** A line of a chart over time, and how it is told apart: by its colour, and by its dash beside a solid one. */
export interface SeriesLine {
  key: string
  label: string
  color: string
  dashed?: boolean
}

/** A figure's limit drawn across a chart, named over its line. */
export interface SeriesLimit {
  value: number
  label: string
}

/** A bar of time (`t`, epoch seconds) and its figures; `null` where nothing was measured. */
type SeriesPoint = { t: number } & Record<string, number | null | boolean>

interface TimeSeriesChartProps {
  points: SeriesPoint[]
  lines: SeriesLine[]
  /** A value as the axis and the tooltip say it. */
  format: (value: number) => string
  /** The bar of time a moment starts, as the tooltip names it (epoch seconds). */
  title: (t: number) => string
  /** The time zone the axis's round moments are read in. */
  timeZone: string
  limit?: SeriesLimit | null
  /** Moments drawn across the chart: the engine's restarts (epoch seconds). */
  marks?: { t: number; label: string }[]
}

/** The figures of the lines alone, a moment in milliseconds: what LineChart reads. */
function linePoints(points: SeriesPoint[], lines: SeriesLine[]): LinePoint[] {
  return points.map((point) => ({
    t: point.t * 1000,
    ...Object.fromEntries(lines.map((line) => [line.key, typeof point[line.key] === 'number' ? point[line.key] : null])),
  }))
}

/**
 * Figures of the same kind over time, one scale (milliseconds, or megabytes): a bar of time without
 * any is a gap. A limit is drawn and named where one is given; the restarts are lines across.
 */
export function TimeSeriesChart({ points, lines, format, title, timeZone, limit, marks = [] }: TimeSeriesChartProps) {
  return (
    <LineChart
      ariaLabel={lines.map((line) => line.label).join(', ')}
      data={linePoints(points, lines)}
      series={lines.map((line) => ({ key: line.key, label: line.label, color: line.color, dashed: line.dashed }))}
      references={limit ? [{ value: limit.value, label: limit.label, tone: 'negative' }] : []}
      markers={marks.map((mark) => ({ t: mark.t * 1000, label: mark.label }))}
      height={CHART_HEIGHT}
      timeZone={timeZone}
      formatValue={format}
      formatTick={format}
      formatTime={(t) => title(t / 1000)}
      showLegend={false}
    />
  )
}

/** Which line is which: always there for two of them or more, a colour is not enough to know a line by. */
export function SeriesLegend({ lines, marks }: { lines: SeriesLine[]; marks?: string }) {
  const items: ChartLegendItem[] = lines.map((line) => ({
    label: line.label,
    color: line.color,
    shape: line.dashed ? 'dashed' : 'line',
  }))
  if (marks) items.push({ label: marks, color: 'var(--chart-axis)', shape: 'marker' })
  return <ChartLegend items={items} />
}

/** Counts by bar of time, stacked: each bar of time whole, its parts in the order of the legend. */
export function CountBarsChart({
  points,
  bars,
  clock,
  title,
  ticks,
}: {
  points: SeriesPoint[]
  bars: SeriesLine[]
  /** A bar of time as the axis names it (epoch seconds). */
  clock: (t: number) => string
  title: (t: number) => string
  /** Bars of the time axis it is marked at: among `points`' own. */
  ticks?: number[]
}) {
  const named = ticks ? new Set(ticks) : null
  const data: BarDatum[] = points.map((point) => ({
    label: clock(point.t),
    ...Object.fromEntries(bars.map((bar) => [bar.key, typeof point[bar.key] === 'number' ? point[bar.key] : 0])),
  }))
  return (
    <BarChart
      ariaLabel={bars.map((bar) => bar.label).join(', ')}
      data={data}
      series={bars.map((bar) => ({ key: bar.key, label: bar.label, color: bar.color }))}
      variant="stacked"
      integer
      height={CHART_HEIGHT}
      formatValue={String}
      formatTick={String}
      formatTitle={(_, index) => title(points[index].t)}
      tickAt={named ? (_, index) => named.has(points[index].t) : undefined}
      showLegend={false}
    />
  )
}
