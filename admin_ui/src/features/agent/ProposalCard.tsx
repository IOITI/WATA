// What the agent proposed, under its answer: a new version of one of the operator's scripts (shown beside
// the version it was made on), a new script of theirs, a change to a signal rule that is not live, or new
// values of trading settings in config.json. Applying it is the operator's click, through the stores' own
// checks (src/admin/agent/proposals.py); settings are previewed and saved as the Configuration page saves
// them, slid to confirm, and apply at the services' next start. Nothing live moves otherwise.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Change } from 'ioiti-ui'
import {
  Alert,
  AlertDescription,
  Badge,
  Button,
  ChangeList,
  CodeBlock,
  formatDateTime,
  Hint,
  Loader,
  QueryState,
  useToast,
} from 'ioiti-ui'
import { Check, Eye, FileCode2, FilePlus2, ListChecks, SlidersHorizontal, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { ConfirmLiveAction } from '@/components/ConfirmLiveAction'
import { useSession } from '@/features/auth/auth-context'
import { RunPeekLink } from '@/features/backtests/RunPeek'
import { serviceNames } from '@/features/config/editor-context'
import { ReviewChanges } from '@/features/config/ReviewChanges'
import { ScriptDiff } from '@/features/ta/editor/ScriptDiff'
import { api, ApiError } from '@/lib/api'
import type { AgentInputChange, AgentProposal } from '@/lib/types'

import { headlineSaid, settingSaid } from './model'
import { conversationKey } from './useConversation'

const KIND_ICON = { script: FileCode2, rule: ListChecks, settings: SlidersHorizontal, new_script: FilePlus2 }

function value(input: AgentInputChange['before']): string {
  return input === null || input === undefined ? 'its default' : String(input)
}

function ScriptChange({ proposal }: { proposal: AgentProposal }) {
  const script = proposal.script!
  const draft = proposal.draft!
  const before = useQuery({
    queryKey: ['ta-script-version', script.id, script.base_version],
    queryFn: () => api.taScriptVersion(script.id, script.base_version),
    staleTime: Infinity,
  })
  const after = useQuery({
    queryKey: ['ta-script-version', draft.id, draft.version],
    queryFn: () => api.taScriptVersion(draft.id, draft.version),
    staleTime: Infinity,
  })
  return (
    <QueryState isPending={before.isPending || after.isPending} error={before.error ?? after.error} hasData={!!before.data && !!after.data}>
      {before.data && after.data && (
        <div className="space-y-1.5">
          <div className="grid grid-cols-2 gap-3 text-xs text-muted-foreground">
            <p>{script.name} v{script.base_version}, as it is</p>
            <p>As proposed</p>
          </div>
          <ScriptDiff before={before.data.source} after={after.data.source} className="max-h-[28rem] min-h-40" />
        </div>
      )}
    </QueryState>
  )
}

function NewScript({ proposal }: { proposal: AgentProposal }) {
  const draft = proposal.draft!
  const source = useQuery({
    queryKey: ['ta-script-version', draft.id, draft.version],
    queryFn: () => api.taScriptVersion(draft.id, draft.version),
    staleTime: Infinity,
  })
  return (
    <QueryState isPending={source.isPending} error={source.error} hasData={!!source.data}>
      {source.data && (
        <CodeBlock code={source.data.source} language="text" lineNumbers maxHeight="28rem" copyable
          label={`${proposal.new_script?.name ?? draft.name}, from ${draft.name} v${draft.version}`} />
      )}
    </QueryState>
  )
}

/** The settings, from their live value to the one proposed, and the finished runs the change rests on. */
function SettingsChange({ proposal }: { proposal: AgentProposal }) {
  const changes: Change[] = (proposal.settings ?? []).map((setting) => ({
    label: setting.label,
    before: settingSaid(setting.before, setting.unit),
    after: settingSaid(setting.after, setting.unit),
  }))
  const runs = proposal.runs ?? []
  return (
    <div className="space-y-2.5 text-sm">
      <ChangeList label="In config.json" changes={changes} />
      {runs.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">It rests on {runs.length === 1 ? 'this run' : 'these runs'}:</p>
          <ul className="space-y-1">
            {runs.map((run) => (
              <li key={run.id} className="flex flex-wrap items-baseline gap-x-2">
                <RunPeekLink id={run.id} className="underline">{run.name ?? run.id}</RunPeekLink>
                {run.headline && <span className="text-xs tabular-nums text-text-secondary">{headlineSaid(run.headline)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(proposal.same ?? []).length > 0 && (
        <p className="text-xs text-muted-foreground">Left out, already at the value proposed: {proposal.same!.join(', ')}.</p>
      )}
    </div>
  )
}

/** A change of settings saved: where it applies, and the way to the restarts. */
function SettingsSaved({ proposal }: { proposal: AgentProposal }) {
  const catalog = useQuery({ queryKey: ['config-catalog'], queryFn: api.configCatalog, staleTime: Infinity })
  const result = proposal.result ?? {}
  const names = catalog.data ? serviceNames(catalog.data) : new Map<string, string>()
  const services = (result.services ?? []).map((service) => names.get(service) ?? service)
  return (
    <>
      Saved to config.json{result.backup && <>, the version before kept as {result.backup}</>}.{' '}
      {services.length > 0
        ? <>Applies at the next start of {services.join(', ')}: <Link className="underline" to="/system">restart them from System</Link>.</>
        : 'No service reads what changed.'}
    </>
  )
}

function RuleChange({ proposal, proposals }: { proposal: AgentProposal; proposals: AgentProposal[] }) {
  const changes = proposal.changes ?? {}
  const first = changes.after_proposal ? proposals.find((entry) => entry.id === changes.after_proposal) : undefined
  const libraries = proposal.result?.libraries ?? changes.libraries ?? []
  return (
    <ul className="space-y-1 text-sm">
      {changes.script_version !== undefined && (
        <li>Runs version {changes.script_version} of its script (v{proposal.rule?.script_version} now).</li>
      )}
      {first && <li>Runs the new version of its script proposed as “{first.title}”, once applied.</li>}
      {(changes.inputs ?? []).map((input) => (
        <li key={input.id}>
          {input.title ?? input.id} <span className="text-muted-foreground">({input.id})</span>: {value(input.before)} → <strong>{String(input.after)}</strong>
        </li>
      ))}
      {changes.history_days && (
        <li>
          Days of history before each engine day: {changes.history_days.before ?? "the engine's"} →{' '}
          <strong>{changes.history_days.after ?? "the engine's"}</strong>
        </li>
      )}
      {libraries.length > 0 && (
        <li className="text-muted-foreground">
          Its libraries move to their current versions: {libraries.map((library) => `${library.name} v${library.before ?? '?'} → v${library.after}`).join(', ')}.
        </li>
      )}
      <li className="text-muted-foreground">Applied, its week in shadow starts again: readiness counts from the change.</li>
    </ul>
  )
}

export function ProposalCard({ conversationId, proposal, proposals, blockedReason, onApplied }: {
  conversationId: string
  proposal: AgentProposal
  proposals: AgentProposal[]
  /** Why it cannot be applied here now (the script's unsaved changes in the editor). */
  blockedReason?: string | null
  /** Once applied: the page that shows what it changed reads it again (the editor its script). */
  onApplied?: (proposal: AgentProposal) => void
}) {
  const { timezone, config_edit_enabled: configEditable } = useSession()
  const queryClient = useQueryClient()
  const toast = useToast()
  const [shown, setShown] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const settled = (proposal: AgentProposal) => {
    if (proposal.status === 'applied') onApplied?.(proposal)
    void queryClient.invalidateQueries({ queryKey: conversationKey(conversationId) })
    void queryClient.invalidateQueries({ queryKey: ['agent-artefacts'] })
    void queryClient.invalidateQueries({ queryKey: ['ta-script'] })
    void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
    void queryClient.invalidateQueries({ queryKey: ['ta-rules'] })
  }
  const settle = useMutation({
    mutationFn: (apply: boolean) => api.settleAgentProposal(conversationId, proposal.id, apply),
    onSuccess: settled,
  })
  // A change of settings: checked as a save would be, then saved at the revision it was checked at.
  const preview = useMutation({ mutationFn: () => api.previewAgentSettings(conversationId, proposal.id) })
  const catalog = useQuery({
    queryKey: ['config-catalog'], queryFn: api.configCatalog, staleTime: Infinity, enabled: reviewing,
  })
  const save = useMutation({
    mutationFn: (revision: string) => api.saveAgentSettings(conversationId, proposal.id, revision),
    onSuccess: async (saved) => {
      setReviewing(false)
      settled(saved)
      toast({ title: 'Configuration saved', description: `From the agent's proposal: ${proposal.title}.`, variant: 'success' })
      await Promise.all(
        ['config', 'config-backups', 'config-history', 'session'].map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
      )
    },
  })
  const review = () => {
    save.reset()
    setReviewing(true)
    preview.mutate()
  }
  const { kind } = proposal
  const isScript = kind === 'script'
  const open = proposal.status === 'proposed'
  const applyLabel = isScript
    ? `Save as version ${proposal.script!.base_version + 1}`
    : kind === 'new_script' ? 'Create the script' : 'Change the rule'
  const conflict = settle.error instanceof ApiError && settle.error.status === 409
  const savedConflict = save.error instanceof ApiError && save.error.status === 409
  const Icon = KIND_ICON[kind]

  return (
    <section id={`proposal-${proposal.id}`} aria-label={`Proposal: ${proposal.title}`}
      className="scroll-mt-14 space-y-3 rounded-xl border border-border bg-surface p-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-0.5">
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <Icon aria-hidden className="size-3.5" />
            {kind === 'script' && (
              <>A new version of <Link className="underline" to={`/ta/indicators/${encodeURIComponent(proposal.script!.id)}`}>{proposal.script!.name}</Link></>
            )}
            {kind === 'rule' && (
              <>A change to the rule <Link className="underline" to="/ta/signals">{proposal.rule!.name}</Link> ({proposal.rule!.mode})</>
            )}
            {kind === 'settings' && <>A change of settings in config.json</>}
            {kind === 'new_script' && (
              <>A new script, from <Link className="underline" to={`/ta/indicators/${encodeURIComponent(proposal.draft!.id)}`}>{proposal.draft!.name}</Link> v{proposal.draft!.version}</>
            )}
            {isScript && proposal.lines && <span>· +{proposal.lines.added} −{proposal.lines.removed} lines</span>}
            {kind === 'new_script' && proposal.lines && <span>· {proposal.lines.added} lines</span>}
          </p>
          <h4 className="text-sm font-semibold text-foreground">{proposal.title}</h4>
        </div>
        <Badge variant="soft" tone={proposal.status === 'applied' ? 'success' : proposal.status === 'dismissed' ? 'neutral' : 'warning'}>
          {proposal.status === 'proposed' ? 'Proposed' : proposal.status === 'applied' ? 'Applied' : 'Dismissed'}
        </Badge>
      </header>
      <p className="whitespace-pre-wrap text-sm text-text-secondary">{proposal.rationale}</p>
      {kind === 'new_script' && proposal.new_script && (
        <p className="text-sm">
          <span className="font-medium">{proposal.new_script.name}</span>
          {proposal.new_script.description && <span className="text-text-secondary">: {proposal.new_script.description}</span>}
        </p>
      )}
      {(kind === 'script' || kind === 'new_script') && (
        <div className="space-y-2">
          <Button size="sm" variant="ghost" aria-expanded={shown} onClick={() => setShown((value) => !value)}>
            {shown ? (isScript ? 'Hide the change' : 'Hide the script') : (isScript ? 'Show the change' : 'Show the script')}
          </Button>
          {shown && (isScript ? <ScriptChange proposal={proposal} /> : <NewScript proposal={proposal} />)}
        </div>
      )}
      {kind === 'rule' && <RuleChange proposal={proposal} proposals={proposals} />}
      {kind === 'settings' && <SettingsChange proposal={proposal} />}
      {proposal.status === 'applied' && proposal.result && (
        <p className="text-sm text-text-secondary">
          {kind === 'script' && (
            <>Saved as <Link className="underline" to={`/ta/indicators/${encodeURIComponent(proposal.result.script_id ?? '')}`}>version {proposal.result.version}</Link>, written by Claude.</>
          )}
          {kind === 'rule' && <>The rule runs version {proposal.result.script_version} now.</>}
          {kind === 'new_script' && (
            <>Created as <Link className="underline" to={`/ta/indicators/${encodeURIComponent(proposal.result.script_id ?? '')}`}>{proposal.result.name}</Link>, yours, its first version written by Claude.</>
          )}
          {kind === 'settings' && <SettingsSaved proposal={proposal} />}
          {proposal.settled_at && <span className="text-muted-foreground"> {formatDateTime(proposal.settled_at, timezone, false)}.</span>}
        </p>
      )}
      {open && (
        <div className="flex flex-wrap items-center gap-2">
          {kind === 'settings' ? (
            <Hint content={configEditable ? undefined : 'Configuration editing is switched off (admin.config_edit_enabled): set it on the server to save from here.'}
              wrap={!configEditable}>
              <Button size="sm" leftIcon={<Eye className="size-3.5" />} disabled={!configEditable} onClick={review}>
                Preview and save
              </Button>
            </Hint>
          ) : (
            <Hint content={blockedReason ?? undefined} wrap={!!blockedReason}>
              <Button size="sm" leftIcon={<Check className="size-3.5" />} disabled={!!blockedReason} isLoading={settle.isPending && settle.variables === true}
                onClick={() => settle.mutate(true)}>
                {applyLabel}
              </Button>
            </Hint>
          )}
          <Button size="sm" variant="ghost" leftIcon={<X className="size-3.5" />} isLoading={settle.isPending && settle.variables === false}
            onClick={() => settle.mutate(false)}>
            Dismiss
          </Button>
        </div>
      )}
      {settle.error && (
        <Alert tone="danger" role="alert">
          <AlertDescription>
            {settle.error.message}
            {conflict && isScript && ' Ask the agent to make its change on the version saved since.'}
          </AlertDescription>
        </Alert>
      )}
      {kind === 'settings' && (
        <ConfirmLiveAction
          open={reviewing}
          title="Save the agent's settings"
          confirmLabel="Slide to save"
          doneLabel="Saved"
          warning="Each service named applies it at its next start, a restart after a crash included."
          pending={save.isPending}
          blocked={!preview.isSuccess || !catalog.data}
          error={
            savedConflict
              ? 'The configuration was saved by someone else since this preview: close this dialog and preview it again.'
              : (save.error?.message ?? (preview.isError ? preview.error.message : null))
          }
          onConfirm={() => preview.data && save.mutate(preview.data.revision)}
          onClose={() => setReviewing(false)}
          description={
            preview.isSuccess && catalog.data ? (
              <div className="space-y-3">
                <p className="text-sm">
                  Saved as the Configuration page saves: the file of now kept as a backup, the new one checked with
                  the services&apos; own validators, the save written in the audit trail.
                </p>
                <ReviewChanges catalog={catalog.data} preview={preview.data} />
              </div>
            ) : preview.isError ? (
              <p className="text-sm">The change cannot be saved as it is.</p>
            ) : (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader size={16} /> Checking the change as a save would…
              </p>
            )
          }
        />
      )}
    </section>
  )
}
