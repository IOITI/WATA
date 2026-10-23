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
