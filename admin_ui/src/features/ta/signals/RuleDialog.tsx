// A signal rule, made or changed, step by step: the script and the version it
// runs, the asset and its bars, the script's own inputs (read by PineTS in the
// page's worker) and what each alert becomes, then what the save changes,
// reviewed before it is made.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, AlertTitle, Button, DragToConfirm, Input, Select, SettleModal, StepPanels, Stepper, useToast } from 'ioiti-ui'
import { useMemo, useState } from 'react'

import { api, ApiError } from '@/lib/api'
import type { TaRule, TaRules, TaScriptSummary } from '@/lib/types'

import { HistoryDaysField } from './HistoryDaysField'
import { BarsFields, ScriptChoice, ScriptSettings } from './RuleFields'
import { useScriptInputs } from './useScriptInputs'
import { draftOfRule, draftProblems, newDraft, ruleChanges, ruleFields } from './ruleModel'
import type { RuleDraft, RuleStep } from './ruleModel'

/** How the dialog opens on a rule: as it is, or on the newer versions of its script or libraries. */
export type RuleDialogStart = 'edit' | 'update-script' | 'update-libraries'

const STEPS = [
  { title: 'Script', description: 'what it runs' },
  { title: 'Market', description: 'its bars, its indice' },
  { title: 'Inputs and alerts', description: 'what makes a signal' },
  { title: 'Review' },
]
const SETTINGS = 2
const REVIEW = 3
/** The step each check of a draft is on. */
const STEP_OF: Record<RuleStep, number> = { script: 0, market: 1 }

/**
 * Where the dialog opens: at the start, but on the inputs and alerts for a newer
 * version of the script (what a new version may change), and on the review for
 * newer libraries (nothing to choose).
 */
function firstStep(start: RuleDialogStart): number {
  if (start === 'update-libraries') return REVIEW
  if (start === 'update-script') return SETTINGS
  return 0
}

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

/** What keeps a step's fields from being saved, under them. */
function StepProblems({ problems }: { problems: string[] }) {
  if (problems.length === 0) return null
  return (
    <ul className="list-disc space-y-1 pl-5 text-xs text-negative">
      {problems.map((problem) => (
        <li key={problem}>{problem}</li>
      ))}
    </ul>
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
  const [step, setStep] = useState(() => firstStep(start))
  const update = (changes: Partial<RuleDraft>) => setDraft((current) => ({ ...current, ...changes }))

  // The script's inputs as PineTS reads them: what the form offers, and the defaults a rule leaves alone.
  const inputs = useScriptInputs(draft.scriptId, draft.version)
  const meta = inputs.meta

  const fields = ruleFields(draft, meta)
  const otherNames = rules.rules.filter((entry) => entry.id !== rule?.id).map((entry) => entry.name)
  const problems = [
    ...draftProblems(draft, otherNames).map((problem) => ({ step: STEP_OF[problem.step], text: problem.text })),
    ...(Object.keys(invalidInputs).length > 0 ? [{ step: SETTINGS, text: 'An input holds a value its type does not take.' }] : []),
  ]
  const problemsOf = (index: number) => problems.filter((problem) => problem.step === index).map((problem) => problem.text)
  const scriptName = (id: string) => scripts.find((entry) => entry.id === id)?.name ?? id
  const changes = ruleChanges(rule, fields, scriptName)
  const refreshLibraries = start === 'update-libraries'
  const live = rule?.mode === 'live'
  const signalsMade = fields.alerts.length > 0 || fields.alert_calls === 'message'
  // Nothing wrong left, and for a rule that exists, something to save.
  const ready = problems.length === 0 && (rule === null || changes.length > 0 || refreshLibraries)

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

  return (
    <div className="space-y-5 text-sm">
      <Stepper steps={STEPS} current={step} onStepChange={setStep} />
      <StepPanels current={step}>
        <div className="space-y-4">
          {runnable.length === 0 && (
            <Alert tone="neutral">
              <AlertDescription>No indicator or strategy to run yet: write one on the Indicators page.</AlertDescription>
            </Alert>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1 sm:col-span-2">
              <span className="text-xs text-text-secondary">Name</span>
              <Input value={draft.name} maxLength={80} onChange={(event) => update({ name: event.target.value })} />
            </label>
            <ScriptChoice draft={draft} update={update} scripts={scripts} onScriptChange={() => setInvalidInputs({})} />
          </div>
          <StepProblems problems={problemsOf(0)} />
        </div>

        <div className="space-y-4">
          {rules.instruments.length === 0 && (
            <Alert tone="warning">
              <AlertDescription>
                The Capital.com feed records no instrument: a rule runs on its bars (Data › Providers › Capital.com).
              </AlertDescription>
            </Alert>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
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
            <HistoryDaysField
              draft={draft}
              update={update}
              engineHistoryDays={rules.engine_history_days}
              restartTime={rules.engine_restart_time}
            />
          </div>
          <StepProblems problems={problemsOf(1)} />
        </div>

        <div className="space-y-4">
          <ScriptSettings
            draft={draft}
            update={update}
            inputs={inputs}
            invalidInputs={invalidInputs}
            setInvalidInputs={setInvalidInputs}
          />
          <StepProblems problems={problemsOf(SETTINGS)} />
        </div>

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
          {problems.length > 0 && (
            // What is left to put right, each leading back to its step.
            <div className="space-y-1">
              <p className="text-xs font-medium text-negative">Before it can be {rule ? 'saved' : 'made'}:</p>
              <ul className="list-disc space-y-1 pl-5 text-xs text-negative">
                {problems.map((problem) => (
                  <li key={problem.text}>
                    {problem.text}{' '}
                    <button type="button" className="underline underline-offset-2" onClick={() => setStep(problem.step)}>
                      {STEPS[problem.step].title}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
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
          {live && ready && (
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
          )}
        </div>
      </StepPanels>

      <div className="flex justify-end gap-2">
        {step === 0 ? (
          <Button variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
        ) : (
          <Button variant="ghost" size="sm" disabled={save.isPending} onClick={() => setStep(step - 1)}>
            Back
          </Button>
        )}
        {step < REVIEW ? (
          <Button size="sm" onClick={() => setStep(step + 1)}>
            Next
          </Button>
        ) : (
          !live && (
            <Button size="sm" disabled={!ready} isLoading={save.isPending} onClick={() => save.mutate()}>
              {rule ? 'Save' : 'Make the rule'}
            </Button>
          )
        )}
      </div>
    </div>
  )
}
