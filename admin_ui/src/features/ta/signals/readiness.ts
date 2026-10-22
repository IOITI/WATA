// Whether a rule is ready to go live, check by check, from what the Signal rules
// page knows of it: how long it has been in shadow, its signals, its days traded on
// paper, how the engine runs it, the script version it pins. Said in the
// confirmation; it never stops the switch, which stays the operator's. Pure.

import type { Check } from '@/lib/checks'
import { formatDateTime, formatDay } from '@/lib/format'
import type { BacktestPaper, TaRule, TaRuleState } from '@/lib/types'

import { addDays, dayIn } from '../data/wataBars'
import { updatesOf } from './ruleModel'

/** Weekdays in shadow before going live is advised: a week of the engine's signals. */
export const SHADOW_DAYS = 5

/** The weekdays after ``from`` up to ``to`` included (days as YYYY-MM-DD). */
export function weekdaysBetween(from: string, to: string): number {
  let count = 0
  for (let day = addDays(from, 1); day <= to; day = addDays(day, 1)) {
    const weekday = new Date(`${day}T00:00:00Z`).getUTCDay()
    if (weekday !== 0 && weekday !== 6) count += 1
  }
  return count
}

export function liveReadiness(
  rule: TaRule,
  state: TaRuleState | undefined,
  paper: BacktestPaper['rules'][string] | undefined,
  now: number,
  timeZone: string,
): Check[] {
  const checks: Check[] = []

  const since = dayIn(Date.parse(rule.mode_changed_at), timeZone)
  const days = weekdaysBetween(since, dayIn(now, timeZone))
  checks.push({
    id: 'shadow',
    label: 'A week in shadow',
    state: rule.mode !== 'shadow' ? 'fail' : days >= SHADOW_DAYS ? 'pass' : 'open',
    detail:
      rule.mode !== 'shadow'
        ? 'Not in shadow now: what it would send is not being journaled.'
        : `In shadow since ${formatDay(since)}: ${days} weekday${days === 1 ? '' : 's'} of ${SHADOW_DAYS}.`,
  })

  checks.push({
    id: 'signals',
    label: 'Signals journaled',
    state: state?.last_alert_at ? 'pass' : 'fail',
    detail: state?.last_alert_at
      ? `The last one ${formatDateTime(state.last_alert_at, timeZone, false)}.`
      : 'None yet: what it would send is not known.',
  })

  const results = (paper?.days ?? []).filter((day) => day.day_percent !== null)
  const outside = results.filter((day) => day.outside).length
  const reference = paper?.reference ?? null
  checks.push({
    id: 'paper',
    label: 'Its days on paper, as its backtest said',
    state: results.length === 0 || !reference || reference.low === null ? 'open' : outside > 0 ? 'fail' : 'pass',
    detail:
      results.length === 0
        ? 'No day traded on paper yet: each finished day is, once the market is closed.'
        : !reference
          ? `${results.length} day${results.length === 1 ? '' : 's'} on paper, and no reference run to set them against: make one a run of this rule.`
          : reference.low === null
            ? 'Its reference run has too few days for a range.'
            : outside > 0
              ? `${outside} of ${results.length} day${results.length === 1 ? '' : 's'} on paper fell outside the range of its reference run.`
              : `${results.length} day${results.length === 1 ? '' : 's'} on paper, all within the range of its reference run.`,
  })

  checks.push({
    id: 'engine',
    label: 'Run by the engine without fault',
    state: state?.state === 'running' ? 'pass' : state?.state === 'error' ? 'fail' : 'open',
    detail:
      state?.state === 'running'
        ? `Running${state.last_bar_time ? `, its last bar ${formatDateTime(state.last_bar_time, timeZone, false)}` : ''}.`
        : state?.state === 'error'
          ? `Failing: ${state.detail ?? 'the engine says no more'}.`
          : 'Not seen running yet.',
  })

  const updates = updatesOf(rule)
  checks.push({
    id: 'script',
    label: 'The script’s latest version',
    state: updates.script === null && updates.libraries.length === 0 ? 'pass' : 'open',
    detail:
      updates.script !== null
        ? `Version ${updates.script} of its script is saved: it runs version ${rule.script.version}.`
        : updates.libraries.length > 0
          ? `Newer versions of ${updates.libraries.map((entry) => entry.name).join(', ')} are saved.`
          : `It runs version ${rule.script.version}, the latest.`,
  })
  return checks
}
