// Whether a rule is ready to go live, check by check, from what the Signal rules
// page knows of it: how long it has been in shadow, its signals, its days traded on
// paper, how the engine runs it, its script as a rule (its rule check), the script
// version it pins. Said in the confirmation. The server holds the same checks but the script's version
// (src/admin/ta_readiness.py: a change to one is made in the other): a rule short of
// one goes live only if the operator goes live anyway, which the audit trail
// records with what was missing. The switch stays the operator's. Pure.

import { formatDateTime, formatDay } from 'ioiti-ui'
import type { Check } from 'ioiti-ui'

import type { BacktestPaper, RuleCheckSummary, TaRule, TaRuleState } from '@/lib/types'

import { addDays, dayIn } from '../data/wataBars'
import { updatesOf } from './ruleModel'

/** Weekdays in shadow before a rule goes live: a week of the engine's signals (SHADOW_DAYS of ta_readiness.py). */
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
  /** Its script as a rule (the summary of rule_checks.py's summary_of_rule): undefined while read, null while made. */
  ruleCheck?: RuleCheckSummary | null,
): Check[] {
  const checks: Check[] = []

  // From the later of its going into shadow and the last change of what it runs (run_changed_at).
  const changedRun = rule.run_changed_at ? dayIn(Date.parse(rule.run_changed_at), timeZone) : null
  const shadowFrom = dayIn(Date.parse(rule.mode_changed_at), timeZone)
  const since = changedRun !== null && changedRun > shadowFrom ? changedRun : shadowFrom
  const days = weekdaysBetween(since, dayIn(now, timeZone))
  checks.push({
    id: 'shadow',
    label: 'A week in shadow',
    state: rule.mode !== 'shadow' ? 'fail' : days >= SHADOW_DAYS ? 'pass' : 'open',
    detail:
      rule.mode !== 'shadow'
        ? 'Not in shadow now: what it would send is not being journaled.'
        : since === changedRun
          ? `What it runs changed on ${formatDay(since)}: ${days} weekday${days === 1 ? '' : 's'} in shadow since, of ${SHADOW_DAYS}.`
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

  checks.push(scriptAsRule(ruleCheck))

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

// What keeps a script from being a rule as it is, by its worst finding (CANNOT_BE of ta_readiness.py).
const CANNOT_BE: Record<string, string> = {
  'no-alerts': 'no alert', 'alert-messages': 'no alert that makes a signal', strategy: 'a strategy with no alert',
  ticker: 'a series WATA does not have', 'live-ticker': 'a series WATA reads only in backtests',
  'no-data': 'data WATA does not have', 'fails-on-bars': "it fails on WATA's bars",
  version: 'it does not compile here', 'library-missing': 'a library WATA does not have',
}

/**
 * Its script as a rule, as the server holds it (`script_unmet` of ta_readiness.py): not a script that cannot be
 * one as it is, nor one that repaints, nor one that may and was never tested as the rule runs it.
 */
export function scriptAsRule(check: RuleCheckSummary | null | undefined): Check {
  const label = 'Its script as a rule'
  if (check === undefined) return { id: 'rule_check', label, state: 'open', detail: 'Its rule check is being read.' }
  if (check === null) return { id: 'rule_check', label, state: 'open', detail: 'Its rule check is being made: the server makes it before the switch.' }
  const worst = check.worst?.id ?? ''
  switch (check.verdict) {
    case 'blocked':
    case 'needs_alerts':
      return { id: 'rule_check', label, state: 'fail', detail: `Its script cannot be a rule as it is: ${CANNOT_BE[worst] ?? 'see its rule check'}.` }
    case 'repaints':
      return { id: 'rule_check', label, state: 'fail', detail: 'Its script repaints: its alerts differ live and in a backtest.' }
    case 'may_repaint':
      return {
        id: 'rule_check',
        label,
        state: 'fail',
        detail: check.as_rule
          ? 'Its script may repaint: its run test as this rule runs it had no alert to compare.'
          : 'Its script may repaint, and was never tested on recorded bars as this rule runs it.',
      }
    default:
      return {
        id: 'rule_check',
        label,
        state: 'pass',
        detail: check.as_rule
          ? 'Its script can be a signal rule, tested on recorded bars as this rule runs it.'
          : 'Its script can be a signal rule, as its source reads.',
      }
  }
}

/**
 * The checks the server holds before a rule goes live (`unmet_readiness`): a week in shadow until it has
 * passed, the others once they failed, the script's version never.
 */
export function holdingBack(checks: Check[]): Check[] {
  return checks.filter((check) => (check.id === 'shadow' ? check.state !== 'pass' : check.id !== 'script' && check.state === 'fail'))
}
