import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { formatDay, formatMoney, formatMoneyCompact, formatPercent } from '@/lib/format'
import type { BalancePoint, DailyPerformance } from '@/lib/types'

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
}

/** Cumulative realised profit and loss: one series, so no legend; the title names it. */
export function CumulativeProfitChart({ days, currency = 'EUR' }: ChartProps) {
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
        <YAxis tickFormatter={formatMoneyCompact} tick={AXIS_TICK} tickLine={false} axisLine={false} width={52} />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ stroke: 'var(--muted-foreground)', strokeWidth: 1 }}
          content={
            <DayTooltip
              rows={(day) => [
                {
                  label: 'Cumulative',
                  value: formatMoney(day.cumulative_profit_loss, currency, true),
                  keyColor: 'var(--chart-series)',
                },
                { label: 'That day', value: formatMoney(day.profit_loss, currency, true) },
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
export function DailyProfitChart({ days, currency = 'EUR' }: ChartProps) {
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
        <YAxis tickFormatter={formatMoneyCompact} tick={AXIS_TICK} tickLine={false} axisLine={false} width={52} />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <DayTooltip
              rows={(day) => [
                {
                  label: day.profit_loss >= 0 ? 'Profit' : 'Loss',
                  value: formatMoney(day.profit_loss, currency, true),
                  keyColor: day.profit_loss >= 0 ? 'var(--chart-positive)' : 'var(--chart-negative)',
                },
                { label: 'Day performance', value: formatPercent(day.performance_percent, true) },
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
}

/**
 * Profit or loss of each group (hour of entry, holding time...), as bars above
 * or below zero. The groups have an order of their own, which the axis keeps.
 */
export function ProfitByGroupChart<T extends ProfitGroup>({ groups, label }: ProfitByGroupChartProps<T>) {
  const data = groups.map((group) => ({ ...group, axis_label: label(group) }))
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <BarChart data={data} margin={CHART_MARGIN} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
        <XAxis dataKey="axis_label" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={0} />
        <YAxis tickFormatter={formatMoneyCompact} tick={AXIS_TICK} tickLine={false} axisLine={false} width={52} />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Tooltip
          cursor={{ fill: 'var(--foreground)', fillOpacity: 0.06 }}
          content={
            <GroupTooltip<T>
              title={label}
              rows={(group) => [
                {
                  label: group.profit_loss >= 0 ? 'Profit' : 'Loss',
                  value: formatMoney(group.profit_loss, 'EUR', true),
                  keyColor: group.profit_loss >= 0 ? 'var(--chart-positive)' : 'var(--chart-negative)',
                },
                { label: 'Trades', value: String(group.trade_count) },
                { label: 'Won', value: formatPercent(group.win_rate, false, 0) },
              ]}
            />
          }
        />
        <Bar dataKey="profit_loss" maxBarSize={24} shape={ProfitBar} isAnimationActive={false} />
      </BarChart>
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
