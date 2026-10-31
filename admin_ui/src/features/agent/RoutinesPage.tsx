// AI agent › Routines: research tasks that come back by themselves (src/admin/agent_routines.py), each trading day
// once its hours are over or once a week, the market closed, never while their last task still works. Each with its
// last runs, when it next comes back, Run now whatever the hour, on or off, changed, or removed with an Undo.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AdvancedDataTable,
  Alert,
  AlertDescription,
  Badge,
  Button,
  DropdownMenu,
  formatDateTime,
  Hint,
  PageHeader,
  QueryState,
  UndoToast,
} from 'ioiti-ui'
import type { ColumnDef } from 'ioiti-ui'
import { ChevronDown, Pause, Pencil, Play, Plus, Trash2, Zap } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { useSession } from '@/features/auth/auth-context'
import { api } from '@/lib/api'
import type { AgentRoutine, AgentRoutineTemplate } from '@/lib/types'

import { costText } from './model'
import { RoutineDialog } from './RoutineDialog'
import { draftOf, monthCommitted, scheduleSaid } from './routineModel'
import { useAgentState } from './useConversation'

const UNDO_MS = 8000

export function RoutinesPage() {
  const { timezone } = useSession()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const state = useAgentState()
  const list = useQuery({ queryKey: ['agent-routines'], queryFn: api.agentRoutines, enabled: !!state.data?.enabled })
  // Ready-made routines: the LuxAlgo scout while the agent has LuxAlgo's tools.
  const templates = useQuery({ queryKey: ['agent-routine-templates'], queryFn: api.agentRoutineTemplates, enabled: !!state.data?.enabled })
  const [editing, setEditing] = useState<{ routine: AgentRoutine | null; template?: AgentRoutineTemplate } | null>(null)
  // Hidden at once, removed once the Undo's time is over: nothing is sent before.
  const [hidden, setHidden] = useState<AgentRoutine | null>(null)
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['agent-routines'] })
  const revision = list.data?.revision ?? ''
  const run = useMutation({
    mutationFn: (routine: AgentRoutine) => api.runAgentRoutine(routine.id),
    onSuccess: ({ conversation }) => {
      refresh()
      navigate(`/agent/${encodeURIComponent(conversation)}`)
    },
  })
  const toggle = useMutation({
    mutationFn: (routine: AgentRoutine) => api.saveAgentRoutine({ ...draftOf(routine), enabled: !routine.enabled }, revision, routine.id),
    onSettled: refresh,
  })
  const remove = useMutation({
    mutationFn: (routine: AgentRoutine) => api.removeAgentRoutine(routine.id, revision),
    onSettled: refresh,
  })
  const routines = useMemo(() => (list.data?.routines ?? []).filter((routine) => routine.id !== hidden?.id), [list.data, hidden])
  const committed = monthCommitted(routines)
  const budget = state.data?.month?.budget_usd
  const full = !!list.data && list.data.routines.length >= list.data.max

  const columns = useMemo<ColumnDef<AgentRoutine>[]>(() => [
    {
      id: 'name',
      header: 'Routine',
      accessorFn: (row) => `${row.name} ${row.goal}`,
      sortable: true,
      cell: ({ row }) => (
        <div className="min-w-0 max-w-[26rem] whitespace-normal">
          <span className="font-medium">{row.name}</span>
          {!row.enabled && <Badge variant="outline" className="ml-2">Off</Badge>}
          <p className="line-clamp-2 text-xs text-muted-foreground">{row.goal}</p>
        </div>
      ),
    },
    { id: 'when', header: 'When', accessorFn: (row) => scheduleSaid(row.schedule), cell: ({ row }) => scheduleSaid(row.schedule) },
    {
      id: 'budget',
      header: 'Budget',
      accessorFn: (row) => row.budget.usd,
      cell: ({ row }) => `${costText(row.budget.usd)} · ${row.budget.backtests} backtests · ${row.budget.hours} h`,
    },
    {
      id: 'last',
      header: 'Last',
      accessorFn: (row) => row.runs[0]?.at ?? '',
      sortable: true,
      cell: ({ row }) => (row.runs[0]
        ? <Link className="underline underline-offset-2" to={`/agent/${encodeURIComponent(row.runs[0].conversation)}`}>
            {formatDateTime(row.runs[0].at, timezone, false)}
          </Link>
        : <span className="text-muted-foreground">Not yet</span>),
    },
    {
      id: 'next',
      header: 'Next',
      accessorFn: (row) => row.next ?? '',
      cell: ({ row }) => (row.enabled && row.next ? formatDateTime(row.next, timezone, false) : <span className="text-muted-foreground">—</span>),
    },
    {
      id: 'actions',
      header: 'Actions',
      align: 'right',
      cell: ({ row }) => (
        <div className="flex justify-end gap-1">
          <Hint content="Run now: its task starts at once, whatever the hour">
            <Button variant="ghost" size="icon" aria-label={`Run ${row.name} now`} onClick={() => run.mutate(row)}>
              <Zap className="size-4" />
            </Button>
          </Hint>
          <Hint content={row.enabled ? 'Switch it off' : 'Switch it on'}>
            <Button variant="ghost" size="icon" aria-label={`${row.enabled ? 'Switch off' : 'Switch on'} ${row.name}`}
              onClick={() => toggle.mutate(row)}>
              {row.enabled ? <Pause className="size-4" /> : <Play className="size-4" />}
            </Button>
          </Hint>
          <Hint content="Change">
            <Button variant="ghost" size="icon" aria-label={`Change ${row.name}`} onClick={() => setEditing({ routine: row })}>
              <Pencil className="size-4" />
            </Button>
          </Hint>
          <Hint content="Remove">
            <Button variant="ghost" size="icon" aria-label={`Remove ${row.name}`} onClick={() => {
              if (hidden) remove.mutate(hidden)
              setHidden(row)
            }}>
              <Trash2 className="size-4" />
            </Button>
          </Hint>
        </div>
      ),
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [timezone, hidden, revision])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Routines"
        description="Research tasks that come back by themselves, each trading day or each week, the market closed."
        actions={state.data?.enabled && (
          <div className="flex flex-wrap items-center gap-2">
            {!!templates.data?.templates.length && (
              <Hint content={full ? `${list.data?.max} routines at most (max_routines): remove one first` : undefined} wrap={full}>
                <DropdownMenu
                  label="Routines ready to add"
                  trigger={
                    <Button size="sm" variant="outline" disabled={full} rightIcon={<ChevronDown aria-hidden className="size-3.5" />}>
                      From a template
                    </Button>
                  }
                  items={templates.data.templates.map((template) => ({
                    label: template.name,
                    description: `${scheduleSaid(template.schedule)}, ${costText(template.budget.usd)} a turn at most`,
                    onSelect: () => setEditing({ routine: null, template }),
                  }))}
                />
              </Hint>
            )}
            <Hint content={full ? `${list.data?.max} routines at most (max_routines): remove one first` : undefined} wrap={full}>
              <Button size="sm" leftIcon={<Plus className="size-3.5" />} disabled={full} onClick={() => setEditing({ routine: null })}>
                New routine
              </Button>
            </Hint>
          </div>
        )}
      />
      <QueryState isPending={state.isPending} error={state.error} hasData={!!state.data}>
        {state.data && !state.data.enabled ? (
          <Alert tone="neutral">
            <AlertDescription>The AI agent is off: switch it on in Configuration › AI agent, then restart the admin.</AlertDescription>
          </Alert>
        ) : (
          <QueryState isPending={list.isPending} error={list.error} hasData={!!list.data}>
            <div className="space-y-3">
              {routines.length > 0 && (
                <p className="text-sm text-text-secondary">
                  Switched on, they would spend {costText(committed)} a month at most, each turn its whole budget
                  {budget ? <> (the month&apos;s budget is {costText(budget)})</> : null}.
                </p>
              )}
              {(run.error || toggle.error || remove.error) && (
                <Alert tone="danger" role="alert">
                  <AlertDescription>{(run.error ?? toggle.error ?? remove.error)!.message}</AlertDescription>
                </Alert>
              )}
              <AdvancedDataTable<AgentRoutine>
                title="Routines"
                searchPlaceholder="Search a routine…"
                emptyTitle="No routine yet"
                emptyDescription="Make one: a goal the agent works on alone, each trading day or each week."
                data={routines}
                columns={columns}
                getRowId={(row) => row.id}
                defaultPageSize={20}
                selectable={false}
              />
            </div>
          </QueryState>
        )}
      </QueryState>
      {editing && (
        <RoutineDialog key={editing.routine?.id ?? editing.template?.key ?? 'new'} open routine={editing.routine}
          template={editing.template} revision={revision} state={state.data} onClose={() => setEditing(null)} />
      )}
      {hidden && (
        <UndoToast
          key={hidden.id}
          open
          title="Routine removed"
          description={hidden.name}
          restoredMessage="Kept: nothing was removed"
          duration={UNDO_MS}
          onUndo={() => setHidden(null)}
          onDismiss={() => {
            if (hidden) remove.mutate(hidden)
            setHidden(null)
          }}
        />
      )}
    </div>
  )
}
