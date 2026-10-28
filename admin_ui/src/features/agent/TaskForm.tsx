// A research task: a goal the agent works on alone, request after request, within the budget chosen here (each
// part at most the settings' max_task_*) and the month's. It goes on whether the page stays open or not; it is
// followed, and stopped, from its conversation.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, Field, Hint, NumberInput, Textarea } from 'ioiti-ui'
import { FlaskConical } from 'lucide-react'
import { useState } from 'react'

import { api } from '@/lib/api'
import type { AgentChoice } from '@/lib/types'

import { ChoiceMenus } from './ChoiceMenus'
import { askRefusal, choiceOf, taskDefaults } from './model'
import { useAgentState } from './useConversation'

const MAX_GOAL = 8000

export function TaskForm({ onStarted }: { onStarted: (id: string) => void }) {
  const queryClient = useQueryClient()
  const state = useAgentState()
  const limits = state.data?.tasks
  const defaults = taskDefaults(limits)
  const [goal, setGoal] = useState('')
  const [usd, setUsd] = useState<number | null>(defaults.usd)
  const [backtests, setBacktests] = useState<number | null>(defaults.backtests)
  const [hours, setHours] = useState<number | null>(defaults.hours)
  const [chosen, setChosen] = useState<AgentChoice | null>(null)
  const choice = chosen ?? choiceOf(state.data, null)
  const start = useMutation({
    mutationFn: () => api.startAgentTask({
      goal: goal.trim(), budget: { usd: usd ?? 0, backtests: backtests ?? 0, hours: hours ?? 0 },
      model: choice?.model, effort: choice?.effort,
    }),
    onSuccess: (made) => {
      void queryClient.invalidateQueries({ queryKey: ['agent'] })
      void queryClient.invalidateQueries({ queryKey: ['agent-conversations'] })
      onStarted(made.id)
    },
  })
  const refusal = askRefusal(state.data)
  const missing = !goal.trim() ? 'Say what the task is for' : usd === null || backtests === null || hours === null ? 'Give it a budget' : null
  const why = refusal ?? missing

  return (
    <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); if (!why) start.mutate() }}>
      <Field label="Its goal">
        <Textarea value={goal} maxLength={MAX_GOAL} rows={3} onChange={(event) => setGoal(event.target.value)}
          placeholder="e.g. Find whether a slower fast line holds on the recorded days, with stress, and propose it if it does." />
      </Field>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Dollars at most">
          <NumberInput value={usd} onChange={setUsd} min={0.5} max={limits?.usd} step={1} precision={2} prefix="$" />
        </Field>
        <Field label="Backtests at most">
          <NumberInput value={backtests} onChange={setBacktests} min={0} max={limits?.backtests} integer />
        </Field>
        <Field label="Hours at most">
          <NumberInput value={hours} onChange={setHours} min={0.5} max={limits?.hours} step={0.5} suffix="h" />
        </Field>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-1">
          {choice && <ChoiceMenus state={state.data} choice={choice} onChoose={setChosen} />}
        </div>
        <Hint content={why ?? undefined} wrap={!!why}>
          <Button type="submit" size="sm" leftIcon={<FlaskConical className="size-3.5" />} disabled={!!why} isLoading={start.isPending}>
            Start the task
          </Button>
        </Hint>
      </div>
      <p className="text-xs text-muted-foreground">
        It works alone until it says it is done, or its dollars or hours are spent: each request within what is left, the
        month within its budget. What it proposes waits for you.
      </p>
      {start.error && (
        <Alert tone="danger" role="alert"><AlertDescription>{start.error.message}</AlertDescription></Alert>
      )}
    </form>
  )
}
