import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Badge, Button, Checkbox, DragToConfirm, Hint, QueryState, SettleModal } from 'ioiti-ui'
import { ArrowRight } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { ReviewChanges } from '@/features/config/ReviewChanges'
import { api, ApiError } from '@/lib/api'
import type { BacktestField, ConfigSaved, RunSettingDifference, RunToLive } from '@/lib/types'

import { differencesSaid, differenceTag, differenceValue, initiallyTicked, reviewBlocked } from './applyToLive'

/**
 * A run's settings applied to WATA: the settings that differ from config.json's now, each ticked at first (WATA then
 * trades as the run did), then the change of config.json reviewed as the Configuration page reviews a save, and saved
 * by a slide. One dialog, its review a step inside it. Nothing is saved before the slide; once saved, each service
 * named applies it at its next start.
 */
export function ApplyToLiveDialog({ runId, open, onClose, onSaved }: {
  runId: string
  open: boolean
  onClose: () => void
  onSaved: (saved: ConfigSaved) => void
}) {
  const toLive = useQuery({ queryKey: ['backtest-to-live', runId], queryFn: () => api.backtestToLive(runId), enabled: open })
  const options = useQuery({ queryKey: ['backtest-options'], queryFn: api.backtestOptions, enabled: open, staleTime: 60_000 })

  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title="Apply this run’s settings to WATA"
      description="config.json is changed as the Configuration page changes it: checked by the services’ own validator, the file of before kept."
      size="xl"
      closeOnBackdrop={false}
    >
      <QueryState isPending={toLive.isPending} error={toLive.error} hasData={!!toLive.data}>
        {/* Another read of config.json, another choice: what was ticked against the old one is not kept. */}
        {toLive.data && open && (
          <ApplyForm key={toLive.data.revision} runId={runId} found={toLive.data} fields={options.data?.fields ?? []}
            onClose={onClose} onSaved={onSaved} onReread={() => void toLive.refetch()} />
        )}
      </QueryState>
    </SettleModal>
  )
}

function ApplyForm({ runId, found, fields, onClose, onSaved, onReread }: {
  runId: string
  found: RunToLive
  fields: readonly BacktestField[]
  onClose: () => void
  onSaved: (saved: ConfigSaved) => void
  onReread: () => void
}) {
  const { config_edit_enabled: editable } = useSession()
  const [ticked, setTicked] = useState<string[]>(() => initiallyTicked(found.differences))
  const [reviewing, setReviewing] = useState(false)
  const catalog = useQuery({ queryKey: ['config-catalog'], queryFn: api.configCatalog, enabled: reviewing, staleTime: Infinity })
  const preview = useMutation({ mutationFn: () => api.previewBacktestToLive(runId, ticked) })
  const save = useMutation({
    mutationFn: (revision: string) => api.applyBacktestToLive(runId, ticked, revision),
    onSuccess: (saved) => onSaved(saved),
  })
  const conflict = save.error instanceof ApiError && save.error.status === 409
  const applicable = found.differences.filter((row) => row.apply)
  const refused = found.differences.filter((row) => !row.apply)
  const blocked = reviewBlocked(ticked, editable)

  if (reviewing) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          What saving changes in <code>config.json</code>, key by key, as the server checked it. The current file is kept
          as a backup beside it.
        </p>
        <QueryState isPending={preview.isPending || catalog.isPending} error={preview.error ?? catalog.error}
          hasData={!!preview.data && !!catalog.data}>
          {preview.data && catalog.data && <ReviewChanges catalog={catalog.data} preview={preview.data} />}
        </QueryState>
        <Alert tone="warning">
          <AlertDescription>
            This acts on the live trading account: each service named applies it at its next start, a restart after a
            crash included.
          </AlertDescription>
        </Alert>
        {save.error && (
          <Alert tone="danger" role="alert">
            <AlertTitle>Not saved</AlertTitle>
            <AlertDescription>
              {conflict
                ? 'config.json was saved by someone else since this review: review it again before saving.'
                : save.error.message}
            </AlertDescription>
          </Alert>
        )}
        {conflict ? (
          <Button size="sm" onClick={() => {
            save.reset()
            setReviewing(false)
            onReread()
          }}>
            Review again
          </Button>
        ) : (
          <DragToConfirm
            // A fresh slider for each attempt.
            key={save.error?.message ?? 'first'}
            label="Slide to save"
            confirmedLabel={save.isPending ? 'Saving…' : 'Saved'}
            variant="warning"
            autoResetDelay={0}
            haptic={false}
            disabled={save.isPending || !preview.isSuccess || !catalog.data}
            onConfirm={() => preview.data && save.mutate(preview.data.revision)}
          />
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => {
            save.reset()
            setReviewing(false)
          }}>
            Back to the settings
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">{differencesSaid(found.differences)}</p>
      {applicable.length > 0 && (
        <ul aria-label="Settings to apply" className="divide-y divide-border/60 rounded-lg border border-border/60">
          {applicable.map((row) => (
            <DifferenceRow key={row.setting} row={row} field={fields.find((field) => field.name === row.setting)}
              checked={ticked.includes(row.setting)}
              onChange={(on) => setTicked((current) => (on ? [...current, row.setting] : current.filter((name) => name !== row.setting)))} />
          ))}
        </ul>
      )}
      {refused.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">Different, but not applied from here</h3>
          <ul aria-label="Settings not applied" className="divide-y divide-border/60 rounded-lg border border-border/60">
            {refused.map((row) => (
              <DifferenceRow key={row.setting} row={row} field={fields.find((field) => field.name === row.setting)} />
            ))}
          </ul>
        </div>
      )}
      {found.backtest_only.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Only a backtest has these, nothing to apply:{' '}
          {found.backtest_only.map((entry) => `${entry.label} ${differenceValue(entry.value, entry)}`).join(' · ')}.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
        <p className="text-xs text-muted-foreground">
          {ticked.length} of {applicable.length} ticked
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            {found.differences.length === 0 ? 'Close' : 'Cancel'}
          </Button>
          {applicable.length > 0 && (
            <Hint content={blocked ?? undefined} wrap={!!blocked}>
              <Button size="sm" disabled={!!blocked} onClick={() => {
                setReviewing(true)
                preview.mutate()
              }}>
                Review the changes
              </Button>
            </Hint>
          )}
        </div>
      </div>
    </div>
  )
}

function DifferenceRow({ row, field, checked, onChange }: {
  row: RunSettingDifference
  field?: BacktestField
  checked?: boolean
  onChange?: (on: boolean) => void
}) {
  const values = (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-sm tabular-nums">
      <span className="text-muted-foreground">{differenceValue(row.before, row, field)}</span>
      <ArrowRight className="size-3.5 text-muted-foreground" aria-label="to" />
      <span className="font-medium">{differenceValue(row.after, row, field)}</span>
    </span>
  )
  return (
    <li className="flex flex-col gap-1.5 px-3 py-2.5 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
      <div className="min-w-0 space-y-1">
        {onChange ? (
          <Checkbox label={row.label} checked={!!checked} onChange={(event) => onChange(event.target.checked)} />
        ) : (
          <p className="text-sm">{row.label}</p>
        )}
        {(row.why_not ?? row.note) && (
          <p className={row.why_not ? 'text-xs text-warning' : 'text-xs text-muted-foreground'}>{row.why_not ?? row.note}</p>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
        {values}
        <Badge variant="outline" tone={row.own ? 'neutral' : 'warning'}>{differenceTag(row)}</Badge>
      </div>
    </li>
  )
}
