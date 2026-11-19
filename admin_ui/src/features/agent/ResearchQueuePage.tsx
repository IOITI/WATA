// AI agent › Research queue: the hypotheses the research autopilot works through (src/admin/research_queue.py), the
// operator's first in their order, then those research tasks left, a family tried long with nothing that held last.
// The operator files one, moves theirs, takes a task's as theirs, drops one (with an Undo) or puts one back; each
// change is sent with the revision the page read (409 once the queue moved on).

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  Badge,
  Button,
  ButtonLink,
  FormDescription,
  FormItem,
  FormLabel,
  formatDateTime,
  Hint,
  Input,
  PageHeader,
  Panel,
  QueryState,
  SettleModal,
  Textarea,
  UndoToast,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { ArrowDown, ArrowUp, Plus, RotateCcw, Trash2, UserCheck } from 'lucide-react'
import { useId, useMemo, useState } from 'react'
import { Link } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { RunPeekLink } from '@/features/backtests/RunPeek'
import { ApiError, api } from '@/lib/api'
import type { QueueDraft, QueueItem } from '@/lib/types'

import {
  asYours,
  criteriaSaid,
  draftProblem,
  emptyDraft,
  filedBy,
  filedByTask,
  movedTo,
  parseDays,
  QUEUE_STATE,
  sourceRun,
  splitQueue,
} from './queueModel'
import { useAgentState } from './useConversation'

const UNDO_MS = 8000

function errorText(error: Error | null): string | null {
  if (error instanceof ApiError && error.status === 409) return `${error.message}.`
  return error?.message ?? null
}

/** A hypothesis of the operator's, filed last of theirs. */
function NewHypothesisDialog({ onClose }: { onClose: () => void }) {
  const id = useId()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<QueueDraft>(emptyDraft)
  const [days, setDays] = useState('')
  const filled = { ...draft, motivated_by: parseDays(days) }
  const problem = draftProblem(filled)
  const save = useMutation({
    mutationFn: () => api.addHypothesis(filled),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['research-queue'] })
      onClose()
    },
  })
  const change = (part: Partial<QueueDraft>) => setDraft((before) => ({ ...before, ...part }))
  return (
    <SettleModal
      open
      onClose={save.isPending ? () => {} : onClose}
      title="A hypothesis to try"
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button size="sm" isLoading={save.isPending} disabled={!!problem} onClick={() => save.mutate()}>
            File it
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault()
          if (!problem) save.mutate()
        }}
      >
        <FormItem>
          <FormLabel htmlFor={`${id}-title`}>Title</FormLabel>
          <Input id={`${id}-title`} value={draft.title} maxLength={120} placeholder="e.g. No entry in the open's first ten minutes"
            onChange={(event) => change({ title: event.target.value })} />
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-hypothesis`}>The hypothesis</FormLabel>
          <Textarea id={`${id}-hypothesis`} rows={4} value={draft.hypothesis} maxLength={2000}
            placeholder="What the data could refute: a run can say it is wrong."
            onChange={(event) => change({ hypothesis: event.target.value })} />
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-family`}>Family of ideas</FormLabel>
          <Input id={`${id}-family`} value={draft.family} maxLength={80} spellCheck={false} className="font-mono"
            placeholder="e.g. kernel-avg-vwap" onChange={(event) => change({ family: event.target.value })} />
          <FormDescription>
            One the experiment journal knows when the idea is the same: every try of a family counts against its later runs.
          </FormDescription>
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-rationale`}>What it comes from (optional)</FormLabel>
          <Textarea id={`${id}-rationale`} rows={3} value={draft.rationale ?? ''} maxLength={2000}
            onChange={(event) => change({ rationale: event.target.value || null })} />
        </FormItem>
        <FormItem>
          <FormLabel htmlFor={`${id}-days`}>The days it came from (optional)</FormLabel>
          <Input id={`${id}-days`} value={days} spellCheck={false} placeholder="2026-11-11, 2026-11-12"
            onChange={(event) => setDays(event.target.value)} />
          <FormDescription>A run of it is also judged without them: a result that needs them is not believed.</FormDescription>
        </FormItem>
        {problem && filled.title && <p className="text-xs text-muted-foreground">{problem}</p>}
        {save.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>{errorText(save.error)}</AlertDescription>
          </Alert>
        )}
      </form>
    </SettleModal>
  )
}

/** Where a hypothesis came from, and what the autopilot made of it. */
function ItemFacts({ item, timezone }: { item: QueueItem; timezone: string }) {
  const task = filedByTask(item)
  const run = sourceRun(item)
  return (
    <p className="text-xs text-muted-foreground">
      <Badge variant="outline" className="mr-1.5 font-mono">{item.family}</Badge>
      {task ? (
        <>
          Left by <Link className="underline underline-offset-2" to={`/agent/${encodeURIComponent(task)}`}>a research task</Link>
        </>
      ) : filedBy(item)}
      {run && (
        <>
          {' '}(<RunPeekLink id={run} className="font-mono underline underline-offset-2">{run}</RunPeekLink>)
        </>
      )}
      , {formatDateTime(item.created_at, timezone, false)}
      {item.criteria.length > 0 && <> · must show: {criteriaSaid(item.criteria)}</>}
      {item.motivated_by.length > 0 && <> · from {item.motivated_by.join(', ')}</>}
    </p>
  )
}

export function ResearchQueuePage() {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const state = useAgentState()
  const queue = useQuery({ queryKey: ['research-queue'], queryFn: api.researchQueue })
  const [adding, setAdding] = useState(false)
  // Dropped at once on the page, sent once the Undo's time is over: nothing is sent before.
  const [hidden, setHidden] = useState<QueueItem | null>(null)
  const revision = queue.data?.revision ?? ''
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['research-queue'] })
  const change = useMutation({
    mutationFn: ({ item, ...body }: { item: QueueItem; state?: 'waiting' | 'dropped'; move?: number }) =>
      api.changeHypothesis(item.id, revision, body),
    onSettled: refresh,
  })
  const { waiting, others } = useMemo(() => splitQueue((queue.data?.items ?? []).filter((item) => item.id !== hidden?.id)), [queue.data, hidden])

  const columns = useMemo<ColumnDef<QueueItem>[]>(() => [
    {
      id: 'title',
      header: 'Hypothesis',
      accessorFn: (row) => `${row.title} ${row.hypothesis} ${row.family}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[30rem] whitespace-normal">
          <span className="font-medium">{row.title}</span>
          <p className="line-clamp-2 text-xs text-muted-foreground">{row.hypothesis}</p>
        </div>
      ),
    },
    {
      id: 'state',
      header: 'State',
      accessorFn: (row) => QUEUE_STATE[row.state].label,
      sortable: true,
      filterable: true,
      cell: ({ row }) => <Badge tone={QUEUE_STATE[row.state].tone}>{QUEUE_STATE[row.state].label}</Badge>,
    },
    {
      id: 'outcome',
      header: 'What became of it',
      accessorFn: (row) => row.conclusion ?? '',
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[26rem] whitespace-normal text-xs">
          {row.conversation && (
            <Link className="underline underline-offset-2" to={`/agent/${encodeURIComponent(row.conversation)}`}>
              Its task
            </Link>
          )}
          {row.experiment && <> · experiment <Link className="font-mono underline underline-offset-2" to="/backtests/experiments">{row.experiment}</Link></>}
          {row.conclusion && <p className="mt-0.5 text-muted-foreground">{row.conclusion}</p>}
        </div>
      ),
    },
    {
      id: 'actions',
      header: 'Actions',
      align: 'right',
      cell: ({ row }) =>
        row.state === 'taken' ? null : (
          <Hint content="Put it back: it waits again, last of yours">
            <Button variant="ghost" size="icon" aria-label={`Put ${row.title} back in the queue`}
              onClick={() => change.mutate({ item: row, state: 'waiting' })}>
              <RotateCcw className="size-4" />
            </Button>
          </Hint>
        ),
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [revision])

  const autopilot = queue.data?.autopilot
  return (
    <div className="space-y-6">
      <PageHeader
        title="Research queue"
        description="Hypotheses waiting to be tried, in the order the research autopilot takes them: yours first, then those research tasks left."
        actions={
          <Button size="sm" leftIcon={<Plus className="size-3.5" />} onClick={() => setAdding(true)}>
            New hypothesis
          </Button>
        }
      />
      <QueryState isPending={queue.isPending} error={queue.error} hasData={!!queue.data}>
        {queue.data && (
          <div className="space-y-4">
            {state.data && !state.data.enabled ? (
              <Alert tone="neutral">
                <AlertDescription>
                  The AI agent is off: hypotheses are kept here, and tried once it is on with its research autopilot.
                </AlertDescription>
              </Alert>
            ) : !autopilot ? (
              <Alert tone="neutral">
                <AlertDescription>
                  <span>No research autopilot yet: make it from a template on Routines. Each trading day once the market is closed, it takes the next hypothesis here.</span>{' '}
                  <ButtonLink to="/agent/routines" size="sm" variant="outline" className="ml-1">Routines</ButtonLink>
                </AlertDescription>
              </Alert>
            ) : !autopilot.enabled ? (
              <Alert tone="neutral">
                <AlertDescription>{autopilot.name} is switched off: nothing here is tried until it is on again (Routines).</AlertDescription>
              </Alert>
            ) : null}
            {queue.data.tired.length > 0 && (
              <p className="text-sm text-text-secondary">
                Tried long with nothing that held on later days, these families wait behind the others: {queue.data.tired.join(', ')}.
              </p>
            )}
            {change.error && (
              <Alert tone="danger" role="alert">
                <AlertDescription>{errorText(change.error)}</AlertDescription>
              </Alert>
            )}
            <Panel
              title="Waiting"
              description={`Research tasks keep ${queue.data.limits.agent_waiting} of theirs waiting at most, ${queue.data.limits.per_conversation} from one task.`}
            >
              {waiting.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing waits. The autopilot then reads the forward checks and the newest autopsies, and files what deserves an experiment.
                </p>
              ) : (
                <ol aria-label="Waiting, in the order they are tried" className="flex flex-col divide-y divide-border">
                  {waiting.map((item, place) => {
                    const yours = item.by === 'operator'
                    const up = yours ? movedTo(waiting, item.id, -1) : null
                    const down = yours ? movedTo(waiting, item.id, 1) : null
                    return (
                      <li key={item.id} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="flex min-w-0 gap-3">
                          <span className="mt-0.5 w-5 shrink-0 text-right text-sm tabular-nums text-muted-foreground">{place + 1}</span>
                          <div className="min-w-0">
                            <p className="font-medium">{item.title}</p>
                            <p className="text-sm text-text-secondary">{item.hypothesis}</p>
                            {item.rationale && <p className="text-xs text-muted-foreground">{item.rationale}</p>}
                            <ItemFacts item={item} timezone={timezone} />
                          </div>
                        </div>
                        <div className="flex shrink-0 justify-end gap-1">
                          {yours ? (
                            <>
                              <Hint content="Tried sooner" wrap={up === null}>
                                <Button variant="ghost" size="icon" aria-label={`Move ${item.title} up`} disabled={up === null}
                                  onClick={() => up !== null && change.mutate({ item, move: up })}>
                                  <ArrowUp className="size-4" />
                                </Button>
                              </Hint>
                              <Hint content="Tried later" wrap={down === null}>
                                <Button variant="ghost" size="icon" aria-label={`Move ${item.title} down`} disabled={down === null}
                                  onClick={() => down !== null && change.mutate({ item, move: down })}>
                                  <ArrowDown className="size-4" />
                                </Button>
                              </Hint>
                            </>
                          ) : (
                            <Hint content="Take it as yours: tried before every task's, last of yours">
                              <Button variant="ghost" size="icon" aria-label={`Take ${item.title} as yours`}
                                onClick={() => change.mutate({ item, move: asYours(waiting) })}>
                                <UserCheck className="size-4" />
                              </Button>
                            </Hint>
                          )}
                          <Hint content="Drop it">
                            <Button variant="ghost" size="icon" aria-label={`Drop ${item.title}`} onClick={() => {
                              if (hidden) change.mutate({ item: hidden, state: 'dropped' })
                              setHidden(item)
                            }}>
                              <Trash2 className="size-4" />
                            </Button>
                          </Hint>
                        </div>
                      </li>
                    )
                  })}
                </ol>
              )}
            </Panel>
            <AdvancedDataTable<QueueItem>
              title="Tried, being tried, dropped"
              searchPlaceholder="Search a hypothesis…"
              emptyTitle="Nothing tried yet"
              emptyDescription="Each hypothesis the autopilot takes is listed here with its task, the experiment it became and what it concluded."
              data={others}
              columns={columns}
              getRowId={(row) => row.id}
              defaultPageSize={10}
              selectable={false}
            />
          </div>
        )}
      </QueryState>
      {adding && <NewHypothesisDialog onClose={() => setAdding(false)} />}
      {hidden && (
        <UndoToast
          key={hidden.id}
          open
          title="Hypothesis dropped"
          description={hidden.title}
          restoredMessage="Kept: it still waits"
          duration={UNDO_MS}
          onUndo={() => setHidden(null)}
          onDismiss={() => {
            if (hidden) change.mutate({ item: hidden, state: 'dropped' })
            setHidden(null)
          }}
        />
      )}
    </div>
  )
}
