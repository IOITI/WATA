// The AI agent's routines as the Routines page shows them (src/admin/agent_routines.py): when each comes back,
// what the form starts from, and what they would cost in a month if each spent its whole budget. Pure.

import type { AgentRoutine, AgentRoutineDraft } from '@/lib/types'

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'] as const

// Turns of a routine in a month: the trading days of a month, the weeks of a month.
const TURNS_A_MONTH = { trading_day: 21, week: 4.35 }

/** When a routine comes back, in words. */
export function scheduleSaid(schedule: AgentRoutine['schedule']): string {
  if (schedule.every === 'trading_day') return 'Each trading day, once its hours are over'
  return `Each ${WEEKDAYS[schedule.weekday] ?? '?'} at ${schedule.time}`
}

/** What a new routine starts from: a weekly look on Saturday morning, a little of what a task may be given. */
export function routineDefaults(limits: { usd: number; backtests: number; hours: number } | undefined): AgentRoutineDraft {
  return {
    name: '', goal: '', schedule: { every: 'week', weekday: 5, time: '10:00' },
    budget: { usd: Math.min(3, limits?.usd ?? 3), backtests: Math.min(10, limits?.backtests ?? 10), hours: Math.min(2, limits?.hours ?? 2) },
    model: null, effort: null, telegram: true, enabled: true,
  }
}

/** A routine as its form edits it. */
export function draftOf(routine: AgentRoutine): AgentRoutineDraft {
  const { name, goal, schedule, budget, model, effort, telegram, enabled } = routine
  return { name, goal, schedule, budget, model, effort, telegram, enabled }
}

/** What the routines switched on would spend in a month at most: each turn its whole budget. */
export function monthCommitted(routines: Pick<AgentRoutine, 'schedule' | 'budget' | 'enabled'>[]): number {
  return routines.filter((routine) => routine.enabled)
    .reduce((sum, routine) => sum + routine.budget.usd * TURNS_A_MONTH[routine.schedule.every], 0)
}

/** Why the form cannot be saved yet, or null. */
export function routineProblem(draft: AgentRoutineDraft): string | null {
  if (!draft.name.trim()) return 'Give it a name'
  if (!draft.goal.trim()) return 'Say what it is for'
  if (draft.schedule.every === 'week' && !/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.schedule.time)) return 'Its time is HH:MM'
  return null
}
