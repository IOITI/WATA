import { Alert, AlertDescription, AlertTitle, Badge } from 'ioiti-ui'
import type { ReactNode } from 'react'

import type { BacktestFate, BacktestStatus } from '@/lib/types'

import { FATE_LABEL, FATE_TONE, STATE_LABEL, STATE_TONE } from './model'
import type { Change } from './model'

/** What the pages of this section share. */

export function StateBadge({ status }: { status: BacktestStatus }) {
  return (
    <Badge tone={STATE_TONE[status.state]} dot pulse={status.state === 'running'}>
      {STATE_LABEL[status.state]}
    </Badge>
  )
}

export function FateBadge({ fate }: { fate: BacktestFate }) {
  return <Badge tone={FATE_TONE[fate]}>{FATE_LABEL[fate]}</Badge>
}

/** Each setting a run changed, with what it was live. */
export function ChangeList({ changes, none }: { changes: readonly Change[]; none: string }) {
  if (changes.length === 0) return <p className="text-sm text-muted-foreground">{none}</p>
  return (
    <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[fit-content(60%)_1fr]">
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

export function Measure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right tabular-nums">{children}</dd>
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
