/** The experiment journal Claude keeps on the server, as the Experiments page reads it. */

import type { BadgeTone } from '@/components/ui/badge'
import { formatDay } from '@/lib/format'
import type { Experiment, ExperimentVerdict, ResearchLockbox } from '@/lib/types'

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
