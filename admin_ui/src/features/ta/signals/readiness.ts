// Whether a rule is ready to go live, check by check, from what the Signal rules
// page knows of it: how long it has been in shadow, its signals, its paper trades
// against its reference (Edge Watch), how the engine runs it, its script as a rule (its rule check), the script
// version it pins. Said in the confirmation. The server holds the same checks but the script's version
// (src/admin/ta_readiness.py: a change to one is made in the other): a rule short of
// one goes live only if the operator goes live anyway, which the audit trail
// records with what was missing. The switch stays the operator's. Pure.

import { formatDateTime, formatDay } from 'ioiti-ui'
import type { Check } from 'ioiti-ui'

import { edgeUnmet, mainTrack, verdictLine } from '@/features/edgeWatch/edgeWatch'
import type { EdgeOwner, EdgeWatchList, RuleCheckSummary, TaRule, TaRuleState } from '@/lib/types'

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

/**
 * A rule's Edge Watch as readiness reads it (``edge_of`` of src/admin/routers/ta.py): its entry, null while it has
 * no reference run, undefined while Edge Watch is not read or not running (backtests off).
 */
export function edgeOf(list: EdgeWatchList | undefined, ruleId: string, enabled: boolean): EdgeOwner | null | undefined {
  if (!enabled || !list) return undefined
  return list.rules[ruleId] ?? null
}

/** Its paper trades against its reference (Edge Watch), as the server holds it (``edge_unmet`` of ta_readiness.py). */
export function edgeCheck(edge: EdgeOwner | null | undefined): Check {
  const label = 'Its paper trades, as its reference led to expect'
  if (edge === undefined) {
    return { id: 'edge_watch', label, state: 'open', detail: 'Edge Watch is not read: it needs the backtests.' }
  }
  const unmet = edgeUnmet(edge)
  const track = edge ? mainTrack(edge) : undefined
  if (unmet.length === 0 && track) {
    return { id: 'edge_watch', label, state: 'pass', detail: `${verdictLine(track)}: consistent with its reference, which is no proof of an edge.` }
  }
  const tooFewOnly = track !== undefined && unmet.length === 1 && unmet[0].endsWith('since its reference of 20')
  return {
    id: 'edge_watch',
    label,
    state: tooFewOnly ? 'open' : 'fail',
    detail: `${unmet.map((each) => each[0].toUpperCase() + each.slice(1)).join('. ')}.`,
  }
}

export function liveReadiness(
  rule: TaRule,
  state: TaRuleState | undefined,
  /** Its Edge Watch (``edgeOf``): undefined while not read. */
  edge: EdgeOwner | null | undefined,
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

  checks.push(edgeCheck(edge))

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
 * The checks the server holds before a rule goes live (`unmet_readiness`): a week in shadow and its paper trades
 * as its reference led to expect until they have passed, the others once they failed, the script's version never.
 */
export function holdingBack(checks: Check[]): Check[] {
  return checks.filter((check) =>
    check.id === 'shadow' || check.id === 'edge_watch' ? check.state !== 'pass' : check.id !== 'script' && check.state === 'fail',
  )
}
