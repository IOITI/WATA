// A research task on its conversation's page: its goal and budget, what it spent of each, the question it waits
// for an answer to (its choices, or the operator's own words), how it ended (the agent's conclusion, the runs it
// kept, what to try next), and its stop.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Badge, Button, Field, formatDateTime, Meter, Panel, Textarea, useNow } from 'ioiti-ui'
import { MessageCircleQuestion, Square } from 'lucide-react'
import { useState } from 'react'

import { useSession } from '@/features/auth/auth-context'
import { RunPeekLink } from '@/features/backtests/RunPeek'
import { api } from '@/lib/api'
import type { AgentConversation, AgentQuestion, AgentTask } from '@/lib/types'

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

/** The question the task waits on: a choice to click, or the operator's own words. */
function Question({ conversation, question }: { conversation: AgentConversation; question: AgentQuestion }) {
  const { timezone } = useSession()
  const queryClient = useQueryClient()
  const [own, setOwn] = useState('')
  const answer = useMutation({
    mutationFn: (said: string) => api.answerAgentTask(conversation.id, said),
    onSuccess: () => {
      setOwn('')
      void queryClient.invalidateQueries({ queryKey: conversationKey(conversation.id) })
      void queryClient.invalidateQueries({ queryKey: ['agent-conversations'] })
    },
  })
  return (
    <section aria-label="Its question" className="space-y-3 rounded-lg border border-border bg-surface p-3 text-sm accent:border-(--brand)">
      <p className="flex items-center gap-1.5 font-medium text-foreground">
        <MessageCircleQuestion aria-hidden className="size-4" /> It asks you, and waits
      </p>
      <p className="whitespace-pre-wrap text-foreground">{question.question}</p>
      <p className="text-xs text-text-secondary">{question.why}</p>
      {question.choices.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {question.choices.map((choice) => (
            <Button key={choice} size="sm" variant="outline" disabled={answer.isPending} onClick={() => answer.mutate(choice)}>
              {choice}
            </Button>
          ))}
        </div>
      )}
      <form className="space-y-2" onSubmit={(event) => { event.preventDefault(); if (own.trim()) answer.mutate(own.trim()) }}>
        <Field label={question.choices.length > 0 ? 'Or in your own words' : 'Your answer'}>
          <Textarea value={own} rows={2} maxLength={2000} onChange={(event) => setOwn(event.target.value)} />
        </Field>
        <Button type="submit" size="sm" disabled={!own.trim()} isLoading={answer.isPending}>Answer</Button>
      </form>
      <p className="text-xs text-muted-foreground">
        Asked {formatDateTime(question.asked_at, timezone, false)}. Its hours do not count while it waits; unanswered for
        two days, it ends.
      </p>
      {answer.error && <Alert tone="danger" role="alert"><AlertDescription>{answer.error.message}</AlertDescription></Alert>}
    </section>
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
        {task.state === 'working' && task.question && <Question conversation={conversation} question={task.question} />}
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
