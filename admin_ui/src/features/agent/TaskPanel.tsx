// A research task on its conversation's page: its goal and budget, what it spent of each, how it ended (the
// agent's conclusion, the runs it kept, what to try next), and its stop.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, Button, formatDateTime, Meter, Panel, useNow } from 'ioiti-ui'
import { Square } from 'lucide-react'

import { useSession } from '@/features/auth/auth-context'
import { RunPeekLink } from '@/features/backtests/RunPeek'
import { api } from '@/lib/api'
import type { AgentConversation, AgentTask } from '@/lib/types'

import { costText, TASK_LABEL, taskShares, taskTone } from './model'
import { conversationKey } from './useConversation'

function Spent({ label, said, share }: { label: string; said: string; share: number }) {
  return (
    <div className="space-y-1">
      <p className="flex justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="tabular-nums text-foreground">{said}</span>
      </p>
      <Meter ratio={share} label={`${label}: ${said}`} tone={share >= 1 ? 'negative' : share >= 0.8 ? 'warning' : 'neutral'} />
    </div>
  )
}

export function TaskPanel({ conversation, task }: { conversation: AgentConversation; task: AgentTask }) {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const now = useNow(30_000)
  const stop = useMutation({
    mutationFn: () => api.stopAgentTask(conversation.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: conversationKey(conversation.id) })
      void queryClient.invalidateQueries({ queryKey: ['agent-conversations'] })
    },
  })
  const shares = taskShares(task, now)
  const conclusion = task.conclusion

  return (
    <Panel
      title="Research task"
      description={task.state === 'working'
        ? `It works alone until it says it is done: no request of it starts after ${formatDateTime(task.deadline, timezone, false)}.`
        : task.ended_at ? `${task.state === 'done' ? 'Done' : 'Ended'} ${formatDateTime(task.ended_at, timezone, false)}.` : undefined}
      action={
        <span className="flex items-center gap-2">
          <Badge variant="soft" tone={taskTone(task)}>{TASK_LABEL[task.state]}</Badge>
          {task.state === 'working' && (
            <Button size="sm" variant="outline" leftIcon={<Square className="size-3.5" />} isLoading={stop.isPending}
              onClick={() => stop.mutate()}>
              Stop the task
            </Button>
          )}
        </span>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Spent label="Dollars" said={`${costText(task.spent_usd)} of ${costText(task.budget.usd)}`} share={shares.usd} />
          <Spent label="Backtests" said={`${task.backtests} of ${task.budget.backtests}`} share={shares.backtests} />
          <Spent label="Time" said={`${task.budget.hours} h at most`} share={shares.time} />
        </div>
        {task.state === 'ended' && task.ended_because && (
          <p className="text-sm text-text-secondary">It ended: {task.ended_because}.</p>
        )}
        {conclusion && (
          <div className="space-y-2 rounded-lg border border-border bg-surface p-3 text-sm">
            <p className="font-medium text-foreground">Its conclusion</p>
            <p className="whitespace-pre-wrap text-text-secondary">{conclusion.conclusion}</p>
            {conclusion.runs_kept.length > 0 && (
              <p className="flex flex-wrap gap-x-2 gap-y-1">
                <span className="text-muted-foreground">Runs kept:</span>
                {conclusion.runs_kept.map((run) => <RunPeekLink key={run} id={run} className="underline">{run}</RunPeekLink>)}
              </p>
            )}
            {conclusion.next_steps && <p className="text-text-secondary"><span className="text-muted-foreground">Next: </span>{conclusion.next_steps}</p>}
          </div>
        )}
        {stop.error && (
          <Alert tone="danger" role="alert"><AlertDescription>{stop.error.message}</AlertDescription></Alert>
        )}
      </div>
    </Panel>
  )
}
