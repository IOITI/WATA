// The AI agent's section: what it costs this month, its key, its pause, what it is working on, every
// conversation (research tasks and daily debriefs marked), a new one and a new research task. A conversation is
// removed with an Undo, as backtests are.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  Badge,
  Button,
  ButtonLink,
  formatDateTime,
  KpiGrid,
  KpiTile,
  Meter,
  PageHeader,
  Panel,
  QueryState,
  UndoToast,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { Pause, Play, Settings, Trash2 } from 'lucide-react'
import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AgentConversationListed } from '@/lib/types'

import { AgentComposer } from './AgentComposer'
import {
  askedBy,
  costText,
  isUnderWay,
  OUTCOME_LABEL,
  STATUS_LABEL,
  statusTone,
  moreAttached,
  subjectLink,
  subjectSaid,
  TASK_LABEL,
  taskTone,
} from './model'
import { TaskForm } from './TaskForm'
import { useAgentState } from './useConversation'

const UNDO_MS = 8000

function columns(timezone: string, remove: (row: AgentConversationListed) => void): ColumnDef<AgentConversationListed>[] {
  return [
    {
      id: 'title',
      header: 'Conversation',
      accessorFn: (row) => `${row.title} ${row.subject?.label ?? ''}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[28rem] whitespace-normal">
          <Link className="font-medium underline-offset-2 hover:underline" to={`/agent/${encodeURIComponent(row.id)}`}>{row.title}</Link>
          {(row.task || askedBy(row.by)) && (
            <span className="ml-2 inline-flex gap-1 align-middle">
              {row.task && <Badge variant="outline" tone={taskTone(row.task)}>{TASK_LABEL[row.task.state]}</Badge>}
              {row.task?.question && <Badge variant="outline" tone="warning">Asks you</Badge>}
              {askedBy(row.by) && <Badge variant="outline">{askedBy(row.by)}</Badge>}
            </span>
          )}
          {row.subject && (
            <Link className="block text-xs text-muted-foreground hover:underline" to={subjectLink(row.subject)}>{subjectSaid(row.subject)}</Link>
          )}
          {moreAttached(row) > 0 && (
            <span className="block text-xs text-muted-foreground">
              {row.subject ? `and ${moreAttached(row)} more attached` : `${moreAttached(row)} attached`}
            </span>
          )}
        </div>
      ),
    },
    {
      id: 'status',
      header: 'State',
      accessorFn: (row) => row.status,
      cell: ({ row }) => (
        <Badge variant="soft" tone={statusTone(row.status, row.outcome)}>
          {row.status === 'idle' && row.outcome ? OUTCOME_LABEL[row.outcome] : STATUS_LABEL[row.status]}
        </Badge>
      ),
    },
    { id: 'requests', header: 'Requests', accessorFn: (row) => row.requests ?? 0, sortable: true },
    { id: 'cost', header: 'Cost', accessorFn: (row) => row.cost_usd ?? 0, sortable: true, cell: ({ row }) => costText(row.cost_usd) },
    {
      id: 'updated',
      header: 'Last change',
      accessorFn: (row) => row.updated_at,
      sortable: true,
      cell: ({ row }) => formatDateTime(row.updated_at, timezone, false),
    },
    {
      id: 'remove',
      header: '',
      accessorFn: (row) => row.id,
      cell: ({ row }) => (
        <Button size="sm" variant="ghost" aria-label={`Remove ${row.title}`} disabled={isUnderWay(row.status)}
          onClick={() => remove(row)}>
          <Trash2 aria-hidden className="size-3.5" />
        </Button>
      ),
    },
  ]
}

export function AgentActivityPage() {
  const { timezone } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const state = useAgentState()
  const list = useQuery({
    queryKey: ['agent-conversations', 'all'],
    queryFn: () => api.agentConversations(),
    enabled: !!state.data?.enabled,
    // While it works, its states change without an event: read again now and then.
    refetchInterval: (query) => (query.state.data?.items.some((item) => isUnderWay(item.status)) ? 5000 : false),
  })
  const pause = useMutation({
    mutationFn: (paused: boolean) => api.pauseAgent(paused),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['agent'] }),
  })
  // Hidden at once, removed once the Undo's time is over: nothing is sent before. ``hidden`` is the removal
  // still waiting: an Undo shows the row again. The toast calls the onDismiss of its last render.
  const [hidden, setHidden] = useState<string | null>(null)
  const [shown, setShown] = useState<AgentConversationListed | null>(null)
  const remove = useMutation({
    mutationFn: (id: string) => api.removeAgentConversation(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['agent-conversations'] }),
  })
  const removeNow = remove.mutate
  const startRemoval = useCallback((row: AgentConversationListed) => {
    // A removal before it, its time cut short.
    if (hidden && hidden !== row.id) removeNow(hidden)
    setHidden(row.id)
    setShown(row)
  }, [hidden, removeNow])
  const rows = useMemo(() => (list.data?.items ?? []).filter((item) => item.id !== hidden), [list.data, hidden])
  const tableColumns = useMemo(() => columns(timezone, startRemoval), [timezone, startRemoval])
  const data = state.data
  const month = data?.month

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI agent"
        description="Claude, from these pages: what it was asked, what it did, what it proposed and what it cost."
        actions={
          <>
            {data?.enabled && (
              <Button size="sm" variant="outline" leftIcon={data.pause ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
                isLoading={pause.isPending} onClick={() => pause.mutate(!data.pause)}>
                {data.pause ? 'Resume' : 'Pause'}
              </Button>
            )}
            <ButtonLink to="/config/agent" size="sm" variant="outline" leftIcon={<Settings className="size-3.5" />}>
              Settings
            </ButtonLink>
          </>
        }
      />
      <QueryState isPending={state.isPending} error={state.error} hasData={!!data}>
        {data && !data.enabled && (
          <Alert tone="neutral">
            <AlertDescription>
              The AI agent is off. Set the Claude API key and switch it on in Configuration › AI agent, then restart the
              admin (System).
            </AlertDescription>
          </Alert>
        )}
        {data?.enabled && (
          <div className="space-y-6">
            {data.broken && (
              <Alert tone="danger" role="alert"><AlertDescription>{data.broken}</AlertDescription></Alert>
            )}
            {data.pause && (
              <Alert tone="warning">
                <AlertDescription>
                  Paused since {formatDateTime(data.pause.since, timezone, false)}: no new request starts; those under way go on to their end.
                </AlertDescription>
              </Alert>
            )}
            <KpiGrid columns={4}>
              <KpiTile label="This month" value={costText(month?.spent_usd)} hint={`of ${costText(month?.budget_usd)}, estimated`}>
                {month && (
                  <Meter ratio={Math.min(1, month.spent_usd / month.budget_usd)} label="This month's budget used"
                    tone={month.spent_usd >= month.budget_usd ? 'negative' : month.spent_usd >= 0.8 * month.budget_usd ? 'warning' : 'neutral'} />
                )}
              </KpiTile>
              <KpiTile label="Working on" value={data.under_way?.length ?? 0} hint={`${data.limits?.running ?? 1} at once at most`} />
              <KpiTile label="Conversations" value={data.conversations ?? 0} hint="kept a year unless set otherwise" />
              <KpiTile label="Claude API key" value={data.key ? 'Set' : data.key === false ? 'Missing' : 'Unknown'}
                hint={data.model_label ?? data.model} />
            </KpiGrid>
            <div className="grid gap-6 xl:grid-cols-2">
              <Panel title="Ask" description="A conversation about anything WATA records. Attach runs, scripts, rules, positions, signals, days or files with the paperclip, or drop a file on the field.">
                <AgentComposer onStarted={(id) => navigate(`/agent/${encodeURIComponent(id)}`)} />
              </Panel>
              <Panel title="New research task" description="A goal it works on alone, request after request, within a budget: it goes on with the page closed.">
                <TaskForm onStarted={(id) => navigate(`/agent/${encodeURIComponent(id)}`)} />
              </Panel>
            </div>
            <QueryState isPending={list.isPending} error={list.error} hasData={!!list.data}>
              <AdvancedDataTable<AgentConversationListed>
                title="Conversations"
                searchPlaceholder="Search a conversation…"
                emptyTitle="No conversation yet"
                emptyDescription="Ask the agent from a run, a script or the field above."
                data={rows}
                columns={tableColumns}
                getRowId={(row) => row.id}
                defaultPageSize={20}
                selectable={false}
              />
            </QueryState>
          </div>
        )}
      </QueryState>
      {shown && (
        <UndoToast
          key={shown.id}
          open
          title="Conversation removed"
          description={shown.title}
          restoredMessage="Kept: nothing was removed"
          duration={UNDO_MS}
          onUndo={() => setHidden(null)}
          // The countdown ran out, or the toast was closed; also called once an Undo has shown its message.
          onDismiss={() => {
            if (hidden === shown.id) removeNow(shown.id)
            setShown(null)
          }}
        />
      )}
    </div>
  )
}
