import { describe, expect, it } from 'vitest'

import { fromTemplate, monthCommitted, routineDefaults, routineProblem, scheduleSaid } from './routineModel'

describe('a routine', () => {
  it('says when it comes back', () => {
    expect(scheduleSaid({ every: 'trading_day' })).toBe('Each trading day, once its hours are over')
    expect(scheduleSaid({ every: 'week', weekday: 5, time: '10:00' })).toBe('Each Saturday at 10:00')
  })

  it('starts from a little of what a task may be given, and says what is missing', () => {
    const draft = routineDefaults({ usd: 2, backtests: 40, hours: 12 })
    expect(draft.budget).toEqual({ usd: 2, backtests: 10, hours: 2 })
    expect(routineProblem(draft)).toBe('Give it a name')
    expect(routineProblem({ ...draft, name: 'Weekly', goal: 'Check', schedule: { every: 'week', weekday: 1, time: '25:00' } }))
      .toBe('Its time is HH:MM')
  })

  it('starts from a template whole, on, with the default model and effort', () => {
    const template = {
      key: 'luxalgo_scout', name: 'LuxAlgo scout', goal: 'Read the newest.', telegram: true,
      schedule: { every: 'week' as const, weekday: 5, time: '10:00' }, budget: { usd: 2, backtests: 2, hours: 1.5 },
    }
    expect(fromTemplate(template)).toEqual({
      name: 'LuxAlgo scout', goal: 'Read the newest.', schedule: template.schedule, budget: template.budget,
      model: null, effort: null, telegram: true, enabled: true,
    })
    expect(routineProblem(fromTemplate(template))).toBeNull()
  })

  it('counts what the routines switched on would spend in a month at most', () => {
    expect(monthCommitted([
      { schedule: { every: 'trading_day' }, budget: { usd: 1, backtests: 0, hours: 1 }, enabled: true },
      { schedule: { every: 'week', weekday: 5, time: '10:00' }, budget: { usd: 2, backtests: 0, hours: 1 }, enabled: true },
      { schedule: { every: 'week', weekday: 5, time: '10:00' }, budget: { usd: 9, backtests: 0, hours: 1 }, enabled: false },
    ])).toBeCloseTo(21 + 8.7)
  })
})
