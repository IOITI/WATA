import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

interface KpiTileProps {
  label: string
  /** The figure. Proportional digits: tabular ones look loose at this size. */
  value: ReactNode
  /** One line of context under the figure (the limit, the period...). */
  hint?: ReactNode
  /** A meter or a sparkline. */
  children?: ReactNode
  className?: string
}

export function KpiTile({ label, value, hint, children, className }: KpiTileProps) {
  return (
    <div
      className={cn(
        'flex min-h-[7.5rem] min-w-0 flex-col rounded-2xl border border-border/70 bg-surface/50 p-4 sm:p-5 dark:bg-surface/25',
        className,
      )}
    >
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-2 text-xl font-semibold tracking-tight sm:text-2xl">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      {children && <div className="mt-auto pt-3">{children}</div>}
    </div>
  )
}

type MeterTone = 'neutral' | 'positive' | 'warning' | 'negative'

const METER_FILL: Record<MeterTone, string> = {
  neutral: 'bg-foreground/70',
  positive: 'bg-positive',
  warning: 'bg-warning',
  negative: 'bg-negative',
}

// The track is a faint step of the fill's own colour, so the state reads across the whole bar.
const METER_TRACK: Record<MeterTone, string> = {
  neutral: 'bg-foreground/10',
  positive: 'bg-positive/15',
  warning: 'bg-warning/15',
  negative: 'bg-negative/15',
}

interface MeterProps {
  /** 0 to 1. */
  ratio: number
  tone?: MeterTone
  label: string
  /** What the two ends of the bar stand for, written under it. */
  scale?: [string, string]
}

/** A single ratio against a limit. */
export function Meter({ ratio, tone = 'neutral', label, scale }: MeterProps) {
  const clamped = Math.min(Math.max(ratio, 0), 1)
  return (
    <div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped * 100)}
        className={cn('h-1.5 w-full overflow-hidden rounded-full', METER_TRACK[tone])}
      >
        <div className={cn('h-full rounded-full', METER_FILL[tone])} style={{ width: `${clamped * 100}%` }} />
      </div>
      {scale && (
        <div aria-hidden className="mt-1 flex justify-between text-xs tabular-nums text-muted-foreground">
          <span>{scale[0]}</span>
          <span>{scale[1]}</span>
        </div>
      )}
    </div>
  )
}

interface SparkBarsProps {
  values: number[]
  label: string
}

const SPARK_WIDTH = 120
const SPARK_HEIGHT = 28
const SPARK_GAP = 2

/**
 * Daily figures as small bars around a zero line: earlier days recede, the
 * latest one carries the accent. Direction is told by which side of the line a
 * bar is on, so it needs no colour of its own.
 */
export function SparkBars({ values, label }: SparkBarsProps) {
  if (values.length === 0) return null

  const highest = Math.max(0, ...values)
  const lowest = Math.min(0, ...values)
  const span = highest - lowest || 1
  const zeroY = (highest / span) * SPARK_HEIGHT
  const barWidth = Math.max((SPARK_WIDTH - SPARK_GAP * (values.length - 1)) / values.length, 1)

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${SPARK_WIDTH} ${SPARK_HEIGHT}`}
      className="h-7 w-full max-w-[10rem]"
      preserveAspectRatio="none"
    >
      <line x1={0} x2={SPARK_WIDTH} y1={zeroY} y2={zeroY} stroke="var(--chart-axis)" strokeWidth={1} />
      {values.map((value, index) => {
        const height = Math.max((Math.abs(value) / span) * SPARK_HEIGHT, value === 0 ? 0 : 1)
        return (
          <rect
            key={index}
            x={index * (barWidth + SPARK_GAP)}
            y={value >= 0 ? zeroY - height : zeroY}
            width={barWidth}
            height={height}
            fill={index === values.length - 1 ? 'var(--chart-series)' : 'var(--chart-muted)'}
          />
        )
      })}
    </svg>
  )
}
