// A signal rule, made or changed: the script and the version it runs, the asset
// and its bars, the script's own inputs (read by PineTS in the page's worker),
// what each alert becomes, then what the save changes, reviewed before it is made.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { useToast } from '@/components/toast-context'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { DragToConfirm } from '@/components/ui/drag-to-confirm'
import { Input, Select } from '@/components/ui/form'
import { SettleModal } from '@/components/ui/settle-modal'
import { api, ApiError } from '@/lib/api'
import type { TaRule, TaRules, TaScriptSummary } from '@/lib/types'

import { BarsFields, ScriptChoice, ScriptSettings } from './RuleFields'
import { useScriptInputs } from './useScriptInputs'
import { draftOfRule, draftProblems, MAX_HISTORY_DAYS, newDraft, ruleChanges, ruleFields } from './ruleModel'
import type { RuleDraft } from './ruleModel'

/** How the dialog opens on a rule: as it is, or on the newer versions of its script or libraries. */
export type RuleDialogStart = 'edit' | 'update-script' | 'update-libraries'

interface RuleDialogProps {
  open: boolean
  /** null for a new rule. */
  rule: TaRule | null
  /** What a new rule starts from, when it is made from elsewhere (a script, a backtest). */
  draft?: RuleDraft
  start?: RuleDialogStart
  rules: TaRules
  scripts: TaScriptSummary[]
  onClose: () => void
}

export function RuleDialog({ open, rule, draft, start = 'edit', rules, scripts, onClose }: RuleDialogProps) {
  return (
    <SettleModal
      open={open}
      onClose={onClose}
      title={rule ? `Signal rule: ${rule.name}` : 'New signal rule'}
      description="A script run on each bar of an asset as the bar closes; its alerts become signals for the trader."
      size="xl"
      closeOnBackdrop={false}
    >
      {/* Another revision, another draft: what was typed against the old one is not kept. */}
      {open && (
        <RuleForm
          key={`${rule?.id ?? 'new'}|${rules.revision}|${start}`}
          rule={rule}
          draft={draft}
          start={start}
          rules={rules}
          scripts={scripts}
          onClose={onClose}
        />
      )}
    </SettleModal>
  )
}

function RuleForm({
  rule,
  draft: startedFrom,
  start,
  rules,
  scripts,
  onClose,
}: {
  rule: TaRule | null
  draft: RuleDraft | undefined
  start: RuleDialogStart
  rules: TaRules
  scripts: TaScriptSummary[]
  onClose: () => void
}) {
  const toast = useToast()
  const queryClient = useQueryClient()
  const runnable = useMemo(() => scripts.filter((script) => script.kind !== 'library'), [scripts])
  const [draft, setDraft] = useState<RuleDraft>(() => {
    if (!rule) return startedFrom ?? newDraft(runnable[0], rules.instruments[0], rules.indices)
    const opened = draftOfRule(rule)
    return start === 'update-script' && rule.latest.script !== null ? { ...opened, version: rule.latest.script } : opened
  })
  // Typed but not a value of its input's type: shown as typed, not saved.
  const [invalidInputs, setInvalidInputs] = useState<Record<string, string>>({})
  const [reviewing, setReviewing] = useState(false)
  const update = (changes: Partial<RuleDraft>) => setDraft((current) => ({ ...current, ...changes }))

  // The script's inputs as PineTS reads them: what the form offers, and the defaults a rule leaves alone.
  const inputs = useScriptInputs(draft.scriptId, draft.version)
  const meta = inputs.meta

  const fields = ruleFields(draft, meta)
  const otherNames = rules.rules.filter((entry) => entry.id !== rule?.id).map((entry) => entry.name)
  const problems = [
    ...draftProblems(draft, otherNames),
    ...(Object.keys(invalidInputs).length > 0 ? ['An input holds a value its type does not take.'] : []),
  ]
  const scriptName = (id: string) => scripts.find((entry) => entry.id === id)?.name ?? id
  const changes = ruleChanges(rule, fields, scriptName)
  const refreshLibraries = start === 'update-libraries'
  const live = rule?.mode === 'live'
  const signalsMade = fields.alerts.length > 0 || fields.alert_calls === 'message'

  const save = useMutation({
    mutationFn: () =>
      rule
        ? api.saveTaRule(rule.id, { ...fields, revision: rules.revision, refresh_libraries: refreshLibraries })
        : api.createTaRule({ ...fields, revision: rules.revision, mode: 'shadow' }),
    onSuccess: async (saved) => {
      toast({
        title: rule ? 'Signal rule saved' : 'Signal rule made, in shadow',
        description: rule
          ? `${saved.rule.name} runs ${scriptName(saved.rule.script.id)} v${saved.rule.script.version}.`
          : 'Its signals are journaled, never sent, until it is switched to live.',
        variant: 'success',
      })
      await queryClient.invalidateQueries({ queryKey: ['ta-rules'] })
      onClose()
    },
  })
  const saveError =
    save.error instanceof ApiError && save.error.status === 409 && save.error.message.includes('changed meanwhile')
      ? 'The rules changed since this dialog read them. Close it and open it again: the change will have to be made again.'
      : save.error instanceof ApiError && save.error.status === 403
        ? 'This rule is live, and operator actions are disabled in the configuration: it cannot be changed.'
        : save.error?.message

  if (reviewing) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {rule ? `${changes.length} change${changes.length === 1 ? '' : 's'} to the rule.` : 'The new rule, in shadow:'}
        </p>
        <dl className="divide-y divide-border rounded-lg border border-border text-sm">
          {changes.map((change) => (
            <div key={change.label} className="grid gap-1 px-3 py-2 sm:grid-cols-[8rem_1fr]">
              <dt className="text-text-secondary">{change.label}</dt>
              <dd className="min-w-0 break-words">
                {rule && <span className="text-muted-foreground line-through decoration-1">{change.before}</span>}
                {rule && ' → '}
                <span className="font-medium">{change.after}</span>
              </dd>
            </div>
          ))}
          {refreshLibraries && (
            <div className="px-3 py-2 text-sm">The libraries it imports run at their current versions.</div>
          )}
        </dl>
        {!signalsMade && (
          <Alert tone="neutral">
            <AlertDescription>No alert becomes a signal yet: the rule runs and journals nothing until one does.</AlertDescription>
          </Alert>
        )}
        {live && (
          <Alert tone="warning">
            <AlertTitle>This rule is live</AlertTitle>
            <AlertDescription>
              Its next signals follow the change at once, and go to the trader. The change is recorded in the audit trail.
            </AlertDescription>
          </Alert>
        )}
        {saveError && (
          <Alert tone="danger" role="alert">
            <AlertTitle>Not saved</AlertTitle>
            <AlertDescription>{saveError}</AlertDescription>
          </Alert>
        )}
        {live ? (
          <DragToConfirm
            key={save.error?.message ?? 'first'}
            label="Slide to change the live rule"
            confirmedLabel={save.isPending ? 'Saving…' : 'Saved'}
            variant="warning"
            autoResetDelay={0}
            haptic={false}
            disabled={save.isPending}
            onConfirm={() => save.mutate()}
          />
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => setReviewing(false)}>
            Back to the rule
          </Button>
          {!live && (
            <Button size="sm" isLoading={save.isPending} onClick={() => save.mutate()}>
              {rule ? 'Save' : 'Make the rule'}
            </Button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-5 text-sm">
      {runnable.length === 0 && (
        <Alert tone="neutral">
          <AlertDescription>No indicator or strategy to run yet: write one on the Indicators page.</AlertDescription>
        </Alert>
      )}
      {rules.instruments.length === 0 && (
        <Alert tone="warning">
          <AlertDescription>
            The Capital.com feed records no instrument: a rule runs on its bars (Data › Providers › Capital.com).
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 sm:col-span-2">
          <span className="text-xs text-text-secondary">Name</span>
          <Input value={draft.name} maxLength={80} onChange={(event) => update({ name: event.target.value })} />
        </label>
        <ScriptChoice draft={draft} update={update} scripts={scripts} onScriptChange={() => setInvalidInputs({})} />
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Asset (Capital.com)</span>
          <Select
            value={draft.epic}
            onChange={(event) => update({ epic: event.target.value })}
            options={rules.instruments.map((instrument) => ({ value: instrument.epic, label: `${instrument.epic} (${instrument.label})` }))}
          />
        </label>
        <BarsFields draft={draft} update={update} />
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Indice its signals are for</span>
          <Select
            value={draft.indice}
            onChange={(event) => update({ indice: event.target.value })}
            options={rules.indices.map((indice) => ({ value: indice, label: indice }))}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-text-secondary">Days of bars before each day</span>
          <Input
            type="number"
            min={0}
            max={MAX_HISTORY_DAYS}
            step={1}
            value={draft.historyDays}
            placeholder={`The engine’s: ${rules.engine_history_days}`}
            title="The script runs each day from the engine's restart on the bars of so many days before: fewer for a script that needs little warm-up (it runs faster at each bar), more for one that needs a long one. Empty: the engine's history_days."
            onChange={(event) => update({ historyDays: event.target.value })}
          />
        </label>
      </div>

      <ScriptSettings
        draft={draft}
        update={update}
        inputs={inputs}
        invalidInputs={invalidInputs}
        setInvalidInputs={setInvalidInputs}
      />

      {problems.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-negative">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          disabled={problems.length > 0 || (rule !== null && changes.length === 0 && !refreshLibraries)}
          onClick={() => setReviewing(true)}
        >
          Review
        </Button>
      </div>
    </div>
  )
}
