/** The experiment journal Claude keeps on the server, as the Experiments page reads it. */

import { formatDay, formatNumber, formatPercent } from 'ioiti-ui'
import type { BadgeTone } from 'ioiti-ui'

import { VERDICT_LABEL, VERDICT_TONE } from '@/features/edgeWatch/edgeWatch'
import type {
  EdgeVerdict,
  Experiment,
  ExperimentCriterion,
  ExperimentVerdict,
  ForwardCheckShort,
  ForwardEnd,
  ResearchLockbox,
  TrackRecordGroup,
} from '@/lib/types'

export const VERDICT: Record<ExperimentVerdict, { tone: BadgeTone; label: string }> = {
  kept: { tone: 'success', label: 'Kept' },
  rejected: { tone: 'neutral', label: 'Rejected' },
  inconclusive: { tone: 'warning', label: 'Inconclusive' },
}
export const RUNS_SHOWN = 4

/** Every configuration the family of ideas was tried with: what a sweep's overfitting tests count against it. */
export function familyTrials(experiments: Experiment[]): Map<string, number> {
  const trials = new Map<string, number>()
  for (const experiment of experiments) trials.set(experiment.family, (trials.get(experiment.family) ?? 0) + experiment.trials)
  return trials
}

/** The lock box in words. */
export function lockboxText(box: ResearchLockbox | null): string {
  if (!box) return 'No lock box: every recorded day can be studied.'
  return box.to ? `${formatDay(box.from)} to ${formatDay(box.to)} are kept out.` : `Every day from ${formatDay(box.from)} is kept out.`
}

// ── what a conclusion rests on, and how it held on the days after it (v0.95.0) ──

/** How a kept conclusion's candidate stood against the bar when it was kept. */
export const BAR_SAID: Record<'pass' | 'open' | 'fail', { tone: BadgeTone; label: string }> = {
  pass: { tone: 'success', label: 'Met the bar' },
  open: { tone: 'warning', label: 'Checks left open' },
  fail: { tone: 'danger', label: 'Kept against the bar' },
}

/** Why a forward check ended. */
export const ENDED_SAID: Record<ForwardEnd, string> = {
  horizon: 'past the trades its candidate was drawn for',
  days: '60 days judged',
  broken: 'judged broken',
  gone: 'its candidate is gone',
  stopped: 'stopped',
}

/** The tabs of the Experiments page, each with its address. */
export type ExperimentsTab = 'journal' | 'forward' | 'record'
export const EXPERIMENTS_TABS: { id: ExperimentsTab; label: string }[] = [
  { id: 'journal', label: 'Journal' },
  { id: 'forward', label: 'Forward checks' },
  { id: 'record', label: 'Track record' },
]

export function experimentsTab(value: string | null): ExperimentsTab {
  return EXPERIMENTS_TABS.some((tab) => tab.id === value) ? (value as ExperimentsTab) : 'journal'
}

export function forwardCheckPath(id: string): string {
  return `/backtests/experiments/forward/${id}`
}

const trades = (count: number) => `${formatNumber(count)} trade${count === 1 ? '' : 's'}`

/** How a forward check stands, in a few words: its verdict after so many trades, its result, or why it ended. */
export function forwardSentence(check: Pick<ForwardCheckShort, 'state' | 'why' | 'verdict' | 'trades' | 'cumulative' | 'days_tested'>): string {
  const result = check.cumulative === null ? '' : `, ${formatPercent(check.cumulative, true)}`
  const said = check.verdict ? `${VERDICT_LABEL[check.verdict]} after ${trades(check.trades)}${result}` : 'Nothing judged yet'
  const days = check.days_tested > 0 ? ` on ${check.days_tested} day${check.days_tested === 1 ? '' : 's'}` : ''
  if (check.state === 'ended') return `Ended (${check.why ? ENDED_SAID[check.why] : 'ended'}): ${said}${days}`
  return `${said}${days}`
}

/** The tone of a forward check's badge: its verdict's, grey while nothing is judged. */
export function forwardTone(verdict: EdgeVerdict | null): BadgeTone {
  return verdict ? VERDICT_TONE[verdict] : 'neutral'
}

/** A criterion as it reads: "edge t at least 2.6". */
export function criterionText(criterion: ExperimentCriterion): string {
  // As written: 2.6 stays 2.6, never rounded to a whole number.
  const decimals = Math.min(4, (String(criterion.value).split('.')[1] ?? '').length)
  return `${criterion.figure.replace(/_/g, ' ')} ${criterion.bound === 'at_least' ? 'at least' : 'at most'} ${formatNumber(criterion.value, decimals)}`
}

/** A family's or a maker's record in a sentence: its kept conclusions, and how their forward checks stand. */
export function recordSentence(group: TrackRecordGroup): string {
  const forward = group.forward
  const judged = forward.held + forward.watch + forward.broken
  const parts = [`${group.kept} kept of ${group.experiments} experiment${group.experiments === 1 ? '' : 's'}`]
  if (group.kept_against_bar > 0) parts.push(`${group.kept_against_bar} against the bar`)
  if (judged > 0) {
    parts.push(`later days: ${forward.held} held, ${forward.watch} on watch, ${forward.broken} broken`)
  } else if (forward.too_early > 0) {
    parts.push(`later days: ${forward.too_early} too early to tell`)
  }
  if (group.held_share !== null) parts.push(`${group.held_share} % held`)
  return parts.join('; ')
}

/** What the 404 of a forward check's page offers. */
export const FORWARD_CHECK_NOT_FOUND = {
  title: 'No such forward check',
  description: 'No forward check has this id: the address may be mistyped.',
  suggestions: [
    { label: 'Forward checks', href: '/backtests/experiments?tab=forward', description: 'Every kept conclusion judged on later days' },
    { label: 'Experiments', href: '/backtests/experiments', description: 'The journal of the research' },
  ],
}

/** A verdict's reasons (Edge Watch's, in lower case) as one sentence. */
export function reasonsSaid(reasons: readonly string[]): string {
  const said = reasons.join('; ')
  return said ? `${said[0].toUpperCase()}${said.slice(1)}.` : ''
}
