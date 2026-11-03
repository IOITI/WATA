// A script's page, its How it works tab: what the script does, in Markdown, written by the operator, by the AI
// agent (its describe_script), or LuxAlgo's write-up of an indicator taken from its Library. Every save is a new
// revision, none written again: the history lists them, any one put back as a new one. The text being written is
// the page's (`draft`): another tab keeps it, and leaving the page asks first.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertTitle, Button, ButtonLink, formatDateTime, Hint, Panel, QueryState, Textarea, useToast } from 'ioiti-ui'
import { BookOpen, History, Pencil, RotateCcw, Save, Sparkles } from 'lucide-react'
import { useId, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { NEW, withAgent } from '@/features/agent/model'
import { useAgentState } from '@/features/agent/useConversation'
import { useSession } from '@/features/auth/auth-context'
import { useLuxAlgoStatus } from '@/features/ta/luxalgo/useLuxAlgo'
import { api, ApiError } from '@/lib/api'
import type { TaScript, TaScriptHow, TaScriptHowMeta, TaScriptSummary } from '@/lib/types'

import { WriteUp } from '../WriteUp'
import { howBy, howShown } from './scriptPageModel'

/** The longest How it works kept (MAX_HOW of src/admin/script_details.py). */
export const MAX_HOW = 30_000

export interface HowDraft {
  text: string
  /** The revision it was started from: one saved meanwhile is not written over (409). */
  revision: number
  /** The text it was started from: unchanged, leaving loses nothing. */
  original: string
}

const howKey = (id: string) => ['ta-script-how', id]
const historyKey = (id: string) => ['ta-script-how-history', id]

/** "Written by you, 3 Nov 2026 14:02 · revision 2", leading to LuxAlgo's page or the agent's conversation. */
function Byline({ meta, timeZone, revision = true }: { meta: TaScriptHowMeta; timeZone: string; revision?: boolean }) {
  const by = howBy(meta)
  const who = !by.href ? by.said : by.href.startsWith('http') ? (
    <a href={by.href} target="_blank" rel="noreferrer" className="underline underline-offset-4">{by.said}</a>
  ) : (
    <Link to={by.href} className="underline underline-offset-4">{by.said}</Link>
  )
  const taken = meta.from?.provider === 'luxalgo' && meta.by === 'operator' ? ', taken by you' : ''
  return (
    <p className="text-xs text-muted-foreground">
      {meta.from?.provider === 'luxalgo' ? who : <>Written by {who}</>}
      {taken}
      {meta.at ? `, ${formatDateTime(meta.at, timeZone, false)}` : ''}
      {revision ? ` · revision ${meta.revision}` : ''}
    </p>
  )
}

/** Its revisions, the newest first: each shown on demand, and put back as a new one. */
function HowHistory({ script, current, onRestore, restoring }: {
  script: TaScriptSummary
  current: number
  onRestore: (entry: TaScriptHow) => void
  restoring: boolean
}) {
  const { timezone } = useSession()
  const history = useQuery({ queryKey: historyKey(script.id), queryFn: () => api.taScriptHowHistory(script.id) })
  const [open, setOpen] = useState<number | null>(null)
  return (
    <Panel title="Its revisions" description="Every save is kept: one put back becomes the newest." dense>
      <QueryState isPending={history.isPending} error={history.error} hasData={!!history.data}>
        <ul aria-label="Its revisions" className="divide-y divide-border/60">
          {(history.data?.items ?? []).map((entry) => (
            <li key={entry.revision} className="flex flex-col gap-2 py-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    Revision {entry.revision}
                    {entry.revision === current ? ' · now' : ''}
                    {!entry.text ? ' · emptied' : ''}
                  </p>
                  <Byline meta={entry} timeZone={timezone} revision={false} />
                </div>
                <div className="flex gap-2">
                  {entry.text && (
                    <Button variant="ghost" size="sm" aria-expanded={open === entry.revision}
                      onClick={() => setOpen(open === entry.revision ? null : entry.revision)}>
                      {open === entry.revision ? 'Hide it' : 'Show it'}
                    </Button>
                  )}
                  {entry.revision !== current && (
                    <Button variant="outline" size="sm" leftIcon={<RotateCcw className="size-3.5" />} isLoading={restoring}
                      onClick={() => onRestore(entry)}>
                      Put this one back
                    </Button>
                  )}
                </div>
              </div>
              {open === entry.revision && (
                <div className="rounded-xl border border-border/70 p-4">
                  <WriteUp markdown={howShown(entry.text, entry.from)} />
                </div>
              )}
            </li>
          ))}
        </ul>
      </QueryState>
    </Panel>
  )
}

export function ScriptHowTab({ script, draft, onDraft }: {
  script: TaScript
  draft: HowDraft | null
  onDraft: (draft: HowDraft | null) => void
}) {
  const { timezone } = useSession()
  const [params] = useSearchParams()
  const queryClient = useQueryClient()
  const toast = useToast()
  const how = useQuery({ queryKey: howKey(script.id), queryFn: () => api.taScriptHow(script.id) })
  const agentOn = !!useAgentState().data?.enabled
  const luxalgoOn = !!useLuxAlgoStatus().data?.enabled
  const fromLuxAlgo = script.origin?.provider === 'luxalgo' && !!script.origin.slug
  const [showHistory, setShowHistory] = useState(false)
  const [conflict, setConflict] = useState<string | null>(null)
  const fieldId = useId()

  /** A revision saved: the tab, the list's and the page's scripts, and the history read again. */
  const saved = (answer: TaScriptHow) => {
    queryClient.setQueryData(howKey(script.id), answer)
    void queryClient.invalidateQueries({ queryKey: historyKey(script.id) })
    void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
    void queryClient.invalidateQueries({ queryKey: ['ta-script', script.id] })
  }

  const save = useMutation({
    mutationFn: ({ text, revision }: { text: string; revision: number }) => api.saveTaScriptHow(script.id, text, revision),
    onSuccess: (answer) => {
      setConflict(null)
      saved(answer)
      onDraft(null)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) setConflict(error.message)
    },
  })

  // Another's revision saved meanwhile: the text written here saved over it, the one before kept in the history.
  const overIt = async () => {
    if (!draft) return
    const fresh = await queryClient.fetchQuery({ queryKey: howKey(script.id), queryFn: () => api.taScriptHow(script.id) })
    save.mutate({ text: draft.text, revision: fresh.revision })
  }

  const restore = useMutation({
    mutationFn: (entry: TaScriptHow) => api.saveTaScriptHow(script.id, entry.text, how.data?.revision ?? 0),
    onSuccess: (answer, entry) => {
      saved(answer)
      toast({ title: `Revision ${entry.revision} put back, as revision ${answer.revision}`, variant: 'success' })
    },
    onError: (error) => toast({ title: 'It was not put back', description: error.message, variant: 'error' }),
  })

  const take = useMutation({
    mutationFn: () => api.takeLuxAlgoDetails(script.id, true),
    onSuccess: (answer) => {
      void queryClient.invalidateQueries({ queryKey: howKey(script.id) })
      void queryClient.invalidateQueries({ queryKey: historyKey(script.id) })
      void queryClient.invalidateQueries({ queryKey: ['ta-scripts'] })
      queryClient.setQueryData<TaScript>(['ta-script', script.id], (before) => (before ? { ...before, ...answer.script } : before))
      toast({
        title: answer.filled.includes('how') ? "LuxAlgo's write-up taken" : 'LuxAlgo gave no write-up of it',
        variant: answer.filled.includes('how') ? 'success' : 'default',
      })
    },
    onError: (error) => toast({ title: "LuxAlgo's write-up was not taken", description: error.message, variant: 'error' }),
  })

  const edit = () => {
    setConflict(null)
    onDraft({ text: how.data?.text ?? '', revision: how.data?.revision ?? 0, original: how.data?.text ?? '' })
  }

  // Beside its title only the edit: the panel's action never wraps, and a phone has no room for more.
  const editButton = !draft && how.data && (
    <Button size="sm" leftIcon={<Pencil className="size-3.5" />} onClick={edit}>
      {how.data.text ? 'Edit' : 'Write it'}
    </Button>
  )
  const actions = (
    <div className="flex flex-wrap gap-2">
      {agentOn && (
        <Hint content="Its requests include “Write its How it works”: what it writes is kept here, every revision too">
          <ButtonLink to={withAgent(params, NEW)} variant="outline" size="sm" leftIcon={<Sparkles className="size-3.5" />}>
            Ask the AI agent
          </ButtonLink>
        </Hint>
      )}
      {fromLuxAlgo && luxalgoOn && (
        <Hint content="LuxAlgo's write-up of the indicator it was taken from, as a new revision: this one stays in the history">
          <Button variant="outline" size="sm" leftIcon={<BookOpen className="size-3.5" />} isLoading={take.isPending} onClick={() => take.mutate()}>
            Take LuxAlgo&apos;s
          </Button>
        </Hint>
      )}
      {(how.data?.revision ?? 0) > 0 && (
        <Button variant="ghost" size="sm" leftIcon={<History className="size-3.5" />} aria-expanded={showHistory}
          onClick={() => setShowHistory((before) => !before)}>
          {showHistory ? 'Hide its revisions' : 'Its revisions'}
        </Button>
      )}
    </div>
  )

  if (draft) {
    const changed = draft.text !== draft.original
    return (
      <Panel
        title="How it works"
        description="In Markdown: what it shows, how it decides, what its inputs change. Saved as a new revision."
        action={
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => { setConflict(null); onDraft(null) }}>
              {changed ? 'Drop the changes' : 'Cancel'}
            </Button>
            <Button size="sm" leftIcon={<Save className="size-3.5" />} isLoading={save.isPending} disabled={!changed}
              onClick={() => save.mutate({ text: draft.text, revision: draft.revision })}>
              Save
            </Button>
          </div>
        }
      >
        <div className="flex flex-col gap-3">
          {conflict && (
            <Alert tone="warning">
              <AlertTitle>Saved meanwhile</AlertTitle>
              <p className="text-sm">{conflict}. What you wrote is still here.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => { setConflict(null); onDraft(null); void how.refetch() }}>
                  Read theirs (drop mine)
                </Button>
                <Button size="sm" isLoading={save.isPending} onClick={() => void overIt()}>
                  Save mine over it
                </Button>
              </div>
            </Alert>
          )}
          {save.error && !conflict && (
            <p role="alert" className="text-sm text-negative">
              {save.error.message}
            </p>
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="flex flex-col gap-1 text-xs">
              <label htmlFor={fieldId} className="text-text-secondary">Its text, in Markdown</label>
              <Textarea
                id={fieldId}
                value={draft.text}
                rows={18}
                maxLength={MAX_HOW}
                className="font-mono text-sm"
                onChange={(event) => onDraft({ ...draft, text: event.target.value })}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === 's') {
                    event.preventDefault()
                    if (changed && !save.isPending) save.mutate({ text: draft.text, revision: draft.revision })
                  }
                }}
              />
              <span className="text-muted-foreground">
                {draft.text.length.toLocaleString('en-GB')} of {MAX_HOW.toLocaleString('en-GB')} characters
              </span>
            </div>
            <section aria-label="As it will be shown" className="min-w-0 rounded-xl border border-border/70 p-4">
              {draft.text.trim() ? (
                <WriteUp markdown={howShown(draft.text, null)} />
              ) : (
                <p className="text-sm text-muted-foreground">Saved empty, the script has no How it works.</p>
              )}
            </section>
          </div>
        </div>
      </Panel>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <Panel title="How it works" action={editButton || undefined}>
        <QueryState isPending={how.isPending} error={how.error} hasData={!!how.data}>
          <div className="flex flex-col gap-3">
            {(agentOn || (fromLuxAlgo && luxalgoOn) || (how.data?.revision ?? 0) > 0) && actions}
            {how.data?.text ? (
              <>
                <Byline meta={how.data} timeZone={timezone} />
                <WriteUp markdown={howShown(how.data.text, how.data.from)} />
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                Nothing says yet what {script.name} does: write it, or ask the AI agent to.
                {fromLuxAlgo ? " LuxAlgo's write-up of the indicator it was taken from can be taken as it." : ''}
              </p>
            )}
          </div>
        </QueryState>
      </Panel>
      {showHistory && (
        <HowHistory script={script} current={how.data?.revision ?? 0} restoring={restore.isPending}
          onRestore={(entry) => restore.mutate(entry)} />
      )}
    </div>
  )
}
