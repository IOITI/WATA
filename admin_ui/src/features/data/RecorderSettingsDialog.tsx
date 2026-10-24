import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo, useState } from 'react'

import { QueryState } from '@/components/status'
import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { DragToConfirm } from '@/components/ui/drag-to-confirm'
import { SettleModal } from '@/components/ui/settle-modal'
import { ChangeList } from '@/features/config/ChangeList'
import { diffConfigs, setAt } from '@/features/config/configModel'
import { DraftProvider, NumberField, SwitchField } from '@/features/config/fields'
import { RowsField } from '@/features/config/listFields'
import type { RowColumn } from '@/features/config/listFields'
import { api, ApiError } from '@/lib/api'
import type { JsonObject, RecorderConfig } from '@/lib/types'

import { instrumentProblems, recorderDraft, recorderWarnings, sectionToSave } from './providers'

/** What the dialog opens on: the settings, the recorder switched on, or a new instrument to fill in. */
export type RecorderDialogStart = 'edit' | 'enable' | 'add-instrument'

const INSTRUMENT_COLUMNS: RowColumn[] = [
  { key: 'label', label: 'Label', kind: 'text', width: '1fr' },
  { key: 'uic', label: 'UIC', kind: 'number', width: '1fr' },
  { key: 'asset_type', label: 'Asset type', kind: 'text', width: '1.2fr' },
]

interface RecorderSettingsDialogProps {
  open: boolean
  start?: RecorderDialogStart
  onClose: () => void
}

/**
 * The price recorder's settings (trade.config.price_recorder), edited on its
 * provider. What is changed is reviewed, then saved by a slide through the
 * configuration editor's own save: checked by the services' validator, the
 * file it replaces kept, the save audited. The recorder applies it at its next
 * start, which is done on the server.
 */
export function RecorderSettingsDialog({ open, start = 'edit', onClose }: RecorderSettingsDialogProps) {
  const configQuery = useQuery({
    queryKey: ['data-recorder-config'],
    queryFn: api.recorderConfig,
    enabled: open,
    // The dialog holds a draft of this section: it must not change under it.
    refetchOnWindowFocus: false,
    staleTime: 0,
  })
  const config = configQuery.data

  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title={start === 'add-instrument' ? 'Record another instrument' : 'Price recorder settings'}
      description="Saxo's stream, recorded every ten seconds. The recorder reads these settings when it starts."
      size="xl"
      closeOnBackdrop={false}
    >
      <QueryState isPending={configQuery.isPending} error={configQuery.error} hasData={!!config}>
        {/* Another revision read, another draft: what was typed against the old one is not kept. */}
        {config && open && <RecorderForm key={`${config.revision}|${start}`} config={config} start={start} onClose={onClose} />}
      </QueryState>
    </SettleModal>
  )
}

function RecorderForm({ config, start, onClose }: { config: RecorderConfig; start: RecorderDialogStart; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const opened = useMemo(() => recorderDraft(config.section, config.defaults), [config])
  const [draft, setDraft] = useState<JsonObject>(() => {
    if (start === 'enable') return { ...opened, enabled: true }
    if (start === 'add-instrument') {
      const instruments = Array.isArray(opened.instruments) ? opened.instruments : []
      return { ...opened, instruments: [...instruments, { label: '', uic: null, asset_type: config.asset_types[0] ?? '' }] }
    }
    return opened
  })
  const [invalidFields, setInvalidFields] = useState<ReadonlySet<string>>(() => new Set())
  const reportInvalid = useCallback((fieldId: string, invalid: boolean) => {
    setInvalidFields((current) => {
      if (current.has(fieldId) === invalid) return current
      const next = new Set(current)
      if (invalid) next.add(fieldId)
      else next.delete(fieldId)
      return next
    })
  }, [])
  const [reviewing, setReviewing] = useState(false)

  const changes = useMemo(() => diffConfigs(opened, draft), [opened, draft])
  const problems = instrumentProblems(draft.instruments)
  const warnings = recorderWarnings(opened, draft, config.indices)
  const disabled = !config.editable

  const save = useMutation({
    mutationFn: () => api.saveRecorderConfig(sectionToSave(config.section, draft, config.defaults), config.revision),
    onSuccess: async (saved) => {
      toast({
        title: 'Price recorder settings saved',
        description: `Previous version kept as ${saved.backup}. The recorder applies them at its next start.`,
        variant: 'success',
      })
      await Promise.all(
        // The configuration page holds the file's revision: it must read the new one, or its next save is refused.
        ['config', 'config-backups', 'session', 'data-recorder-config', 'data-providers', 'data-provider'].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      )
      onClose()
    },
  })
  const conflict = save.error instanceof ApiError && save.error.status === 409

  if (reviewing) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {changes.length} change{changes.length === 1 ? '' : 's'} to <code>trade.config.price_recorder</code> in{' '}
          <code>config.json</code>. The current file is kept as a backup beside it, and the new one is checked with the
          services&rsquo; own validator before it is written.
        </p>
        <ChangeList changes={changes.map((change) => ({ ...change, path: `trade.config.price_recorder.${change.path}` }))} />
        {warnings.map((warning) => (
          <Alert key={warning} tone="warning">
            <AlertDescription>{warning}</AlertDescription>
          </Alert>
        ))}
        <p className="text-xs text-muted-foreground">
          The recorder reads its settings when it starts: once saved, its page offers to restart it, which leaves the
          trading services as they are. Switching it on for the first time also needs <code>recorder</code> in{' '}
          <code>COMPOSE_PROFILES</code>, then <code>watastop</code> and <code>watastart</code> on the server: a container
          not created cannot be restarted.
        </p>
        {save.error && (
          <Alert tone="danger" role="alert">
            <AlertTitle>Not saved</AlertTitle>
            <AlertDescription>
              {conflict
                ? 'The configuration file changed on the server since this dialog read it. Close it and open it again: the changes will have to be made again.'
                : save.error.message}
            </AlertDescription>
          </Alert>
        )}
        <DragToConfirm
          // A fresh slider for each attempt.
          key={save.error?.message ?? 'first'}
          label="Slide to save"
          confirmedLabel={save.isPending ? 'Saving…' : 'Saved'}
          variant="warning"
          autoResetDelay={0}
          haptic={false}
          disabled={save.isPending}
          onConfirm={() => save.mutate()}
        />
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => setReviewing(false)}>
            Back to the settings
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {disabled && (
        <Alert tone="neutral">
          <AlertDescription>
            Editing is switched off in the configuration (<code>admin.config_edit_enabled</code>): the settings are shown
            read-only.
          </AlertDescription>
        </Alert>
      )}
      <DraftProvider
        value={{
          draft,
          disabled,
          reportInvalid,
          update: (path, value) => setDraft((current) => setAt(current, path, value) as JsonObject),
        }}
      >
        <div className="space-y-4">
          <SwitchField
            label="Record Saxo's prices"
            path={['enabled']}
            hint="Every ten seconds, the turbos the trader could buy and the index of each indice."
          />
          <SwitchField
            label="Record Saxo's volume"
            path={['record_volume']}
            hint="Also asks Saxo for what it counts as traded. wataprices probe --uic <a turbo> says whether it has one."
          />
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField label="Instruments at most" path={['max_instruments']} hint="Beyond it, the turbos seen longest ago are dropped" />
          <NumberField label="Keep" unit="months" path={['retention_months']} hint="0 keeps everything" />
          <NumberField label="Look for candidates every" unit="s" path={['refresh_interval_seconds']} hint="30 at least" />
          <NumberField label="Quotes at most every" unit="ms" path={['refresh_rate_ms']} hint="100 at least" />
          <NumberField
            label="Say it stopped after"
            unit="s"
            path={['alert_after_seconds']}
            hint="Without a bar in trading hours: a Telegram message"
          />
        </div>
        <RowsField
          label="Instruments recorded besides the turbos"
          path={['instruments']}
          columns={INSTRUMENT_COLUMNS}
          newRow={() => ({ label: '', uic: null, asset_type: config.asset_types[0] ?? '' })}
          hint={`An index Saxo quotes for your account, for example; asset types: ${config.asset_types.join(', ')}. On the server, wataprices probe --uic <n> prints the entry to copy here. One labelled like an indice is recorded as its index.`}
        />
      </DraftProvider>

      {problems.length > 0 && (
        <Alert tone="danger" role="alert">
          <AlertDescription>
            <ul className="list-inside list-disc">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-4">
        <p className="text-xs text-muted-foreground">
          {invalidFields.size > 0
            ? `${invalidFields.size} field${invalidFields.size === 1 ? ' is' : 's are'} not a number`
            : changes.length === 0
              ? 'No change'
              : `${changes.length} change${changes.length === 1 ? '' : 's'}`}
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            disabled={disabled || changes.length === 0 || invalidFields.size > 0 || problems.length > 0}
            onClick={() => setReviewing(true)}
          >
            Review the changes
          </Button>
        </div>
      </div>
    </div>
  )
}
