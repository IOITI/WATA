import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  ChangeList,
  DragToConfirm,
  FormDescription,
  FormItem,
  FormLabel,
  QueryState,
  SettleModal,
  useToast,
} from 'ioiti-ui'
import { useCallback, useMemo, useState } from 'react'
import { Link } from 'react-router'

import { keyChanges } from '@/features/config/keyChanges'
import { diffConfigs, getAt, setAt } from '@/features/config/configModel'
import { useDraft } from '@/features/config/draft-context'
import { DraftProvider, NumberField, SwitchField } from '@/features/config/fields'
import { RowsField } from '@/features/config/listFields'
import type { RowColumn } from '@/features/config/listFields'
import { api, ApiError } from '@/lib/api'
import type { CapitalConfig, JsonObject } from '@/lib/types'

import { capitalDraft, capitalInstrumentProblems, capitalSectionToSave, capitalWarnings } from './providers'

const SECTION = 'trade.config.capital_feed'

/** What the dialog opens on: the settings, or the feed switched on. */
export type CapitalDialogStart = 'edit' | 'enable'

const INSTRUMENT_COLUMNS: RowColumn[] = [
  { key: 'epic', label: 'Epic', kind: 'text', width: '1fr' },
  { key: 'label', label: 'Label', kind: 'text', width: '1fr' },
]

const ACCOUNTS: { id: string; label: string; hint: string }[] = [
  { id: 'demo', label: 'Demo', hint: 'The free demo account: its prices are Capital.com’s.' },
  { id: 'live', label: 'Live', hint: 'A real account: the key must be one of it.' },
]

/** Which of Capital.com's accounts the key logs into. */
function AccountField({ environments }: { environments: string[] }) {
  const { draft, update, disabled } = useDraft()
  const current = getAt(draft, ['environment'])
  const choices = ACCOUNTS.filter((account) => environments.includes(account.id))
  const chosen = choices.find((account) => account.id === current)
  return (
    <FormItem>
      <FormLabel>Account</FormLabel>
      <div role="group" aria-label="Account" className="flex gap-1.5">
        {choices.map((account) => (
          <Button
            key={account.id}
            type="button"
            size="sm"
            variant={account.id === current ? 'primary' : 'outline'}
            aria-pressed={account.id === current}
            disabled={disabled}
            onClick={() => update(['environment'], account.id)}
          >
            {account.label}
          </Button>
        ))}
      </div>
      {chosen && <FormDescription>{chosen.hint}</FormDescription>}
    </FormItem>
  )
}

interface CapitalSettingsDialogProps {
  open: boolean
  start?: CapitalDialogStart
  onClose: () => void
}

/**
 * The Capital.com feed's settings (trade.config.capital_feed), edited on its
 * provider. Reviewed, then saved by a slide through the configuration editor's
 * own save, as the price recorder's are. Its login is not here: it is a
 * credential sealed for the feed alone (Configuration, Credentials).
 */
export function CapitalSettingsDialog({ open, start = 'edit', onClose }: CapitalSettingsDialogProps) {
  const configQuery = useQuery({
    queryKey: ['data-capital-config'],
    queryFn: api.capitalConfig,
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
      title="Capital.com feed settings"
      description="Capital.com's stream, recorded every ten seconds and sent live to the charts. The feed reads these settings when it starts."
      size="xl"
      closeOnBackdrop={false}
    >
      <QueryState isPending={configQuery.isPending} error={configQuery.error} hasData={!!config}>
        {config && open && <CapitalForm key={`${config.revision}|${start}`} config={config} start={start} onClose={onClose} />}
      </QueryState>
    </SettleModal>
  )
}

function CapitalForm({ config, start, onClose }: { config: CapitalConfig; start: CapitalDialogStart; onClose: () => void }) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const section = useMemo(() => config.section ?? {}, [config.section])
  const opened = useMemo(() => capitalDraft(section, config.defaults), [section, config.defaults])
  const [draft, setDraft] = useState<JsonObject>(() => (start === 'enable' ? { ...opened, enabled: true } : opened))
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
  const problems = capitalInstrumentProblems(draft.instruments)
  const warnings = capitalWarnings(opened, draft)
  const disabled = !config.editable

  const save = useMutation({
    mutationFn: () => api.saveCapitalConfig(capitalSectionToSave(section, draft, config.defaults), config.revision),
    onSuccess: async (saved) => {
      toast({
        title: 'Capital.com feed settings saved',
        description: `Previous version kept as ${saved.backup}. The feed applies them at its next start.`,
        variant: 'success',
      })
      await Promise.all(
        ['config', 'config-backups', 'session', 'data-capital-config', 'data-providers', 'data-provider'].map((key) =>
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
          {changes.length} change{changes.length === 1 ? '' : 's'} to <code>{SECTION}</code> in <code>config.json</code>.
          The current file is kept as a backup beside it, and the new one is checked with the feed&rsquo;s own rules
          before it is written.
        </p>
        <ChangeList label="What saving changes" className="max-h-64 overflow-y-auto" changes={keyChanges(changes, SECTION)} />
        {warnings.map((warning) => (
          <Alert key={warning} tone="warning">
            <AlertDescription>{warning}</AlertDescription>
          </Alert>
        ))}
        <p className="text-xs text-muted-foreground">
          The feed reads its settings when it starts: once saved, its page offers to restart it, which leaves the
          trading services as they are. Switching it on for the first time also needs its login (Configuration,
          Credentials) and <code>capital</code> in <code>COMPOSE_PROFILES</code>, then <code>watastop</code> and{' '}
          <code>watastart</code> on the server: a container not created cannot be restarted.
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
            label="Record Capital.com's prices"
            path={['enabled']}
            hint="Streamed live to the charts, and every ten seconds into the database."
          />
          <SwitchField
            label="Capital.com's volume of each minute"
            path={['minute_volume']}
            hint="Asked for each minute and kept beside the bars: what Capital.com counts, beside the quote changes the feed counts itself."
          />
          <SwitchField
            label="Day files for backtests"
            path={['day_files']}
            hint="Each day of UTC, once over, written as a file of ten-second bars that backtests can run on."
          />
        </div>
        <AccountField environments={config.environments} />
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField label="Keep in the database" unit="months" path={['retention_months']} hint="0 keeps everything; the day files stay" />
          <NumberField
            label="Say it stopped after"
            unit="s"
            path={['alert_after_seconds']}
            hint="Without a bar while the market is open: a Telegram message"
          />
        </div>
        <RowsField
          label="Instruments"
          path={['instruments']}
          columns={INSTRUMENT_COLUMNS}
          newRow={() => ({ epic: '', label: '' })}
          hint={`The epic is Capital.com's name of the instrument (US100 for the Nasdaq 100; python -m src.capital_feed.probe --search finds others), the label WATA's${config.indices.length > 0 ? ` (${config.indices.join(', ')} for an indice of the rules)` : ''}. Day files go under the label.`}
        />
      </DraftProvider>

      <Alert tone="neutral">
        <AlertDescription>
          The login is not set here: it is in{' '}
          <Link className="underline underline-offset-2" to="/config/credentials">
            Configuration, Credentials
          </Link>
          , sealed for the feed alone. Capital.com has no key that can only read prices.
        </AlertDescription>
      </Alert>

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
