import { Alert, AlertDescription, AlertTitle, Badge, ChangeList } from 'ioiti-ui'
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

/** Each setting a run changed, from what it was live (ioiti-ui's ChangeList), or what it means when it changed none. */
export function SettingChanges({ changes, none, label }: { changes: readonly Change[]; none: string; label: string }) {
  if (changes.length === 0) return <p className="text-sm text-muted-foreground">{none}</p>
  return <ChangeList label={label} numeric changes={changes.map(({ label, before, after }) => ({ label, before, after }))} />
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
