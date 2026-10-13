import { Link } from 'react-router'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { BacktestStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

import { STATE_LABEL, STATE_TONE } from './model'
import type { Change } from './model'

/** What the pages of this section share. */

export function StateBadge({ status }: { status: BacktestStatus }) {
  return (
    <Badge tone={STATE_TONE[status.state]} dot pulse={status.state === 'running'}>
      {STATE_LABEL[status.state]}
    </Badge>
  )
}

/** Each thing a run changed, with what it was before. */
export function ChangeList({ changes, none }: { changes: readonly Change[]; none: string }) {
  if (changes.length === 0) return <p className="text-sm text-muted-foreground">{none}</p>
  return (
    <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[max-content_1fr]">
      {changes.map((change) => (
        <div key={change.name} className="contents">
          <dt className="text-muted-foreground">{change.label}</dt>
          <dd className="tabular-nums">
            <span className="text-muted-foreground line-through decoration-muted-foreground/50">{change.before}</span>
            <span aria-hidden className="mx-1.5 text-muted-foreground">
              →
            </span>
            <span className="sr-only"> changed to </span>
            <span className="font-medium">{change.after}</span>
          </dd>
        </div>
      ))}
    </dl>
  )
}

interface LeveragePickerProps {
  leverages: readonly number[]
  value: number
  onChange: (leverage: number) => void
}

/**
 * Which assumption about the turbo the figures are shown for. The leverage is
 * not known, so a run is made with several: a result that holds for one only
 * is not a result.
 */
export function LeveragePicker({ leverages, value, onChange }: LeveragePickerProps) {
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Leverage assumed for the turbo">
      <span className="text-xs text-muted-foreground">Index points for 1.00 of turbo</span>
      {leverages.map((leverage) => (
        <Button
          key={leverage}
          // Filled: the one cue that says which assumption the page shows.
          variant={leverage === value ? 'primary' : 'outline'}
          size="sm"
          aria-pressed={leverage === value}
          onClick={() => onChange(leverage)}
        >
          {leverage}
        </Button>
      ))}
    </div>
  )
}

export function BacktestsDisabled() {
  return (
    <Alert tone="warning">
      <AlertTitle>Backtests are switched off</AlertTitle>
      <AlertDescription>
        Set <code>admin.backtests_enabled</code> to <code>true</code> in the configuration, then restart the admin
        service.
      </AlertDescription>
    </Alert>
  )
}

export function BackToRuns({ className }: { className?: string }) {
  return (
    <Link to="/backtests" className={cn('text-xs text-muted-foreground hover:text-foreground', className)}>
      ← All backtests
    </Link>
  )
}
