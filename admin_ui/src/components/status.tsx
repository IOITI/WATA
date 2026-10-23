import type { ReactNode } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { AnimatedNumber } from '@/components/ui/animated-number'
import { Badge } from '@/components/ui/badge'
import type { BadgeTone } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { formatMoney, formatPercent, toneOf } from '@/lib/format'
import type { Tone } from '@/lib/format'
import { actionLabel } from '@/lib/labels'
import type { ServiceStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

const SERVICE_STATUS: Record<ServiceStatus, { tone: BadgeTone; label: string }> = {
  ok: { tone: 'success', label: 'Up' },
  warning: { tone: 'warning', label: 'Degraded' },
  down: { tone: 'danger', label: 'Down' },
  unknown: { tone: 'neutral', label: 'Unknown' },
  disabled: { tone: 'neutral', label: 'Disabled' },
}

/** State of a service: always a word next to the colour, never the colour alone. */
export function ServiceStatusBadge({ status }: { status: ServiceStatus }) {
  const { tone, label } = SERVICE_STATUS[status]
  return (
    <Badge tone={tone} dot={status !== 'disabled'}>
      {label}
    </Badge>
  )
}

/** Beside the name of what Claude made through the admin's MCP endpoint: a run, a script, a rule, an experiment. */
export function ClaudeBadge({ className }: { className?: string }) {
  return (
    <Badge variant="outline" className={className} title="Made by Claude through the admin's MCP endpoint">
      Claude
    </Badge>
  )
}

/** Trade direction. Neutral on purpose: long and short are identities, not good or bad. */
export function ActionBadge({ action }: { action: string | null | undefined }) {
  return <Badge variant="outline">{actionLabel(action)}</Badge>
}

const TONE_TEXT: Record<Tone, string> = {
  positive: 'text-positive',
  negative: 'text-negative',
  neutral: 'text-foreground/80',
}

/** A profit or a loss: signed, so the sign carries the meaning and the colour repeats it. */
export function PnlValue({
  value,
  kind,
  currency,
  className,
  animate = false,
}: {
  value: number | null | undefined
  kind: 'money' | 'percent'
  currency?: string | null
  className?: string
  /** Its digits roll to each new value: for a figure that changes in front of the operator. */
  animate?: boolean
}) {
  const text = kind === 'money' ? formatMoney(value, currency ?? 'EUR', true) : formatPercent(value, true)
  if (animate && typeof value === 'number' && Number.isFinite(value)) {
    return <AnimatedNumber value={value} text={text} className={cn(TONE_TEXT[toneOf(value)], className)} />
  }
  return <span className={cn('tabular-nums', TONE_TEXT[toneOf(value)], className)}>{text}</span>
}

interface QueryStateProps {
  isPending: boolean
  error: Error | null
  /** There is data from an earlier fetch: keep showing it and report the failure above it. */
  hasData: boolean
  skeleton?: ReactNode
  children: ReactNode
}

/** Loading and failure states shared by the pages. A failed refresh never blanks what is on screen. */
export function QueryState({ isPending, error, hasData, skeleton, children }: QueryStateProps) {
  if (isPending && !hasData) {
    return skeleton ?? <Skeleton className="h-40 w-full rounded-2xl" />
  }
  return (
    <>
      {error && (
        <Alert tone="danger" role="alert">
          <AlertTitle>{hasData ? 'Could not refresh: showing the last data received' : 'Could not load'}</AlertTitle>
          <AlertDescription>{error.message}</AlertDescription>
        </Alert>
      )}
      {hasData && children}
    </>
  )
}
