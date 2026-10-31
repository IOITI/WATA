// A routine made or changed: its name and goal, when it comes back (each trading day once its hours are over, or a
// weekday and time of the trading timezone), its budget (each part at most what a task may be given), its model and
// effort, whether its end is told on Telegram, and whether it is on. Saved at the revision the page read.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, AlertDescription, Button, Field, Hint, Input, NumberInput, Select, SettleModal, StretchSwitch, Textarea } from 'ioiti-ui'
import { useState } from 'react'

import { ApiError, api } from '@/lib/api'
import type { AgentChoice, AgentRoutine, AgentRoutineDraft, AgentState } from '@/lib/types'

import { ChoiceMenus } from './ChoiceMenus'
import { choiceOf } from './model'
import { draftOf, routineDefaults, routineProblem, WEEKDAYS } from './routineModel'

export function RoutineDialog({ open, routine, revision, state, onClose }: {
  open: boolean
  /** The routine changed; none: a new one. */
  routine: AgentRoutine | null
  revision: string
  state: AgentState | undefined
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const limits = state?.tasks
  const [draft, setDraft] = useState<AgentRoutineDraft>(() => (routine ? draftOf(routine) : routineDefaults(limits)))
  const change = (changes: Partial<AgentRoutineDraft>) => setDraft((current) => ({ ...current, ...changes }))
  const fallback = choiceOf(state, null)
  const choice: AgentChoice | null = draft.model && draft.effort
    ? { model: draft.model, effort: draft.effort }
    : fallback
  const save = useMutation({
    mutationFn: () => api.saveAgentRoutine(draft, revision, routine?.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['agent-routines'] })
      onClose()
    },
  })
  const problem = routineProblem(draft)
  const schedule = draft.schedule

  return (
    <SettleModal
      open={open}
      onClose={save.isPending ? () => {} : onClose}
      title={routine ? `Change ${routine.name}` : 'A new routine'}
      description="A research task that comes back by itself, the market closed, within its budget and the month's."
      size="lg"
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={save.isPending}>Cancel</Button>
          <Hint content={problem ?? undefined} wrap={!!problem}>
            <Button size="sm" disabled={!!problem} isLoading={save.isPending} onClick={() => save.mutate()}>
              {routine ? 'Save' : 'Make the routine'}
            </Button>
          </Hint>
        </>
      }
    >
      <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (!problem) save.mutate() }}>
        <Field label="Its name">
          <Input value={draft.name} maxLength={80} onChange={(event) => change({ name: event.target.value })}
            placeholder="e.g. Weekly edge check" />
        </Field>
        <Field label="Its goal">
          <Textarea value={draft.goal} rows={3} maxLength={8000} onChange={(event) => change({ goal: event.target.value })}
            placeholder="e.g. Run the reference runs again with the week's days, and say which rule lost its edge." />
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="When">
            <Select
              value={schedule.every}
              onChange={(event) => change({ schedule: event.target.value === 'trading_day'
                ? { every: 'trading_day' } : { every: 'week', weekday: 5, time: '10:00' } })}
              options={[{ value: 'trading_day', label: 'Each trading day' }, { value: 'week', label: 'Each week' }]}
            />
          </Field>
          {schedule.every === 'week' && (
            <>
              <Field label="On">
                <Select
                  value={String(schedule.weekday)}
                  onChange={(event) => change({ schedule: { ...schedule, weekday: Number(event.target.value) } })}
                  options={WEEKDAYS.map((day, index) => ({ value: String(index), label: day }))}
                />
              </Field>
              <Field label="At (trading timezone)">
                <Input value={schedule.time} maxLength={5} placeholder="HH:MM"
                  onChange={(event) => change({ schedule: { ...schedule, time: event.target.value } })} />
              </Field>
            </>
          )}
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Dollars at most">
            <NumberInput value={draft.budget.usd} onChange={(usd) => change({ budget: { ...draft.budget, usd: usd ?? 0 } })}
              min={0.5} max={limits?.usd} step={1} precision={2} prefix="$" />
          </Field>
          <Field label="Backtests at most">
            <NumberInput value={draft.budget.backtests} integer min={0} max={limits?.backtests}
              onChange={(backtests) => change({ budget: { ...draft.budget, backtests: backtests ?? 0 } })} />
          </Field>
          <Field label="Hours at most">
            <NumberInput value={draft.budget.hours} min={0.5} max={limits?.hours} step={0.5} suffix="h"
              onChange={(hours) => change({ budget: { ...draft.budget, hours: hours ?? 0 } })} />
          </Field>
        </div>
        {choice && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-xs text-muted-foreground">It works with</span>
            <ChoiceMenus state={state} choice={choice} onChoose={(chosen) => change({ model: chosen.model, effort: chosen.effort })} />
          </div>
        )}
        <StretchSwitch label="Its end said on Telegram" checked={draft.telegram} onChange={(telegram) => change({ telegram })}
          description="Its conclusion's first lines, once its task ends." />
        <StretchSwitch label="On" checked={draft.enabled} onChange={(enabled) => change({ enabled })}
          description="Off, it waits: Run now still starts it." />
        {save.error && (
          <Alert tone="danger" role="alert">
            <AlertDescription>
              {save.error instanceof ApiError && save.error.status === 409
                ? 'The routines changed since this page read them: close this, and make the change again.'
                : save.error.message}
            </AlertDescription>
          </Alert>
        )}
      </form>
    </SettleModal>
  )
}
