import { formatDay, formatMoney, formatMoneyCompact, formatPercent } from 'ioiti-ui'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { BalancePoint, DailyPerformance } from '@/lib/types'

import { compareColor } from './compare'

const AXIS_TICK = { fill: 'var(--muted-foreground)', fontSize: 11 }
const CHART_MARGIN = { top: 8, right: 12, bottom: 0, left: 0 }
// Plot plus the x-axis band, so the day labels are never clipped by the card.
const CHART_HEIGHT = 260
const BAR_END_RADIUS = 4

interface TooltipRow {
  label: string
  value: string
  /** Colour of the mark this row belongs to, shown as a short key beside the label. */
  keyColor?: string
}

/** The value leads and the label follows: the reader already knows the series and wants the number. */
function TooltipCard({ title, rows }: { title: string; rows: TooltipRow[] }) {
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-elevated">
      <p className="mb-1.5 text-muted-foreground">{title}</p>
      <dl className="space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-6">
            <dt className="flex items-center gap-1.5 text-muted-foreground">
              {row.keyColor && (
                <span aria-hidden className="h-0.5 w-3 rounded-full" style={{ backgroundColor: row.keyColor }} />
              )}
              {row.label}
            </dt>
            <dd className="font-semibold tabular-nums text-foreground">{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

interface DayTooltipProps {
  active?: boolean
  payload?: readonly { payload?: DailyPerformance }[]
  rows: (day: DailyPerformance) => TooltipRow[]
}

function DayTooltip({ active, payload, rows }: DayTooltipProps) {
  const day = payload?.[0]?.payload
  if (!active || !day) return null
  return <TooltipCard title={formatDay(day.day_date)} rows={rows(day)} />
}

function dayTick(value: string): string {
  return formatDay(value, false)
}

interface ChartProps {
  days: DailyPerformance[]
  currency?: string
  /** What the figures of a day are: an amount, or a percentage (a backtest has no amounts). */
  unit?: 'money' | 'percent'
}

function percentTick(value: number): string {
  return `${value}%`
}

/** Cumulative realised profit and loss: one series, so no legend; the title names it. */
export function CumulativeProfitChart({ days, currency = 'EUR', unit = 'money' }: ChartProps) {
  const show = (value: number) => (unit === 'percent' ? formatPercent(value, true) : formatMoney(value, currency, true))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <AreaChart data={days} margin={CHART_MARGIN}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="day_date"
          tickFormatter={dayTick}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={40}
        />
        <YAxis
          tickFormatter={unit === 'percent' ? percentTick : formatMoneyCompact}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          content={
            <DayTooltip
              rows={(day) => [
                { label: 'Cumulative', value: show(day.cumulative_profit_loss), keyColor: 'var(--chart-series)' },
                { label: 'That day', value: show(day.profit_loss) },
              ]}
            />
          }
        />
        <Area
          type="linear"
          dataKey="cumulative_profit_loss"
          stroke="var(--chart-series)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          fill="var(--chart-series)"
          fillOpacity={0.1}
          dot={false}
          activeDot={{ r: 4, fill: 'var(--chart-series)', stroke: 'var(--card)', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

interface BarShapeProps {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: { profit_loss?: number }
}

/**
 * A bar grown from the zero line, rounded at its far end only. The side of the
 * line it is on tells profit from loss; the colour repeats it.
 */
function ProfitBar({ x = 0, y = 0, width = 0, height = 0, payload }: BarShapeProps) {
  const profitable = (payload?.profit_loss ?? 0) >= 0
  const top = height < 0 ? y + height : y
  const size = Math.abs(height)
  if (size === 0 || width <= 0) return null

  const radius = Math.min(BAR_END_RADIUS, width / 2, size)
  const right = x + width
  const bottom = top + size
  const path = profitable
    ? `M${x},${bottom} V${top + radius} Q${x},${top} ${x + radius},${top} H${right - radius} Q${right},${top} ${right},${top + radius} V${bottom} Z`
    : `M${x},${top} V${bottom - radius} Q${x},${bottom} ${x + radius},${bottom} H${right - radius} Q${right},${bottom} ${right},${bottom - radius} V${top} Z`

  return <path d={path} fill={profitable ? 'var(--chart-positive)' : 'var(--chart-negative)'} />
}

/** Profit or loss of each day, as bars above or below zero. */
export function DailyProfitChart({ days, currency = 'EUR', unit = 'money' }: ChartProps) {
  const show = (value: number) => (unit === 'percent' ? formatPercent(value, true) : formatMoney(value, currency, true))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={days} margin={CHART_MARGIN} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="day_date"
          tickFormatter={dayTick}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          minTickGap={40}
        />
        <YAxis
          tickFormatter={unit === 'percent' ? percentTick : formatMoneyCompact}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <DayTooltip
              rows={(day) => [
                {
                  label: day.profit_loss >= 0 ? 'Profit' : 'Loss',
                  value: show(day.profit_loss),
                  keyColor: day.profit_loss >= 0 ? 'var(--chart-positive)' : 'var(--chart-negative)',
                },
                // In percent the day's result is that figure already.
                ...(unit === 'percent'
                  ? []
                  : [{ label: 'Day performance', value: formatPercent(day.performance_percent, true) }]),
                { label: 'Trades', value: `${day.trade_count} (${day.win_count} won, ${day.loss_count} lost)` },
              ]}
            />
          }
        />
        <Bar dataKey="profit_loss" maxBarSize={24} shape={ProfitBar} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}

/** Key for the profit and loss bars: what the two colours mean. */
export function ProfitLossLegend({ profit = 'Profit day', loss = 'Loss day' }: { profit?: string; loss?: string }) {
  return (
    <ul className="flex items-center gap-4 text-xs text-muted-foreground">
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="size-2.5 rounded-[2px]" style={{ backgroundColor: 'var(--chart-positive)' }} />
        {profit}
      </li>
      <li className="flex items-center gap-1.5">
        <span aria-hidden className="size-2.5 rounded-[2px]" style={{ backgroundColor: 'var(--chart-negative)' }} />
        {loss}
      </li>
    </ul>
  )
}

interface GroupTooltipProps<T> {
  active?: boolean
  payload?: readonly { payload?: T }[]
  title: (entry: T) => string
  rows: (entry: T) => TooltipRow[]
}

function GroupTooltip<T>({ active, payload, title, rows }: GroupTooltipProps<T>) {
  const entry = payload?.[0]?.payload
  if (!active || !entry) return null
  return <TooltipCard title={title(entry)} rows={rows(entry)} />
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
  more?: (group: T) => TooltipRow[]
}

/**
 * Profit or loss of each group (hour of entry, holding time...), as bars above
 * or below zero. The groups have an order of their own, which the axis keeps.
 */
export function ProfitByGroupChart<T extends ProfitGroup>({
  groups,
  label,
  unit = 'money',
  more,
}: ProfitByGroupChartProps<T>) {
  const data = groups.map((group) => ({ ...group, axis_label: label(group) }))
  const show = (value: number) => (unit === 'percent' ? formatPercent(value, true) : formatMoney(value, 'EUR', true))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={data} margin={CHART_MARGIN} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="axis_label" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={0} />
        <YAxis
          tickFormatter={unit === 'percent' ? percentTick : formatMoneyCompact}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <GroupTooltip<T>
              title={label}
              rows={(group) => [
                {
                  label: group.profit_loss >= 0 ? 'Profit' : 'Loss',
                  value: show(group.profit_loss),
                  keyColor: group.profit_loss >= 0 ? 'var(--chart-positive)' : 'var(--chart-negative)',
                },
                { label: 'Trades', value: String(group.trade_count) },
                { label: 'Won', value: formatPercent(group.win_rate, false, 0) },
                ...(more ? more(group) : []),
              ]}
            />
          }
        />
        <Bar dataKey="profit_loss" maxBarSize={24} shape={ProfitBar} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
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

const DISTRIBUTION_HEIGHT = 200

/**
 * How many results fell in each range of percent, with one result of its own
 * marked among them. The others are context, in one muted colour; the marked
 * one has a line, a label and the series colour.
 */
export function DistributionChart({ bins, counted, own, ownLabel }: DistributionChartProps) {
  const data = bins.map((bin, index) => ({ ...bin, index }))
  const marked = data.find((bin) => bin.marked)
  const range = (bin: DistributionBin) => `${formatPercent(bin.from, true, 1)} to ${formatPercent(bin.to, true, 1)}`
  return (
    <ResponsiveContainer width="100%" height={DISTRIBUTION_HEIGHT}>
      <BarChart data={data} margin={{ ...CHART_MARGIN, top: 22 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="index"
          tickFormatter={(index: number) => (data[index] ? formatPercent(data[index].from, true, 1) : '')}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={36}
        />
        <YAxis allowDecimals={false} tick={AXIS_TICK} tickLine={false} axisLine={false} width={36} />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <GroupTooltip<DistributionBin>
              title={range}
              rows={(bin) => [
                { label: counted, value: String(bin.count), keyColor: 'var(--chart-muted)' },
                ...(bin.marked
                  ? [{ label: ownLabel, value: formatPercent(own, true), keyColor: 'var(--chart-series)' }]
                  : []),
              ]}
            />
          }
        />
        <Bar dataKey="count" maxBarSize={28} radius={[BAR_END_RADIUS, BAR_END_RADIUS, 0, 0]} isAnimationActive={false}>
          {data.map((bin) => (
            <Cell key={bin.index} fill={bin.marked ? 'var(--chart-series)' : 'var(--chart-muted)'} />
          ))}
        </Bar>
        {marked && (
          <ReferenceLine
            x={marked.index}
            stroke="var(--chart-series)"
            strokeWidth={2}
            label={{ value: ownLabel, position: 'top', fill: 'var(--foreground)', fontSize: 11 }}
          />
        )}
      </BarChart>
    </ResponsiveContainer>
  )
}

export interface PathPoint {
  /** Epoch seconds. */
  time: number
  last: number
  /** The worst and the best of the same stretch of time. */
  range: [number, number]
  /** A level that follows the best seen so far, once there is one. */
  trailing: number | null
}

export interface PathLine {
  label: string
  value: number
  tone: 'positive' | 'negative'
}

interface PathChartProps {
  points: PathPoint[]
  /** Levels drawn across the chart, each named on it. */
  lines: PathLine[]
  domain: [number, number]
  /** A moment as the axis and the tooltip show it. */
  clock: (time: number) => string
}

const PATH_TONE = { positive: 'var(--chart-positive)', negative: 'var(--chart-negative)' }
const TICK_STEPS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]

/** Round percentages to label a scale with: a handful of them, from one end of it to the other. */
function percentTicks([from, to]: [number, number]): number[] {
  const step = TICK_STEPS.find((candidate) => (to - from) / candidate <= 6) ?? TICK_STEPS[TICK_STEPS.length - 1]
  const ticks = []
  for (let tick = Math.ceil(from / step) * step; tick <= to; tick += step) ticks.push(Math.round(tick * 10) / 10)
  return ticks
}

function PathTooltip({ active, payload, clock }: { active?: boolean; payload?: readonly { payload?: PathPoint }[]; clock: (time: number) => string }) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  return (
    <TooltipCard
      title={clock(point.time)}
      rows={[
        { label: 'Result', value: formatPercent(point.last, true), keyColor: 'var(--chart-series)' },
        { label: 'Worst and best', value: `${formatPercent(point.range[0], true)} to ${formatPercent(point.range[1], true)}` },
        ...(point.trailing === null
          ? []
          : [{ label: 'Trailing stop', value: formatPercent(point.trailing, true), keyColor: 'var(--muted-foreground)' }]),
      ]}
    />
  )
}

/**
 * A result in percent over time, with the worst and the best of each stretch
 * as a band around it, a level that trails behind its best, and the levels it
 * was measured against as lines across. One scale: everything is the same percent.
 */
export function PathChart({ points, lines, domain, clock }: PathChartProps) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <ComposedChart data={points} margin={CHART_MARGIN}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="time"
          type="number"
          domain={['dataMin', 'dataMax']}
          tickFormatter={clock}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={48}
        />
        <YAxis
          domain={domain}
          ticks={percentTicks(domain)}
          tickFormatter={percentTick}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
          allowDataOverflow
        />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }} content={<PathTooltip clock={clock} />} />
        <Area
          type="linear"
          dataKey="range"
          stroke="none"
          fill="var(--chart-series)"
          fillOpacity={0.14}
          isAnimationActive={false}
          activeDot={false}
        />
        <Line
          type="stepAfter"
          dataKey="trailing"
          stroke="var(--muted-foreground)"
          strokeWidth={1.5}
          strokeDasharray="5 4"
          dot={false}
          activeDot={false}
          connectNulls={false}
          isAnimationActive={false}
        />
        <Line
          type="linear"
          dataKey="last"
          stroke="var(--chart-series)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          dot={false}
          activeDot={{ r: 4, fill: 'var(--chart-series)', stroke: 'var(--card)', strokeWidth: 2 }}
          isAnimationActive={false}
        />
        {lines.map((line) => (
          <ReferenceLine
            key={line.label}
            y={line.value}
            stroke={PATH_TONE[line.tone]}
            strokeDasharray="6 4"
            label={{
              // Inside the plot, over its line: beside it, a long name would be cut by the edge of the card.
              value: `${line.label} ${formatPercent(line.value, true)}`,
              position: 'insideTopRight',
              fill: 'var(--foreground)',
              fontSize: 11,
            }}
          />
        ))}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/**
 * Total account value over time. A line, not an area: the value is nowhere
 * near zero, and a fill down to a cut-off axis would exaggerate its moves.
 */
export function BalanceChart({ points, currency = 'EUR' }: { points: BalancePoint[]; currency?: string }) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={points} margin={CHART_MARGIN}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="date_day"
          tickFormatter={dayTick}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={40}
        />
        <YAxis
          domain={['auto', 'auto']}
          tickFormatter={formatMoneyCompact}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <Tooltip
          cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          content={
            <GroupTooltip<BalancePoint>
              title={(point) => formatDay(point.date_day)}
              rows={(point) => [
                {
                  label: 'Account value',
                  value: formatMoney(point.total_value, currency),
                  keyColor: 'var(--chart-series)',
                },
              ]}
            />
          }
        />
        <Line
          type="linear"
          dataKey="total_value"
          stroke="var(--chart-series)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          dot={false}
          activeDot={{ r: 4, fill: 'var(--chart-series)', stroke: 'var(--card)', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

/** One thing among those compared: a name, and a value per day. */
export interface NamedCurve {
  id: string
  name: string
  points: { day: string; value: number }[]
}

interface CurvesTooltipProps {
  active?: boolean
  payload?: readonly { payload?: Record<string, number | string | undefined> }[]
  curves: NamedCurve[]
}

function CurvesTooltip({ active, payload, curves }: CurvesTooltipProps) {
  const day = payload?.[0]?.payload
  if (!active || !day) return null
  return (
    <TooltipCard
      title={formatDay(String(day.day_date))}
      rows={curves
        .map((curve, index) => ({ curve, index, value: day[curve.id] }))
        .filter((row): row is typeof row & { value: number } => typeof row.value === 'number')
        .map(({ curve, index, value }) => ({
          label: curve.name,
          value: formatPercent(value, true),
          keyColor: compareColor(index),
        }))}
    />
  )
}

/** Which line is which. Always shown: the colour of a line is not enough to know it by. */
export function CurvesLegend({ curves }: { curves: NamedCurve[] }) {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
      {curves.map((curve, index) => {
        const last = curve.points[curve.points.length - 1]
        return (
          <li key={curve.id} className="flex items-center gap-1.5">
            <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ backgroundColor: compareColor(index) }} />
            <span className="text-foreground">{curve.name}</span>
            {last && <span className="tabular-nums">{formatPercent(last.value, true)}</span>}
          </li>
        )
      })}
    </ul>
  )
}

/** Several results over the same days, in percent, on one scale. */
export function CompareCurvesChart({ curves }: { curves: NamedCurve[] }) {
  const values = curves.map((curve) => new Map(curve.points.map((point) => [point.day, point.value])))
  const days = [...new Set(curves.flatMap((curve) => curve.points.map((point) => point.day)))].sort()
  const data = days.map((day) => ({
    day_date: day,
    ...Object.fromEntries(curves.map((curve, index) => [curve.id, values[index].get(day)])),
  }))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={data} margin={CHART_MARGIN}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="day_date"
          tickFormatter={dayTick}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={40}
        />
        <YAxis tickFormatter={percentTick} tick={AXIS_TICK} tickLine={false} axisLine={false} width={52} />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          content={<CurvesTooltip curves={curves} />}
        />
        {curves.map((curve, index) => (
          <Line
            key={curve.id}
            type="linear"
            dataKey={curve.id}
            stroke={compareColor(index)}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            dot={false}
            // A day one run did not test is a gap in the days, not in its line.
            connectNulls
            activeDot={{ r: 4, fill: compareColor(index), stroke: 'var(--card)', strokeWidth: 2 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
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

/** A bar grown from zero, rounded at its top only, in the colour of the one series. */
function SeriesBar({ x = 0, y = 0, width = 0, height = 0 }: BarShapeProps) {
  if (height <= 0 || width <= 0) return null
  const radius = Math.min(BAR_END_RADIUS, width / 2, height)
  const right = x + width
  const bottom = y + height
  return (
    <path
      d={`M${x},${bottom} V${y + radius} Q${x},${y} ${x + radius},${y} H${right - radius} Q${right},${y} ${right},${y + radius} V${bottom} Z`}
      fill="var(--chart-series)"
    />
  )
}

/**
 * The average spread of each slot of the day (half hours of the trading
 * timezone): when an asset is dear to trade. One series, named by the title;
 * the widest spread and the bars of a slot are in its tooltip.
 */
export function SpreadByTimeChart({ slots, decimals }: { slots: SpreadSlot[]; decimals: number }) {
  const show = (value: number | null) => (value === null ? '—' : value.toFixed(decimals))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={slots} margin={CHART_MARGIN} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="slot" tick={AXIS_TICK} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis
          tickFormatter={(value: number) => value.toFixed(decimals)}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <GroupTooltip<SpreadSlot>
              title={(slot) => `From ${slot.slot}`}
              rows={(slot) => [
                { label: 'Average spread', value: show(slot.spread_average), keyColor: 'var(--chart-series)' },
                { label: 'Widest', value: show(slot.spread_widest) },
                { label: 'Bars', value: String(slot.bars) },
                ...(slot.without_ask > 0 ? [{ label: 'Without an ask', value: String(slot.without_ask) }] : []),
              ]}
            />
          }
        />
        <Bar dataKey="spread_average" maxBarSize={24} shape={SeriesBar} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
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

type SeriesPoint = { t: number } & Record<string, number | null | boolean>

interface TimeSeriesChartProps {
  points: SeriesPoint[]
  lines: SeriesLine[]
  /** A value as the axis and the tooltip say it. */
  format: (value: number) => string
  /** The start of a bar of time as the axis says it, and as the tooltip names it. */
  clock: (t: number) => string
  title: (t: number) => string
  limit?: SeriesLimit | null
  /** Moments drawn across the chart: the engine's restarts. */
  marks?: { t: number; label: string }[]
  /** Where the time axis is marked: round moments, not where the library would fall. */
  ticks?: number[]
  /** The width of the value axis, for long labels (megabytes). */
  yWidth?: number
}

function SeriesTooltip({
  active,
  payload,
  lines,
  format,
  title,
}: {
  active?: boolean
  payload?: readonly { payload?: SeriesPoint }[]
  lines: SeriesLine[]
  format: (value: number) => string
  title: (t: number) => string
}) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  const rows = lines
    .filter((line) => typeof point[line.key] === 'number')
    .map((line) => ({ label: line.label, value: format(point[line.key] as number), keyColor: line.color }))
  return <TooltipCard title={title(point.t)} rows={rows.length ? rows : [{ label: 'Nothing measured', value: '—' }]} />
}

/**
 * Figures of the same kind over time, one scale (milliseconds, or megabytes): a bar of time without
 * any is a gap. A limit is drawn and named where one is given; the restarts are lines across.
 */
export function TimeSeriesChart({ points, lines, format, clock, title, limit, marks = [], ticks, yWidth = 60 }: TimeSeriesChartProps) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={points} margin={CHART_MARGIN}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="t"
          type="number"
          domain={['dataMin', 'dataMax']}
          tickFormatter={clock}
          ticks={ticks}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
          minTickGap={24}
        />
        <YAxis tickFormatter={format} tick={AXIS_TICK} tickLine={false} axisLine={false} width={yWidth} />
        <Tooltip
          cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          content={<SeriesTooltip lines={lines} format={format} title={title} />}
        />
        {marks.map((mark) => (
          <ReferenceLine key={`${mark.t}-${mark.label}`} x={mark.t} stroke="var(--chart-axis)" strokeDasharray="2 3" />
        ))}
        {limit && (
          <ReferenceLine
            y={limit.value}
            ifOverflow="extendDomain"
            stroke="var(--chart-negative)"
            strokeDasharray="6 4"
            label={{ value: limit.label, position: 'insideTopRight', fill: 'var(--foreground)', fontSize: 11 }}
          />
        )}
        {lines.map((line) => (
          <Line
            key={line.key}
            type="linear"
            dataKey={line.key}
            name={line.label}
            stroke={line.color}
            strokeWidth={2}
            strokeDasharray={line.dashed ? '5 4' : undefined}
            strokeLinejoin="round"
            strokeLinecap="round"
            dot={false}
            activeDot={{ r: 4, fill: line.color, stroke: 'var(--card)', strokeWidth: 2 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Which line is which: always there for two of them or more, a colour is not enough to know a line by. */
export function SeriesLegend({ lines, marks }: { lines: SeriesLine[]; marks?: string }) {
  return (
    <ul aria-label="Legend" className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
      {lines.map((line) => (
        <li key={line.key} className="flex items-center gap-1.5">
          <svg aria-hidden width="16" height="4" className="shrink-0">
            <line x1="0" x2="16" y1="2" y2="2" stroke={line.color} strokeWidth="2" strokeDasharray={line.dashed ? '4 3' : undefined} />
          </svg>
          <span className="text-foreground">{line.label}</span>
        </li>
      ))}
      {marks && (
        <li className="flex items-center gap-1.5">
          <svg aria-hidden width="4" height="12" className="shrink-0">
            <line x1="2" x2="2" y1="0" y2="12" stroke="var(--muted-foreground)" strokeWidth="1" strokeDasharray="2 3" />
          </svg>
          <span>{marks}</span>
        </li>
      )}
    </ul>
  )
}

function CountTooltip({
  active,
  payload,
  bars,
  title,
}: {
  active?: boolean
  payload?: readonly { payload?: SeriesPoint }[]
  bars: SeriesLine[]
  title: (t: number) => string
}) {
  const point = payload?.[0]?.payload
  if (!active || !point) return null
  return (
    <TooltipCard
      title={title(point.t)}
      rows={bars.map((bar) => ({ label: bar.label, value: String(point[bar.key] ?? 0), keyColor: bar.color }))}
    />
  )
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
  clock: (t: number) => string
  title: (t: number) => string
  /** Bars of the time axis it is marked at: among `points`' own. */
  ticks?: number[]
}) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={points} margin={CHART_MARGIN} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis
          dataKey="t"
          tickFormatter={clock}
          ticks={ticks}
          interval={0}
          tick={AXIS_TICK}
          tickLine={false}
          axisLine={{ stroke: 'var(--chart-axis)' }}
        />
        <YAxis allowDecimals={false} tick={AXIS_TICK} tickLine={false} axisLine={false} width={40} />
        <Tooltip cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }} content={<CountTooltip bars={bars} title={title} />} />
        {bars.map((bar) => (
          <Bar
            key={bar.key}
            dataKey={bar.key}
            name={bar.label}
            stackId="counts"
            fill={bar.color}
            stroke="var(--card)"
            strokeWidth={1}
            maxBarSize={24}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}
