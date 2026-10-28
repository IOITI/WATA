// Technical Analysis › Indicators › a script: the Pine editor, its checks as the
// script is written, its versions, and a test on WATA's prices.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertTitle, Button, ButtonLink, formatDateTime, Hint, Input, PageHeader, QueryState } from 'ioiti-ui'
import { RouterLeaveGuard } from 'ioiti-ui/react-router'
import { ChartSpline, FlaskConical, History, Radar, Save, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'

import { NotFoundState } from '@/components/NotFoundState'
import { AgentSheet } from '@/features/agent/AgentSheet'
import { NEW, withAgent } from '@/features/agent/model'
import { useSession } from '@/features/auth/auth-context'
import { isSignIn } from '@/features/auth/returnPath'
import { api, ApiError, isNotFound } from '@/lib/api'
import type { TaScript, TaScriptKind } from '@/lib/types'

import { chartsPath } from './chartLinks'
import { DiagnosticsList } from './editor/DiagnosticsList'
import { HistoryDialog } from './editor/HistoryDialog'
import { PineEditor } from './editor/PineEditor'
import { TestPanel } from './editor/TestPanel'
import type { TestDone } from './editor/TestPanel'
import { TestResult } from './editor/TestResult'
import { usePineReference, usePineWorker, useScriptCheck } from './editor/useScriptCheck'
import { declaredKind, declaredTitle, isKind, KIND_LABEL, TEMPLATES } from './templates'

interface Draft {
  source: string
  name: string
  description: string
  /** The version the text was started from: a save made meanwhile is not overwritten (409). */
  base: number
}

function fromScript(script: TaScript): Draft {
  return { source: script.source, name: script.name, description: script.description, base: script.version }
}

/** One editor per script: another script, or the one just created, starts from its own text. */
export function ScriptEditorPage() {
  const { id } = useParams()
  return <ScriptEditor key={id ?? 'new'} id={id} />
}

function ScriptEditor({ id }: { id: string | undefined }) {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { timezone, backtests_enabled: backtestsEnabled } = useSession()
  const newKind: TaScriptKind = isKind(searchParams.get('kind')) ? (searchParams.get('kind') as TaScriptKind) : 'indicator'

  const scriptQuery = useQuery({
    queryKey: ['ta-script', id],
    queryFn: () => api.taScript(id as string),
    enabled: !!id,
    // What is being edited is not replaced by a refetch behind its back.
    refetchOnWindowFocus: false,
  })
  const script = id ? scriptQuery.data : undefined

  const [draft, setDraft] = useState<Draft | null>(() =>
    id ? null : { source: TEMPLATES[newKind], name: '', description: '', base: 0 },
  )
  const [conflict, setConflict] = useState<string | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  // The last test that ran, drawn under the editor.
  const [tested, setTested] = useState<TestDone | null>(null)
  // A script just created, or the changes saved as a copy: the page goes to it once that is drawn,
  // and what it leaves is saved, so the guard against losing changes lets it go.
  const [leavingTo, setLeavingTo] = useState<string | null>(null)
  useEffect(() => {
    if (leavingTo) navigate(`/ta/indicators/${leavingTo}`, { replace: !id })
  }, [leavingTo, navigate, id])

  // The script read (first, or again after a conflict): what the editor starts from.
  useEffect(() => {
    if (script && (draft === null || draft.base === 0)) setDraft(fromScript(script))
    // Only a script read for the first time replaces the draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [script])

  const source = draft?.source ?? ''
  const kind = declaredKind(source) ?? script?.kind ?? newKind
  const libraryName = kind === 'library' ? declaredTitle(source) ?? 'Library' : null

  const worker = usePineWorker()
  const reference = usePineReference()
  const check = useScriptCheck(source, libraryName, draft ? worker : null, reference)

  const dirty = useMemo(() => {
    if (!draft) return false
    if (!script) return draft.source !== TEMPLATES[newKind] || draft.name.trim() !== ''
    return draft.source !== script.source || draft.name !== script.name || draft.description !== script.description
  }, [draft, script, newKind])

  const update = useCallback((changes: Partial<Draft>) => setDraft((before) => (before ? { ...before, ...changes } : before)), [])

  const saveMutation = useMutation({
    mutationFn: async (): Promise<TaScript> => {
      if (!draft) throw new Error('Nothing to save')
      const name = draft.name.trim() || declaredTitle(draft.source) || `Untitled ${KIND_LABEL[kind].toLowerCase()}`
      if (!id) return api.createTaScript({ name, source: draft.source, description: draft.description })
      return api.saveTaScript(id, { base_version: draft.base, source: draft.source, name, description: draft.description })
    },
    onSuccess: (saved) => {
      setConflict(null)
      queryClient.setQueryData(['ta-script', saved.id], saved)
      void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
      void queryClient.invalidateQueries({ queryKey: ['ta-library'] })
      setDraft(fromScript(saved))
      if (!id) setLeavingTo(saved.id)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409 && error.message.includes('saved meanwhile')) {
        setConflict(error.message)
      }
    },
  })

  const copyMutation = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error('Nothing to save')
      return api.createTaScript({
        name: `${draft.name.trim() || 'Script'} (copy)`,
        source: draft.source,
        description: draft.description,
      })
    },
    onSuccess: (copy) => {
      void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
      setConflict(null)
      setLeavingTo(copy.id)
    },
  })

  const reload = async () => {
    const fresh = await queryClient.fetchQuery({ queryKey: ['ta-script', id], queryFn: () => api.taScript(id as string) })
    setDraft(fromScript(fresh))
    setConflict(null)
  }

  const save = useCallback(() => {
    if (dirty && !saveMutation.isPending) saveMutation.mutate()
  }, [dirty, saveMutation])

  const title = draft?.name.trim() || script?.name || `New ${KIND_LABEL[newKind].toLowerCase()}`
  const errors = check.diagnostics.filter((diagnostic) => diagnostic.severity === 'error').length

  const saveButton = (
    <Button size="sm" leftIcon={<Save className="size-3.5" />} isLoading={saveMutation.isPending} disabled={!dirty} onClick={save}>
      {dirty ? 'Save' : 'Saved'}
    </Button>
  )

  if (id && isNotFound(scriptQuery.error)) {
    return (
      <NotFoundState
        title="No such script"
        description="No indicator, strategy or library has this address: it may have been deleted, or the address is mistyped."
        suggestions={[
          { label: 'Indicators', href: '/ta/indicators', description: 'Every script kept' },
          { label: 'New script', href: '/ta/indicators/new', description: 'Write one' },
        ]}
      />
    )
  }

  return (
    <>
      <PageHeader
        title={title}
        description={
          script
            ? `${KIND_LABEL[script.kind]} · Pine v${script.pine_version} · version ${script.version}, saved ${formatDateTime(script.updated_at, timezone, false)}`
            : `A new ${KIND_LABEL[newKind].toLowerCase()}: it is kept once saved.`
        }
        actions={
          <>
            {script && (
              <Button variant="outline" size="sm" leftIcon={<History className="size-3.5" />} onClick={() => setHistoryOpen(true)}>
                History
              </Button>
            )}
            {/* The next steps of a script that runs, each a page of its own: as saved, not as being edited. */}
            {script && kind !== 'library' && (
              <>
                <ButtonLink to={chartsPath({ script: script.id })} variant="outline" size="sm" leftIcon={<ChartSpline className="size-3.5" />}>
                  Open in Charts
                </ButtonLink>
                {backtestsEnabled && (
                  <Hint content={`Backtest version ${script.version}, the one saved last`}>
                    <ButtonLink
                      to={`/backtests/new?script=${script.id}&version=${script.version}`}
                      variant="outline"
                      size="sm"
                      leftIcon={<FlaskConical className="size-3.5" />}
                    >
                      Backtest v{script.version}
                    </ButtonLink>
                  </Hint>
                )}
                <ButtonLink to={`/ta/signals?new=${script.id}`} variant="outline" size="sm" leftIcon={<Radar className="size-3.5" />}>
                  New signal rule
                </ButtonLink>
                <ButtonLink to={withAgent(searchParams, NEW)} variant="outline" size="sm" leftIcon={<Sparkles className="size-3.5" />}>
                  Ask the AI agent
                </ButtonLink>
              </>
            )}
            <Hint content={errors > 0 ? 'It can be saved with its errors, and fixed later' : null} wrap={!dirty}>
              {saveButton}
            </Hint>
          </>
        }
      />

      {script && (
        <AgentSheet
          subject={{ kind: 'script', id: script.id, label: script.name, version: script.version }}
          // A new version of this script, while the editor holds changes not saved: they would be left behind.
          blockedFor={(proposal) => (dirty && proposal.kind === 'script' && proposal.script?.id === script.id
            ? 'Save or drop your changes in the editor first: applying it saves a new version.' : null)}
          // A new version saved from its proposal: the editor starts again from it.
          onApplied={(proposal) => {
            if (proposal.kind !== 'script' || proposal.script?.id !== script.id) return
            void queryClient.fetchQuery({ queryKey: ['ta-script', script.id], queryFn: () => api.taScript(script.id) })
              .then((fresh) => setDraft(fromScript(fresh)))
          }}
        />
      )}
      <QueryState isPending={!!id && scriptQuery.isPending} error={id ? scriptQuery.error : null} hasData={!!draft}>
        {draft && (
          <>
            {conflict && (
              <Alert tone="warning">
                <AlertTitle>Changed elsewhere</AlertTitle>
                <p className="text-sm">{conflict}.</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={() => void reload()}>
                    Reload it (drop these changes)
                  </Button>
                  <Button size="sm" isLoading={copyMutation.isPending} onClick={() => copyMutation.mutate()}>
                    Save these changes as a copy
                  </Button>
                </div>
              </Alert>
            )}
            {saveMutation.error && !conflict && (
              <p role="alert" className="text-sm text-negative">
                {saveMutation.error.message}
              </p>
            )}

            <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_24rem]">
              <div className="flex min-w-0 flex-col gap-3">
                <div className="grid gap-2 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
                  <Input
                    aria-label="Name"
                    placeholder={declaredTitle(source) ?? 'Name'}
                    value={draft.name}
                    onChange={(event) => update({ name: event.target.value })}
                  />
                  <Input
                    aria-label="Description"
                    placeholder="What it shows, in a sentence"
                    value={draft.description}
                    onChange={(event) => update({ description: event.target.value })}
                  />
                </div>
                <PineEditor
                  value={draft.source}
                  onChange={(next) => update({ source: next })}
                  diagnostics={check.diagnostics}
                  label={`Pine source of ${title}`}
                  onSave={save}
                  functions={reference?.functions}
                  className="h-[60vh] min-h-[420px]"
                />
                <DiagnosticsList diagnostics={check.diagnostics} checking={check.checking} />
                {check.result?.prepared && check.result.prepared.libraries.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    Libraries inlined:{' '}
                    {check.result.prepared.libraries.map((library) => `${library.name} (version ${library.version})`).join(', ')}.
                  </p>
                )}
              </div>
              <div className="flex min-w-0 flex-col gap-4">
                {kind === 'library' ? (
                  <section className="rounded-2xl border border-border/70 p-5 text-sm text-text-secondary">
                    A library runs inside the scripts that import it:{' '}
                    <code className="font-mono text-xs">
                      import you/{libraryName}/1 as {libraryName ? libraryName.charAt(0).toLowerCase() + libraryName.slice(1) : 'lib'}
                    </code>
                    . It is checked here inside an empty indicator.
                  </section>
                ) : (
                  <TestPanel
                    prepared={check.result?.compiled ? check.result.prepared : null}
                    inputs={check.result?.compiled?.inputs ?? []}
                    worker={worker}
                    scriptId={script?.id ?? null}
                    scriptName={title}
                    onResult={setTested}
                  />
                )}
              </div>
            </div>
            {tested && kind !== 'library' && <TestResult run={tested} scriptName={title} timeZone={timezone} />}
          </>
        )}
      </QueryState>

      {script && draft && (
        <HistoryDialog
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          script={script}
          current={draft.source}
          onRestore={(restored) => {
            update({ source: restored })
            setHistoryOpen(false)
          }}
        />
      )}
      <RouterLeaveGuard
        when={dirty && leavingTo === null}
        allowed={isSignIn}
        message="The script has changes. Nothing of it has been saved."
        stayLabel="Stay on this page"
        leaveLabel="Leave and discard"
      />
    </>
  )
}
